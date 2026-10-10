"""Tuya BLE protocol send mixin: packet building, encryption and sending."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from functools import partial
import logging
import secrets
from struct import pack
from typing import Any

from bleak.exc import BleakDBusError, BleakError
from bleak_retry_connector import (
    BLEAK_BACKOFF_TIME,
    BLEAK_RETRY_EXCEPTIONS,
    BleakNotFoundError,
)
from Crypto.Cipher import AES

from .const import (
    CHARACTERISTIC_WRITE,
    GATT_MTU,
    RESPONSE_WAIT_TIMEOUT,
    TuyaBLECode,
)
from .datapoints import TuyaBLEDataPoint, TuyaBLEDataPoints
from .exceptions import (
    TuyaBLEDataFormatError,
    TuyaBLEDeviceError,
    TuyaBLEError,
    TuyaBLEOutgoingDataLengthError,
)

_LOGGER = logging.getLogger(__name__)

# The v3 datapoint envelope encodes each datapoint as three single-byte fields
# (dp_id, dp_type, value length), which bounds both to this value.
MAX_BYTE = 255

# BLEAK_RETRY_EXCEPTIONS includes AttributeError, which would otherwise make a
# programming error (typo'd attribute, missing Protocol member, None access)
# look like a transient BLE failure and be retried silently. Excluding it keeps
# real bugs visible instead of presenting them as a flaky device.
BLEAK_EXCEPTIONS = (
    *(err for err in BLEAK_RETRY_EXCEPTIONS if err is not AttributeError),
    OSError,
)

BLE_CONNECTION_EXCEPTIONS = (TuyaBLEError, *BLEAK_EXCEPTIONS)


class TuyaBLEProtocolSendMixin:
    """Mixin providing packet building, encryption and sending."""

    # Instance attributes expected by protocol methods (set by TuyaBLEDevice.__init__).
    _protocol_version: int
    _local_key: bytes | None
    _login_key: bytes | None
    _session_key: bytes | None
    _auth_key: bytes | None
    _expected_disconnect: bool
    _is_paired: bool
    _is_bound: bool
    _flags: int
    _device_version: str
    _protocol_version_str: str
    _hardware_version: str
    _input_buffer: bytearray | None
    _input_expected_packet_num: int
    _input_expected_length: int
    _input_reassembly_deadline: float
    _input_expected_responses: dict[int, asyncio.Future[int]]
    _seq_num_lock: asyncio.Lock
    _current_seq_num: int
    _operation_lock: asyncio.Lock
    _client: Any
    _datapoints: TuyaBLEDataPoints
    _callbacks: list[Callable[[list[TuyaBLEDataPoint]], None]]
    _reconnect_task: asyncio.Task[None] | None
    _resend_task: asyncio.Task[None] | None
    _send_response_tasks: set[asyncio.Task[None]]

    @property
    def address(self) -> str:
        """Return the BLE address."""
        return ""

    @property
    def rssi(self) -> int | None:
        """Return the RSSI if connected."""
        return None

    async def _ensure_connected(self) -> None:
        """Ensure BLE connection is established."""

    async def _reconnect(self) -> None:
        """Attempt reconnection."""

    def _disconnected(self, client: Any) -> None:
        """Handle disconnection."""

    def _fire_callbacks(self, datapoints: list[TuyaBLEDataPoint]) -> None:
        """Notify registered callbacks of data point updates."""
        for callback in self._callbacks:
            callback(datapoints)

    async def send_datapoints(self, datapoint_ids: list[int]) -> None:
        """Send new values of datapoints to the device."""
        if self._protocol_version == 3:
            await self._send_datapoints_v3(datapoint_ids)
        else:
            raise TuyaBLEDeviceError(0)

    async def set_multiple_values(
        self, dp_updates: dict[int, bytes | bool | int | str]
    ) -> None:
        """Set multiple datapoint values in a single atomic BLE payload.

        The BLE payload is atomic, the state transition is not: the device may
        reject it. Local values are therefore snapshotted first and restored if
        the send fails, so an entity never keeps reporting a value the device
        did not accept.
        """
        if self._protocol_version != 3:
            raise TuyaBLEDeviceError(0)

        sent_ids: list[int] = []
        previous_values: list[tuple[TuyaBLEDataPoint, bytes | bool | int | str]] = []
        for dp_id, value in dp_updates.items():
            dp = self._datapoints[dp_id]
            if dp is None:
                _LOGGER.warning(
                    "%s: Skipping unknown datapoint id %s in bulk update",
                    self.address,
                    dp_id,
                )
                continue

            previous_values.append((dp, dp.value))
            dp.set_value_no_notify(value)
            sent_ids.append(dp_id)

        if not sent_ids:
            return

        try:
            await self.send_datapoints(sent_ids)
        except BLE_CONNECTION_EXCEPTIONS:
            # Any failure the caller would see as a send failure must also revert
            # the local values, otherwise HA keeps reporting what the device
            # never accepted.
            for dp, previous in previous_values:
                dp.set_value_no_notify(previous)
            _LOGGER.warning(
                "%s: Bulk update of datapoints %s failed, local values reverted",
                self.address,
                sent_ids,
            )
            raise

    async def _send_datapoints_v3(self, datapoint_ids: list[int]) -> None:
        """Serialize and send datapoint updates using the v3 protocol envelope.

        The envelope's per-datapoint fields are single bytes, so the id and the
        serialized value are capped at ``MAX_BYTE`` and validated here, raising a
        ``TuyaBLEError`` subclass rather than a bare ``struct.error``. The total
        payload needs no bound: ``_send_packet`` already fragments it.
        """
        data = bytearray()
        for dp_id in datapoint_ids:
            dp = self._datapoints[dp_id]
            if dp is None:
                raise TuyaBLEDeviceError(0)
            value = dp.get_value()
            _LOGGER.debug(
                "%s: Sending datapoint update, id: %s, type: %s: value: %s",
                self.address,
                dp.dp_id,
                dp.dp_type.name,
                dp.value,
            )
            if not 0 <= dp.dp_id <= MAX_BYTE or len(value) > MAX_BYTE:
                raise TuyaBLEOutgoingDataLengthError(dp_id)
            data += pack(">BBB", dp.dp_id, int(dp.dp_type.value), len(value))
            data += value

        await self._send_packet(TuyaBLECode.FUN_SENDER_DPS, bytes(data))

    @staticmethod
    def _calc_crc16(data: bytes) -> int:
        """Calculate CRC-16/MODBUS over the given bytes."""
        crc = 0xFFFF
        for byte in data:
            crc ^= byte & 255
            for _ in range(8):
                tmp = crc & 1
                crc >>= 1
                if tmp != 0:
                    crc ^= 0xA001
        return crc

    @staticmethod
    def _pack_int(value: int) -> bytearray:
        """Encode an integer as a variable-length big-endian field."""
        curr_byte: int
        result = bytearray()
        while True:
            curr_byte = value & 0x7F
            value >>= 7
            if value != 0:
                curr_byte |= 0x80
            result += pack(">B", curr_byte)
            if value == 0:
                break
        return result

    @staticmethod
    def _unpack_int(data: bytes, start_pos: int) -> tuple[int, int]:
        """Decode a variable-length big-endian integer starting at start_pos."""
        result: int = 0
        offset: int = 0
        while offset < 5:
            pos: int = start_pos + offset
            if pos >= len(data):
                raise TuyaBLEDataFormatError()
            curr_byte: int = data[pos]
            result |= (curr_byte & 0x7F) << (offset * 7)
            offset += 1
            if (curr_byte & 0x80) == 0:
                break
        if offset > 4:
            raise TuyaBLEDataFormatError()
        return (result, start_pos + offset)

    def _build_packets(
        self,
        seq_num: int,
        code: TuyaBLECode,
        data: bytes,
        response_to: int = 0,
    ) -> list[bytes]:
        """Build encrypted BLE packets for the given command."""
        encrypted = self._encrypt_payload(seq_num, code, data, response_to)
        return self._fragment_into_packets(encrypted)

    def _select_encryption_key(self, code: TuyaBLECode) -> tuple[bytes, bytes]:
        """Select the encryption key and security flag for the given code."""
        if code == TuyaBLECode.FUN_SENDER_DEVICE_INFO:
            if self._login_key is None:
                raise TuyaBLEDeviceError(0)
            return self._login_key, b"\x04"
        if self._session_key is None:
            raise TuyaBLEDeviceError(0)
        return self._session_key, b"\x05"

    def _encrypt_payload(
        self,
        seq_num: int,
        code: TuyaBLECode,
        data: bytes,
        response_to: int,
    ) -> bytes:
        """Build, CRC-check, pad, and encrypt the packet payload."""
        key, security_flag = self._select_encryption_key(code)
        iv = secrets.token_bytes(16)

        raw = bytearray()
        raw += pack(">IIHH", seq_num, response_to, code.value, len(data))
        raw += data
        crc = self._calc_crc16(bytes(raw))
        raw += pack(">H", crc)
        while len(raw) % 16 != 0:
            raw += b"\x00"

        cipher = AES.new(key, AES.MODE_CBC, iv)  # noqa: S5542
        encrypted: bytes = security_flag + iv + cipher.encrypt(raw)
        return encrypted

    def _fragment_into_packets(self, encrypted: bytes) -> list[bytes]:
        """Fragment encrypted payload into GATT MTU-sized packets."""
        packets: list[bytes] = []
        pos = 0
        packet_num = 0
        length = len(encrypted)
        while pos < length:
            packet = bytearray()
            packet += self._pack_int(packet_num)
            if packet_num == 0:
                packet += self._pack_int(length)
                packet += pack(">B", self._protocol_version << 4)
            data_part = encrypted[
                pos : pos
                + GATT_MTU
                - len(
                    packet
                )  # fmt: skip
            ]
            packet += data_part
            packets.append(bytes(packet))
            pos += len(data_part)
            packet_num += 1
        return packets

    async def _get_seq_num(self) -> int:
        """Return the next monotonically increasing sequence number."""
        async with self._seq_num_lock:
            result = self._current_seq_num
            self._current_seq_num += 1
        return result

    async def _send_packet(
        self,
        code: TuyaBLECode,
        data: bytes,
        wait_for_response: bool = True,
    ) -> None:
        """Send packet to device and optional read response."""
        if self._expected_disconnect:
            return
        await self._ensure_connected()
        if self._expected_disconnect:  # noqa: S2583
            return
        await self._send_packet_while_connected(code, data, 0, wait_for_response)

    async def _send_response(
        self,
        code: TuyaBLECode,
        data: bytes,
        response_to: int,
    ) -> None:
        """Send response to received packet."""
        if self._client and self._client.is_connected:
            await self._send_packet_while_connected(code, data, response_to, False)

    def _log_unretrieved_exception(
        self, task: asyncio.Task[None], description: str
    ) -> None:
        """Retrieve and log an exception from a finished background task.

        Without this an escaping exception is only reported by asyncio at GC
        time as "Task exception was never retrieved", with no device context.
        """
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            _LOGGER.warning(
                "%s: Unretrieved exception from %s: %s",
                self.address,
                description,
                exc,
                exc_info=exc,
            )

    def _spawn_background_task(
        self, coro: Coroutine[Any, Any, None], description: str
    ) -> asyncio.Task[None]:
        """Create a background task that logs its exception when it finishes."""
        task = asyncio.create_task(coro)
        task.add_done_callback(
            partial(self._log_unretrieved_exception, description=description)
        )
        return task

    def _schedule_reconnect(self) -> None:
        """Ensure exactly one reconnect task is pending.

        A failed GATT write reaches both `_disconnected()` and the
        `_send_packets_locked()` error handler, and each failed `_reconnect`
        reschedules itself, so without this guard the population doubles on
        every event. Skipped once an intentional disconnect is expected.
        """
        if self._expected_disconnect:
            return
        pending = self._reconnect_task
        if (
            pending is not None
            and pending is not asyncio.current_task()
            and not pending.done()
        ):
            return
        self._reconnect_task = self._spawn_background_task(
            self._reconnect(), "reconnect task"
        )

    def _schedule_resend(self, packets: list[bytes]) -> None:
        """Ensure exactly one resend task is pending for these packets."""
        pending = self._resend_task
        if pending is not None and not pending.done():
            return
        self._resend_task = self._spawn_background_task(
            self._resend_packets(packets), "resend task"
        )

    def _track_send_response_task(self, task: asyncio.Task[None]) -> None:
        """Track a send-response task so outstanding tasks can be cancelled."""
        self._send_response_tasks.add(task)
        task.add_done_callback(
            partial(
                self._log_unretrieved_exception,
                description="send-response task",
            )
        )
        task.add_done_callback(self._send_response_tasks.discard)

    async def _send_packet_while_connected(
        self,
        code: TuyaBLECode,
        data: bytes,
        response_to: int,
        wait_for_response: bool,
    ) -> bool:
        """Send packet to device and optional read response."""
        result = True
        future: asyncio.Future[int] | None = None
        seq_num = await self._get_seq_num()
        if wait_for_response:
            future = asyncio.Future()
            self._input_expected_responses[seq_num] = future

        if response_to > 0:
            _LOGGER.debug(
                "%s: Sending packet: #%s %s in response to #%s",
                self.address,
                seq_num,
                code.name,
                response_to,
            )
        else:
            _LOGGER.debug(
                "%s: Sending packet: #%s %s",
                self.address,
                seq_num,
                code.name,
            )
        packets: list[bytes]
        try:
            packets = self._build_packets(seq_num, code, data, response_to)
            await self._int_send_packet_while_connected(packets)
            if future is not None:
                try:
                    await asyncio.wait_for(future, RESPONSE_WAIT_TIMEOUT)
                except TimeoutError:
                    _LOGGER.error(
                        "%s: timeout receiving response to #%s for %s, RSSI: %s",
                        self.address,
                        seq_num,
                        code.name,
                        self.rssi,
                    )
                    result = False
        finally:
            if future is not None:
                self._input_expected_responses.pop(seq_num, None)

        return result

    async def _int_send_packet_while_connected(
        self,
        packets: list[bytes],
    ) -> None:
        """Send packets over GATT, retrying on transient BLE errors."""
        if self._operation_lock.locked():
            _LOGGER.debug(
                "%s: Operation already in progress, "
                "waiting for it to complete; RSSI: %s",
                self.address,
                self.rssi,
            )
        async with self._operation_lock:
            try:
                await self._send_packets_locked(packets)
            except BleakNotFoundError:
                _LOGGER.exception(
                    "%s: device not found, no longer in range, or poor RSSI: %s",
                    self.address,
                    self.rssi,
                    exc_info=True,
                )
                raise
            except BLEAK_EXCEPTIONS:
                _LOGGER.exception(
                    "%s: communication failed",
                    self.address,
                    exc_info=True,
                )
                raise

    async def _resend_packets(self, packets: list[bytes]) -> None:
        """Re-send packets after a transient disconnection."""
        if self._expected_disconnect:
            return
        await self._ensure_connected()
        if self._expected_disconnect:  # noqa: S2583
            return
        await self._int_send_packet_while_connected(packets)

    async def _send_packets_locked(self, packets: list[bytes]) -> None:
        """Send command to device and read response."""
        try:
            await self._int_send_packets_locked(packets)
        except BleakDBusError as ex:
            # Disconnect so we can reset state and try again
            await asyncio.sleep(BLEAK_BACKOFF_TIME)
            _LOGGER.debug(
                "%s: RSSI: %s; Backing off %ss; Disconnecting due to error: %s",
                self.address,
                self.rssi,
                BLEAK_BACKOFF_TIME,
                ex,
            )
            if self._is_paired:
                self._schedule_resend(packets)
            else:
                self._schedule_reconnect()
            raise BleakError from ex
        except BleakError as ex:
            # Disconnect so we can reset state and try again
            _LOGGER.debug(
                "%s: RSSI: %s; Disconnecting due to error: %s",
                self.address,
                self.rssi,
                ex,
            )
            if self._is_paired:
                self._schedule_resend(packets)
            else:
                self._schedule_reconnect()
            raise

    async def _int_send_packets_locked(self, packets: list[bytes]) -> None:
        """Write each packet to the GATT characteristic; raise on failure."""
        for packet in packets:
            if self._client:
                try:
                    await self._client.write_gatt_char(
                        CHARACTERISTIC_WRITE,
                        packet,
                        False,
                    )
                except Exception:
                    _LOGGER.exception(
                        "%s: Error during sending packet",
                        self.address,
                        exc_info=True,
                    )
                    if self._client and self._client.is_connected:
                        self._disconnected(self._client)
                    raise BleakError() from None
            else:
                _LOGGER.error(
                    "%s: Client disconnected during sending packet",
                    self.address,
                    exc_info=True,
                )
                raise BleakError()
