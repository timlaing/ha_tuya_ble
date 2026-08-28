"""Tuya BLE protocol mixin with packet building, sending, and parsing."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import hashlib
import logging
import secrets
from struct import pack, unpack
import time
from typing import Any, Protocol

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
    TuyaBLEDataPointType,
)
from .datapoints import TuyaBLEDataPoint, TuyaBLEDataPoints
from .exceptions import (
    TuyaBLEDataCRCError,
    TuyaBLEDataFormatError,
    TuyaBLEDataLengthError,
    TuyaBLEDeviceError,
    TuyaBLEError,
)

_LOGGER = logging.getLogger(__name__)

BLEAK_EXCEPTIONS = (*BLEAK_RETRY_EXCEPTIONS, OSError)

BLE_CONNECTION_EXCEPTIONS = (TuyaBLEError, *BLEAK_EXCEPTIONS)


class TuyaBLEProtocol(Protocol):
    """Mixin providing Tuya BLE protocol methods for TuyaBLEDevice."""

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
    _input_expected_responses: dict[int, asyncio.Future[int]]
    _seq_num_lock: asyncio.Lock
    _current_seq_num: int
    _operation_lock: asyncio.Lock
    _client: Any
    _datapoints: TuyaBLEDataPoints
    _callbacks: list[Callable[[list[TuyaBLEDataPoint]], None]]

    @property
    def address(self) -> str:
        """Return the BLE address."""

    @property
    def rssi(self) -> int | None:
        """Return the RSSI if connected."""

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

    async def set_multiple_values(  # pylint: disable=protected-access
        self, dp_updates: dict[int, bytes | bool | int | str]
    ) -> None:
        """Set multiple datapoint values in a single atomic BLE payload."""
        sent_ids: list[int] = []
        for dp_id, value in dp_updates.items():
            dp = self._datapoints[dp_id]
            if dp is None:
                continue

            if dp.dp_type in (
                TuyaBLEDataPointType.DT_RAW,
                TuyaBLEDataPointType.DT_BITMAP,
            ):
                dp._value = bytes(value)  # type: ignore[arg-type]
            elif dp.dp_type == TuyaBLEDataPointType.DT_BOOL:
                dp._value = bool(value)
            elif dp.dp_type in (
                TuyaBLEDataPointType.DT_VALUE,
                TuyaBLEDataPointType.DT_ENUM,
            ):
                dp._value = int(value)
            elif dp.dp_type == TuyaBLEDataPointType.DT_STRING:
                dp._value = str(value)
            dp._changed_by_device = False
            sent_ids.append(dp_id)

        if sent_ids:
            await self.send_datapoints(sent_ids)

    async def _send_datapoints_v3(self, datapoint_ids: list[int]) -> None:
        """Serialize and send datapoint updates using the v3 protocol envelope."""
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
        packets: list[bytes] = self._build_packets(seq_num, code, data, response_to)
        await self._int_send_packet_while_connected(packets)
        if future:
            try:
                await asyncio.wait_for(future, RESPONSE_WAIT_TIMEOUT)
            except TimeoutError:
                _LOGGER.error(
                    "%s: timeout receiving response, RSSI: %s",
                    self.address,
                    self.rssi,
                )
                result = False
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
                asyncio.create_task(self._resend_packets(packets))
            else:
                asyncio.create_task(self._reconnect())
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
                asyncio.create_task(self._resend_packets(packets))
            else:
                asyncio.create_task(self._reconnect())
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

    def _get_key(self, security_flag: int) -> bytes:
        """Return the encryption key for the given security flag."""
        if security_flag == 1:
            if self._auth_key is None:
                raise TuyaBLEDeviceError(0)
            return self._auth_key
        if security_flag == 4:
            if self._login_key is None:
                raise TuyaBLEDeviceError(0)
            return self._login_key
        if security_flag == 5:
            if self._session_key is None:
                raise TuyaBLEDeviceError(0)
            return self._session_key
        raise TuyaBLEDataFormatError()

    def _parse_timestamp(self, data: bytes, start_pos: int) -> tuple[float, int]:
        """Decode a timestamp field (type 0: ms-string, type 1: 4-byte big-endian)."""
        timestamp: float
        pos = start_pos
        if pos >= len(data):
            raise TuyaBLEDataLengthError()
        time_type = data[pos]
        pos += 1
        end_pos = pos
        match time_type:
            case 0:
                end_pos += 13
                if end_pos > len(data):
                    raise TuyaBLEDataLengthError()
                timestamp = int(data[pos:end_pos].decode()) / 1000
            case 1:
                end_pos += 4
                if end_pos > len(data):
                    raise TuyaBLEDataLengthError()
                timestamp = int.from_bytes(data[pos:end_pos], "big") * 1.0
            case _:
                raise TuyaBLEDataFormatError()

        _LOGGER.debug(
            "%s: Received timestamp: %s",
            self.address,
            time.ctime(timestamp),
        )
        return (timestamp, end_pos)

    def _parse_datapoints_v3(
        self, timestamp: float, flags: int, data: bytes, start_pos: int
    ) -> None:
        """Parse a v3 datapoint payload and fire callbacks."""
        datapoints: list[TuyaBLEDataPoint] = []

        pos = start_pos
        while len(data) - pos >= 4:
            dp_id: int = data[pos]
            pos += 1
            raw_type: int = data[pos]
            if raw_type > TuyaBLEDataPointType.DT_BITMAP.value:
                raise TuyaBLEDataFormatError()
            dp_type: TuyaBLEDataPointType = TuyaBLEDataPointType(raw_type)
            pos += 1
            data_len: int = data[pos]
            pos += 1
            next_pos = pos + data_len
            if next_pos > len(data):
                raise TuyaBLEDataLengthError()
            raw_value = data[pos:next_pos]
            value: bytes | bool | int | str
            match dp_type:
                case TuyaBLEDataPointType.DT_RAW | TuyaBLEDataPointType.DT_BITMAP:
                    value = raw_value
                case TuyaBLEDataPointType.DT_BOOL:
                    value = int.from_bytes(raw_value, "big") != 0
                case TuyaBLEDataPointType.DT_VALUE | TuyaBLEDataPointType.DT_ENUM:
                    value = int.from_bytes(raw_value, "big", signed=True)
                case TuyaBLEDataPointType.DT_STRING:
                    value = raw_value.decode()

            _LOGGER.debug(
                "%s: Received datapoint update, id: %s, type: %s: value: %s",
                self.address,
                dp_id,
                dp_type.name,
                value,
            )
            self._datapoints.update_from_device(dp_id, timestamp, flags, dp_type, value)
            dp = self._datapoints[dp_id]
            if dp is None:
                raise TuyaBLEDeviceError(0)
            datapoints.append(dp)
            pos = next_pos

        self._fire_callbacks(datapoints)

    def _handle_command_or_response(
        self, seq_num: int, response_to: int, code: TuyaBLECode, data: bytes
    ) -> None:
        """Dispatch an incoming command or response to its handler."""
        result = self._dispatch_command(code, seq_num, data)
        self._resolve_expected_response(response_to, result)

    def _dispatch_command(self, code: TuyaBLECode, seq_num: int, data: bytes) -> int:
        """Dispatch an incoming command to its handler. Returns result code."""
        result = 0
        match code:
            case TuyaBLECode.FUN_SENDER_DEVICE_INFO:
                self._handle_device_info_response(data)
            case TuyaBLECode.FUN_SENDER_PAIR:
                result = self._handle_pair_response(data)
            case TuyaBLECode.FUN_SENDER_DEVICE_STATUS:
                result = self._handle_device_status_response(data)
            case TuyaBLECode.FUN_RECEIVE_TIME1_REQ:
                self._handle_time1_request(seq_num)
            case TuyaBLECode.FUN_RECEIVE_TIME2_REQ:
                self._handle_time2_request(seq_num)
            case TuyaBLECode.FUN_RECEIVE_DP:
                self._handle_receive_dp(seq_num, data)
            case TuyaBLECode.FUN_RECEIVE_SIGN_DP:
                self._handle_receive_sign_dp(seq_num, data)
            case TuyaBLECode.FUN_RECEIVE_TIME_DP:
                self._handle_receive_time_dp(seq_num, data)
            case TuyaBLECode.FUN_RECEIVE_SIGN_TIME_DP:
                self._handle_receive_sign_time_dp(seq_num, data)
        return result

    def _handle_device_info_response(self, data: bytes) -> None:
        """Handle FUN_SENDER_DEVICE_INFO response: extract version and session key."""
        if len(data) < 46:
            raise TuyaBLEDataLengthError()

        self._device_version = f"{data[0]}.{data[1]}"
        self._protocol_version_str = f"{data[2]}.{data[3]}"
        self._hardware_version = f"{data[12]}.{data[13]}"

        self._protocol_version = data[2]
        self._flags = data[4]
        self._is_bound = data[5] != 0

        srand = data[6:12]
        if self._local_key is None:
            raise TuyaBLEDeviceError(0)
        self._session_key = hashlib.md5(self._local_key + srand).digest()  # noqa: S4790
        self._auth_key = data[14:46]

    def _handle_pair_response(self, data: bytes) -> int:
        """Handle FUN_SENDER_PAIR response: parse pairing status."""
        if len(data) != 1:
            raise TuyaBLEDataLengthError()
        result = data[0]
        if result == 2:
            _LOGGER.debug(
                "%s: Device is already paired",
                self.address,
            )
            result = 0
        self._is_paired = result == 0
        return result

    def _handle_device_status_response(self, data: bytes) -> int:
        """Handle FUN_SENDER_DEVICE_STATUS response: parse status code."""
        if len(data) != 1:
            raise TuyaBLEDataLengthError()
        return data[0]

    def _handle_time1_request(self, seq_num: int) -> None:
        """Handle FUN_RECEIVE_TIME1_REQ: respond with millisecond timestamp."""
        timestamp = int(time.time_ns() / 1000000)
        timezone = -int(time.timezone / 36)
        data = str(timestamp).encode() + pack(">h", timezone)
        asyncio.create_task(
            self._send_response(TuyaBLECode.FUN_RECEIVE_TIME1_REQ, data, seq_num)
        )

    def _handle_time2_request(self, seq_num: int) -> None:
        """Handle FUN_RECEIVE_TIME2_REQ: respond with structured time fields."""
        time_str: time.struct_time = time.localtime()
        timezone = -int(time.timezone / 36)
        data = pack(
            ">BBBBBBBh",
            time_str.tm_year % 100,
            time_str.tm_mon,
            time_str.tm_mday,
            time_str.tm_hour,
            time_str.tm_min,
            time_str.tm_sec,
            time_str.tm_wday,
            timezone,
        )
        asyncio.create_task(
            self._send_response(TuyaBLECode.FUN_RECEIVE_TIME2_REQ, data, seq_num)
        )

    def _handle_receive_dp(self, seq_num: int, data: bytes) -> None:
        """Handle FUN_RECEIVE_DP: parse datapoints and send ack."""
        self._parse_datapoints_v3(time.time(), 0, data, 0)
        asyncio.create_task(
            self._send_response(TuyaBLECode.FUN_RECEIVE_DP, bytes(0), seq_num)
        )

    def _handle_receive_sign_dp(self, seq_num: int, data: bytes) -> None:
        """Handle FUN_RECEIVE_SIGN_DP: parse signed datapoints and send ack."""
        dp_seq_num = int.from_bytes(data[:2], "big")
        flags = data[2]
        self._parse_datapoints_v3(time.time(), flags, data, 2)
        response = pack(">HBB", dp_seq_num, flags, 0)
        asyncio.create_task(
            self._send_response(TuyaBLECode.FUN_RECEIVE_SIGN_DP, response, seq_num)
        )

    def _handle_receive_time_dp(self, seq_num: int, data: bytes) -> None:
        """Handle FUN_RECEIVE_TIME_DP: parse timestamped datapoints and send ack."""
        ts: float
        dp_pos: int
        ts, dp_pos = self._parse_timestamp(data, 0)
        self._parse_datapoints_v3(ts, 0, data, dp_pos)
        asyncio.create_task(
            self._send_response(TuyaBLECode.FUN_RECEIVE_TIME_DP, bytes(0), seq_num)
        )

    def _handle_receive_sign_time_dp(self, seq_num: int, data: bytes) -> None:
        """Handle FUN_RECEIVE_SIGN_TIME_DP: parse signed timestamped datapoints."""
        dp_seq_num = int.from_bytes(data[:2], "big")
        flags = data[2]
        _ts, dp_pos = self._parse_timestamp(data, 3)
        self._parse_datapoints_v3(time.time(), flags, data, dp_pos)
        response = pack(">HBB", dp_seq_num, flags, 0)
        asyncio.create_task(
            self._send_response(TuyaBLECode.FUN_RECEIVE_SIGN_TIME_DP, response, seq_num)
        )

    def _resolve_expected_response(self, response_to: int, result: int) -> None:
        """Resolve or reject a pending response future."""
        if response_to == 0:
            return
        future = self._input_expected_responses.pop(response_to, None)
        if not future:
            return
        _LOGGER.debug(
            "%s: Received expected response to #%s, result: %s",
            self.address,
            response_to,
            result,
        )
        if result == 0:
            future.set_result(result)
        else:
            future.set_exception(TuyaBLEDeviceError(result))

    def _clean_input(self) -> None:
        """Reset the input buffer and expected packet counter."""
        self._input_buffer = None
        self._input_expected_packet_num = 0
        self._input_expected_length = 0

    def _parse_input(self) -> None:
        """Decrypt and process the buffered input packet."""
        raw = self._decrypt_input()
        result = self._validate_and_parse_packet(raw)
        if result is None:
            return
        seq_num, response_to, code, data = result
        if response_to != 0:
            _LOGGER.debug(
                "%s: Received: #%s %s, response to #%s",
                self.address,
                seq_num,
                code.name,
                response_to,
            )
        else:
            _LOGGER.debug(
                "%s: Received: #%s %s",
                self.address,
                seq_num,
                code.name,
            )
        self._handle_command_or_response(seq_num, response_to, code, data)

    def _decrypt_input(self) -> bytes:
        """Decrypt the input buffer and return raw bytes."""
        if self._input_buffer is None:
            raise TuyaBLEDataFormatError()
        security_flag = self._input_buffer[0]
        key = self._get_key(security_flag)
        iv = self._input_buffer[1:17]
        encrypted = self._input_buffer[17:]
        self._clean_input()
        cipher = AES.new(key, AES.MODE_CBC, iv)  # noqa: S5542
        raw: bytes = cipher.decrypt(encrypted)
        return raw

    def _validate_and_parse_packet(
        self, raw: bytes
    ) -> tuple[int, int, TuyaBLECode, bytes] | None:
        """Validate a decrypted packet: check length, CRC, and extract fields."""
        seq_num, response_to, _code, length = unpack(">IIHH", raw[:12])

        data_end_pos = length + 12
        raw_length = len(raw)
        if raw_length < data_end_pos:
            raise TuyaBLEDataLengthError()
        if raw_length > data_end_pos:
            calc_crc = self._calc_crc16(raw[:data_end_pos])
            (data_crc,) = unpack(
                ">H",
                raw[data_end_pos : data_end_pos + 2],  # fmt: skip
            )
            if calc_crc != data_crc:
                raise TuyaBLEDataCRCError()
        data = raw[12:data_end_pos]

        try:
            code = TuyaBLECode(_code)
        except ValueError:
            _LOGGER.debug(
                "%s: Received unknown message: #%s %x, response to #%s, data %s",
                self.address,
                seq_num,
                _code,
                response_to,
                data.hex(),
            )
            return None
        return seq_num, response_to, code, data

    def _notification_handler(self, _sender: Any, data: bytearray) -> None:
        """Accumulate fragmented BLE notification packets and parse when complete."""
        _LOGGER.debug("%s: Packet received: %s", self.address, data.hex())

        pos: int = 0
        packet_num: int

        packet_num, pos = self._unpack_int(bytes(data), pos)

        if packet_num < self._input_expected_packet_num:
            _LOGGER.error(
                "%s: Unexpected packet (number %s) in notifications, expected %s",
                self.address,
                packet_num,
                self._input_expected_packet_num,
            )
            self._clean_input()

        if packet_num == self._input_expected_packet_num:
            if packet_num == 0:
                self._input_buffer = bytearray()
                self._input_expected_length, pos = self._unpack_int(bytes(data), pos)
                pos += 1
            if self._input_buffer is None:
                _LOGGER.error("%s: Buffer not initialized", self.address)
                return
            self._input_buffer += data[pos:]
            self._input_expected_packet_num += 1
        else:
            _LOGGER.error(
                "%s: Missing packet (number %s) in notifications, received %s",
                self.address,
                self._input_expected_packet_num,
                packet_num,
            )
            self._clean_input()
            return

        if len(self._input_buffer) > self._input_expected_length:
            _LOGGER.error(
                "%s: Unexpected length of data in notifications, "
                "received %s expected %s",
                self.address,
                len(self._input_buffer),
                self._input_expected_length,
            )
            self._clean_input()
            return

        if len(self._input_buffer) == self._input_expected_length:
            self._parse_input()
