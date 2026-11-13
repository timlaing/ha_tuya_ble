"""Tuya BLE device protocol implementation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
import hashlib
import json
import logging
from struct import pack
import time
from typing import Any

from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from bleak_retry_connector import (
    BLEAK_BACKOFF_TIME,
    BleakClientWithServiceCache,
    BleakNotFoundError,
    establish_connection,
)

from .advertisement import decode_fd50_identity_record, decode_tuya_ble_advertisement
from .const import (
    CHARACTERISTIC_NOTIFY,
    CHARACTERISTIC_NOTIFY_V4,
    CHARACTERISTIC_WRITE,
    CHARACTERISTIC_WRITE_V4,
    GATT_MTU,
    INPUT_REASSEMBLY_TIMEOUT,
    MANUFACTURER_DATA_ID,
    PAIRING_REQUEST_LENGTH,
    PROTOCOL_VERSION_V4,
    SERVICE_UUID_V4,
    DPType,
    TuyaBLECode,
    TuyaBLEDataPointType,
)
from .datapoints import TuyaBLEDataPoint, TuyaBLEDataPoints
from .exceptions import TuyaBLEDeviceError
from .manager import AbstractTuyaBLEDeviceManager, TuyaBLEDeviceCredentials
from .protocol_mixin import (
    BLE_CONNECTION_EXCEPTIONS,
    BLEAK_EXCEPTIONS,
    TuyaBLEProtocol,
)

_LOGGER = logging.getLogger(__name__)
_FINGERBOT_TOUCH_DISCONNECT_TIMEOUT = 5.0

global_connect_lock = asyncio.Lock()


@dataclass
class TuyaBLEDeviceFunction:
    """Models a code, DP and values for a device data point."""

    code: str
    dp_id: int
    type: DPType
    values: str | dict[str, Any] | list[Any] | None = field(default=None)

    def __setattr__(
        self, name: str, value: str | dict[str, Any] | list[Any] | None
    ) -> None:
        if name == "values" and isinstance(value, str):
            # Only containers are unwrapped: a scalar JSON document stays a str,
            # so an int or bool from the cloud can't reach DPType(...) lookups
            # that expect str | dict | list | None.
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                pass
            else:
                if isinstance(parsed, (dict, list)):
                    value = parsed
        super().__setattr__(name, value)


class TuyaBLEDevice(TuyaBLEProtocol):
    """Represents a Tuya BLE device and manages its BLE connection."""

    def __init__(
        self,
        device_manager: AbstractTuyaBLEDeviceManager,
        ble_device: BLEDevice,
        advertisement_data: AdvertisementData | None = None,
    ) -> None:
        """Initialize device state, connection locks, and credential buffers."""
        self.device_manager = device_manager
        self._device_info: TuyaBLEDeviceCredentials | None = None
        self._ble_device = ble_device
        self._advertisement_data = advertisement_data
        self._operation_lock = asyncio.Lock()
        self._connect_lock = asyncio.Lock()
        self._client: BleakClientWithServiceCache | None = None
        self._notify_char = CHARACTERISTIC_NOTIFY
        self._write_char = CHARACTERISTIC_WRITE
        self._expected_disconnect = False
        self._connected_callbacks: list[Callable[[], None]] = []
        self._callbacks: list[Callable[[list[TuyaBLEDataPoint]], None]] = []
        self._disconnected_callbacks: list[Callable[[], None]] = []
        self._current_seq_num = 1
        self._seq_num_lock = asyncio.Lock()

        self._is_bound = False
        self._flags = 0
        self._protocol_version = 2

        self._device_version: str = ""
        self._protocol_version_str: str = ""
        self._hardware_version: str = ""

        self._auth_key: bytes | None = None
        self._local_key: bytes | None = None
        self._login_key: bytes | None = None
        self._session_key: bytes | None = None

        self._is_paired = False

        self._input_buffer: bytearray | None = None
        self._input_expected_packet_num = 0
        self._input_expected_length = 0
        self._input_reassembly_deadline = 0.0
        self._input_expected_responses: dict[int, asyncio.Future[int]] = {}
        self._uuid: str = ""

        self._datapoints = TuyaBLEDataPoints(self)
        self._function: dict[str, TuyaBLEDeviceFunction] = {}
        self._status_range: dict[str, TuyaBLEDeviceFunction] = {}

        self._reconnect_task: asyncio.Task[None] | None = None
        self._resend_task: asyncio.Task[None] | None = None
        self._send_response_tasks: set[asyncio.Task[None]] = set()

        self._awaiting_fd50_identity = False
        self._discarded_client: BleakClientWithServiceCache | None = None
        self._identity_connect_task: asyncio.Task[None] | None = None
        self._advertisement_provider: (
            Callable[[], tuple[BLEDevice, AdvertisementData] | None] | None
        ) = None

    def set_advertisement_provider(
        self, provider: Callable[[], tuple[BLEDevice, AdvertisementData] | None]
    ) -> None:
        """Set where to read the newest stored advertisement before connecting.

        Home Assistant does not pass every advertisement to the callback: it
        skips unchanged packets, and while another scanner is the preferred
        source it only stores a connectable packet.
        """
        self._advertisement_provider = provider

    def set_ble_device_and_advertisement_data(
        self, ble_device: BLEDevice, advertisement_data: AdvertisementData
    ) -> None:
        """Update the underlying BLE device and advertisement data after a new scan.

        While a connection waits for an FD50 identity, each new advertisement
        is decoded, and a usable identity starts it. Keep the wait armed until
        the handshake succeeds so later advertisements can retry a failed attempt.
        """
        self._ble_device = ble_device
        self._advertisement_data = advertisement_data
        if self._awaiting_fd50_identity and self._decode_advertisement_data():
            _LOGGER.debug("%s: FD50 identity received, connecting", self.address)
            self._start_identity_connect()

    async def initialize(self) -> None:
        """Initialize the device by loading credentials and decoding advertisements."""
        _LOGGER.debug("%s: Initializing", self.address)
        if await self._update_device_info():
            self._decode_advertisement_data()

    def initialize_with_credentials(
        self, credentials: TuyaBLEDeviceCredentials
    ) -> None:
        """Initialize the device with pre-built credentials (no cloud needed)."""
        _LOGGER.debug("%s: Initializing with stored credentials", self.address)
        self._device_info = credentials
        local_key = credentials.local_key.encode()
        if len(local_key) < 6:
            raise TuyaBLEDeviceError(0)
        self._local_key = local_key[:6]
        self._login_key = hashlib.md5(self._local_key, usedforsecurity=False).digest()
        self.append_functions(
            credentials.functions if credentials.functions else [],
            credentials.status_range if credentials.status_range else [],
        )
        self._decode_advertisement_data()

    def _build_pairing_request(self) -> bytes:
        result = bytearray()
        if self._device_info is None or self._local_key is None:
            raise TuyaBLEDeviceError(0)

        result += self._device_info.uuid.encode()
        result += self._local_key
        result += self._device_info.device_id.encode()
        if len(result) > PAIRING_REQUEST_LENGTH:
            _LOGGER.error(
                "%s: Pairing request of %d bytes exceeds the %d byte frame",
                self.address,
                len(result),
                PAIRING_REQUEST_LENGTH,
            )
            raise TuyaBLEDeviceError(0)

        return bytes(result).ljust(PAIRING_REQUEST_LENGTH, b"\x00")

    async def pair(self) -> None:
        """Send pairing request."""
        await self._send_packet(
            TuyaBLECode.FUN_SENDER_PAIR, self._build_pairing_request()
        )

    async def update(self) -> None:
        """Request the current device status."""
        _LOGGER.debug("%s: Updating", self.address)
        await self._send_packet(TuyaBLECode.FUN_SENDER_DEVICE_STATUS, b"")

    async def _update_device_info(self) -> bool:
        if self._device_info is None:
            if self.device_manager:
                self._device_info = await self.device_manager.get_device_credentials(
                    self._ble_device.address
                )
            if self._device_info:
                local_key = self._device_info.local_key.encode()
                if len(local_key) < 6:
                    raise TuyaBLEDeviceError(0)
                self._local_key = local_key[:6]
                self._login_key = hashlib.md5(
                    self._local_key, usedforsecurity=False
                ).digest()
                self.append_functions(
                    self._device_info.functions if self._device_info.functions else [],
                    self._device_info.status_range
                    if self._device_info.status_range
                    else [],
                )

        return self._device_info is not None

    def _decode_advertisement_data(self) -> bool:
        """Decode the advertisement data from the BLE device."""
        if not self._advertisement_data:
            return False

        if (
            SERVICE_UUID_V4 not in self._advertisement_data.service_data
            and (
                manufacturer_data := self._advertisement_data.manufacturer_data.get(
                    MANUFACTURER_DATA_ID
                )
            )
            and len(manufacturer_data) > 6
            and manufacturer_data[1] in (2, 3)
        ):
            self._is_bound = (manufacturer_data[0] & 0x80) != 0
            self._protocol_version = manufacturer_data[1]

        if advert := decode_tuya_ble_advertisement(self._advertisement_data):
            self._uuid = advert.uuid
            self._is_bound = advert.is_bound
            self._protocol_version = advert.protocol_version
            return True
        # The encrypted identity is only in the scan response, which a passive
        # scanner never receives. A device with stored credentials already has
        # its identity, so the FD50 record in the advertisement is enough to
        # choose the handshake.
        if self._device_info is not None and (
            record := decode_fd50_identity_record(self._advertisement_data)
        ):
            self._is_bound = record.is_bound
            self._protocol_version = record.protocol_version
            return True
        return False

    @property
    def address(self) -> str:
        """Return the Bluetooth MAC address of the device."""
        return self._ble_device.address

    @property
    def name(self) -> str:
        """Get the name of the device."""
        if self._device_info:
            return self._device_info.device_name or self._ble_device.address
        return self._ble_device.name or self._ble_device.address

    @property
    def cloud_name(self) -> str:
        """Get the cloud-configured device name, if any."""
        if self._device_info:
            return self._device_info.device_name or ""
        return ""

    @property
    def advertised_name(self) -> str:
        """Get the Bluetooth-advertised device name, if any."""
        return self._ble_device.name or ""

    @property
    def rssi(self) -> int | None:
        """Return the latest RSSI signal strength from advertisement data, or None."""
        if self._advertisement_data:
            return self._advertisement_data.rssi
        return None

    @property
    def uuid(self) -> str:
        """Return the device UUID."""
        if self._device_info is not None:
            return self._device_info.uuid
        return ""

    @property
    def local_key(self) -> str:
        """Return the device local key."""
        if self._device_info is not None:
            return self._device_info.local_key
        return ""

    @property
    def category(self) -> str:
        """Return the device category."""
        if self._device_info is not None:
            return self._device_info.category
        return ""

    @property
    def device_id(self) -> str:
        """Return the device ID."""
        if self._device_info is not None:
            return self._device_info.device_id
        return ""

    @property
    def product_id(self) -> str:
        """Return the product ID."""
        if self._device_info is not None:
            return self._device_info.product_id
        return ""

    @property
    def product_model(self) -> str:
        """Return the product model."""
        if self._device_info is not None:
            return self._device_info.product_model or ""
        return ""

    @property
    def product_name(self) -> str:
        """Return the product name."""
        if self._device_info is not None:
            return self._device_info.product_name or ""
        return ""

    @property
    def device_version(self) -> str:
        """Return the device firmware version."""
        return self._device_version

    @property
    def hardware_version(self) -> str:
        """Return the hardware version."""
        return self._hardware_version

    @property
    def protocol_version(self) -> str:
        """Return the protocol version string."""
        return self._protocol_version_str

    @property
    def function(self) -> dict[str, TuyaBLEDeviceFunction]:
        """Return the device function definitions."""
        return self._function

    @property
    def status_range(self) -> dict[str, TuyaBLEDeviceFunction]:
        """Return the device status range definitions."""
        return self._status_range

    def append_functions(
        self,
        function: list[dict[str, Any]] | None,
        status_range: list[dict[str, Any]] | None,
    ) -> None:
        """Parse and store function/status_range credential lists."""
        self._append_function_list(function, self._function, "function")
        self._append_function_list(status_range, self._status_range, "status")

    def _append_function_list(
        self,
        entries: list[dict[str, Any]] | None,
        target: dict[str, TuyaBLEDeviceFunction],
        kind: str,
    ) -> None:
        """Populate a function/status_range dict, skipping entries with no DP ID."""
        if not entries:
            return
        for f in entries:
            dpcode = f.get("code")
            if dpcode and "dp_id" in f:
                target[dpcode] = TuyaBLEDeviceFunction(**f)
            elif dpcode:
                _LOGGER.warning(
                    "%s: Ignoring %s metadata without a DP ID for %s",
                    self.address,
                    kind,
                    dpcode,
                )

    @property
    def datapoints(self) -> TuyaBLEDataPoints:
        """Get datapoints exposed by device."""
        return self._datapoints

    def get_or_create_datapoint(
        self,
        dp_id: int,
        dp_type: TuyaBLEDataPointType,
        value: bytes | bool | int | str | None = None,
    ) -> TuyaBLEDataPoint:
        """Return an existing datapoint by ID, or create and register a new one."""
        return self._datapoints.get_or_create(dp_id, dp_type, value)

    def _fire_connected_callbacks(self) -> None:
        """Invoke all registered connected callbacks, isolating each."""
        for callback in self._connected_callbacks:
            try:
                callback()
            except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
                _LOGGER.warning(
                    "%s: Connected callback failed", self.address, exc_info=True
                )

    def register_connected_callback(
        self, callback: Callable[[], None]
    ) -> Callable[[], None]:
        """Register a callback to be called when the device connects."""

        def unregister_callback() -> None:
            with suppress(ValueError):
                self._connected_callbacks.remove(callback)

        self._connected_callbacks.append(callback)
        return unregister_callback

    def register_callback(
        self,
        callback: Callable[[list[TuyaBLEDataPoint]], None],
    ) -> Callable[[], None]:
        """Register a callback to be called when the state changes."""

        def unregister_callback() -> None:
            with suppress(ValueError):
                self._callbacks.remove(callback)

        self._callbacks.append(callback)
        return unregister_callback

    def _fire_disconnected_callbacks(self) -> None:
        """Invoke all registered disconnected callbacks, isolating each."""
        for callback in self._disconnected_callbacks:
            try:
                callback()
            except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
                _LOGGER.warning(
                    "%s: Disconnected callback failed", self.address, exc_info=True
                )

    def register_disconnected_callback(
        self, callback: Callable[[], None]
    ) -> Callable[[], None]:
        """Register a callback to be called when device disconnected."""

        def unregister_callback() -> None:
            with suppress(ValueError):
                self._disconnected_callbacks.remove(callback)

        self._disconnected_callbacks.append(callback)
        return unregister_callback

    async def stop(self) -> None:
        """Stop the TuyaBLE and cancel any in-flight background tasks."""
        _LOGGER.debug("%s: Stop", self.address)
        # Set before touching any lock, otherwise an in-flight connect holds
        # _connect_lock and stop() blocks behind its whole retry loop.
        self._expected_disconnect = True
        for task in self._background_tasks():
            if not task.done():
                task.cancel()
        await self._execute_disconnect()
        for task in self._background_tasks():
            with suppress(asyncio.CancelledError, Exception):
                await task

    def _background_tasks(self) -> tuple[asyncio.Task[None], ...]:
        """Snapshot the currently tracked background tasks."""
        return tuple(
            task
            for task in (
                self._reconnect_task,
                self._resend_task,
                self._identity_connect_task,
                *self._send_response_tasks,
            )
            if task is not None
        )

    def _disconnected(self, client: BleakClientWithServiceCache) -> None:
        """Handle BLE disconnection: fire callbacks and reconnect."""
        if client is self._discarded_client:
            # Closed on purpose before any handshake, see _discard_client. The
            # callback can arrive after a later connection has replaced it, so
            # it must not touch the current client or the paired state.
            self._discarded_client = None
            _LOGGER.debug(
                "%s: Closed the connection made before the FD50 identity arrived",
                self.address,
            )
            return
        fingerbot_touch = self._is_fingerbot_touch
        if fingerbot_touch and self._client is not client:
            return
        was_paired = self._is_paired
        self._is_paired = False
        if fingerbot_touch:
            self._client = None
            self._clean_input()
            self._cancel_pending_responses()
        self._fire_disconnected_callbacks()
        if self._expected_disconnect:
            _LOGGER.debug(
                "%s: Disconnected from device; RSSI: %s",
                self.address,
                self.rssi,
            )
            return
        self._client = None
        _LOGGER.warning(
            "%s: Device unexpectedly disconnected; RSSI: %s",
            self.address,
            self.rssi,
        )
        if was_paired:
            _LOGGER.debug(
                "%s: Scheduling reconnect; RSSI: %s",
                self.address,
                self.rssi,
            )
            self._schedule_reconnect()

    async def _execute_disconnect(self) -> None:
        """Execute disconnection."""
        self._expected_disconnect = True
        self._cancel_pending_responses()
        async with self._connect_lock:
            client = self._client
            if self._is_fingerbot_touch and client is not None:
                try:
                    await self._close_fingerbot_touch_client(client)
                finally:
                    self._disconnected(client)
            else:
                self._client = None
                if client and client.is_connected:
                    await client.stop_notify(self._notify_char)
                    await client.disconnect()
        async with self._seq_num_lock:
            self._current_seq_num = 1

    @property
    def _is_fingerbot_touch(self) -> bool:
        """Return whether this device needs the Fingerbot Touch reconnect fix."""
        return self.category == "kg" and self.product_id == "bs3ubslo"

    async def _close_fingerbot_touch_client(
        self, client: BleakClientWithServiceCache
    ) -> None:
        """Release the client and its notifications even if the caller is cancelled."""
        if not client.is_connected:
            return

        async def disconnect() -> None:
            try:
                # Bleak stops notifications when the connection is closed.
                async with asyncio.timeout(_FINGERBOT_TOUCH_DISCONNECT_TIMEOUT):
                    await client.disconnect()
            except (TimeoutError, *BLE_CONNECTION_EXCEPTIONS):
                _LOGGER.debug(
                    "%s: BLE client cleanup failed", self.address, exc_info=True
                )

        task = asyncio.create_task(disconnect())
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await asyncio.shield(task)
            raise

    async def _handle_write_failure(self, client: BleakClientWithServiceCache) -> None:
        """Handle a failed GATT write for this device.

        Fingerbot Touch closes only the client whose write failed so a
        replacement link is left alone; other devices keep the original
        behavior of treating a failed write on a live link as a disconnect.
        """
        if not self._is_fingerbot_touch:
            if self._client and self._client.is_connected:
                self._disconnected(self._client)
        elif self._client is client:
            try:
                await self._close_fingerbot_touch_client(client)
            finally:
                self._disconnected(client)

    def _cancel_pending_responses(self) -> None:
        """Fail every awaited response so no sender waits for its full timeout."""
        pending = list(self._input_expected_responses.items())
        self._input_expected_responses.clear()
        for seq_num, future in pending:
            if not future.done():
                future.set_exception(TuyaBLEDeviceError(0))
            _LOGGER.debug(
                "%s: Cancelled pending response for packet #%s", self.address, seq_num
            )

    async def _ensure_connected(self) -> None:
        """Ensure connection to device is established."""
        if self._expected_disconnect:
            return
        if self._connect_lock.locked():
            _LOGGER.warning(
                "%s: Connection already in progress,"
                " waiting for it to complete; RSSI: %s",
                self.address,
                self.rssi,
            )
        if self._is_ready():
            return
        if self._await_fd50_identity():
            raise TuyaBLEDeviceError(0)
        async with self._connect_lock:
            if self._is_ready():
                return
            await self._connect_with_retries()
        self._log_connection_status()

    def _is_ready(self) -> bool:
        """Return True if the device is connected and paired."""
        return bool(self._client and self._client.is_connected and self._is_paired)

    async def _try_connect_and_configure(self) -> bool:
        """Try one connection + handshake cycle. Returns True on success."""
        client = await self._try_establish_connection()
        if client is None:
            return False
        if self._await_fd50_identity():
            # The FD50 advertisement arrived while connecting, still without its
            # identity. Retrying would only repeat the wrong-protocol handshake.
            await self._discard_client(client)
            raise TuyaBLEDeviceError(0)
        self._client = client
        if not self._is_fingerbot_touch:
            return await self._configure_client()
        self._clean_input()
        configured = False
        try:
            configured = (
                not self._expected_disconnect
                and await self._configure_client()
                and not self._expected_disconnect
                and self._client is client
                and client.is_connected
            )
            return configured
        finally:
            if not configured:
                if self._client is None or self._client is client:
                    self._client = None
                    self._is_paired = False
                    self._clean_input()
                    self._cancel_pending_responses()
                await self._close_fingerbot_touch_client(client)

    async def _configure_client(self) -> bool:
        """Subscribe to notifications and complete the BLE handshake."""
        return (
            await self._try_start_notifications()
            and await self._try_send_device_info()
            and await self._try_send_pairing()
        )

    async def _connect_with_retries(self) -> None:
        """Try connecting up to 100 times, raising on failure."""
        for _ in range(100):
            if self._expected_disconnect:
                return
            if await self._try_connect_and_configure():
                return
        if self._expected_disconnect:
            return
        _LOGGER.error(
            "%s: Connecting, all attempts failed; RSSI: %s",
            self.address,
            self.rssi,
        )
        raise BleakNotFoundError()

    async def _try_establish_connection(
        self,
    ) -> BleakClientWithServiceCache | None:
        """Try to establish a BLE connection. Returns client or None."""
        try:
            async with global_connect_lock:
                _LOGGER.debug("%s: Connecting; RSSI: %s", self.address, self.rssi)
                client = await establish_connection(
                    BleakClientWithServiceCache,
                    self._ble_device,
                    self.address,
                    self._disconnected,
                    use_services_cache=True,
                    ble_device_callback=lambda: self._ble_device,
                )
        except BleakNotFoundError:
            _LOGGER.warning(
                "%s: device not found, not in range, or poor RSSI: %s",
                self.address,
                self.rssi,
                exc_info=True,
            )
            return None
        except BLEAK_EXCEPTIONS:
            _LOGGER.warning("%s: communication failed", self.address, exc_info=True)
            return None
        except BLE_CONNECTION_EXCEPTIONS:
            _LOGGER.warning("%s: unexpected error", self.address, exc_info=True)
            return None

        if client and client.is_connected:
            _LOGGER.debug("%s: Connected; RSSI: %s", self.address, self.rssi)
            return client
        return None

    def _await_fd50_identity(self) -> bool:
        """Return True, and start waiting, while an FD50 advertisement is unusable.

        It first reads the newest stored advertisement. A beacon, another
        version, malformed data, or a device without credentials that has no
        scan response leaves the protocol version unknown, and a handshake would
        run the protocol-2 default against protocol-4 firmware. The next usable
        advertisement starts the connection. Classic advertisements never wait.
        """
        if self._advertisement_provider is not None and (
            latest := self._advertisement_provider()
        ):
            self._ble_device, self._advertisement_data = latest
        if (
            not self._advertisement_data
            or SERVICE_UUID_V4 not in self._advertisement_data.service_data
            or self._decode_advertisement_data()
        ):
            return False
        if not self._awaiting_fd50_identity:
            _LOGGER.debug(
                "%s: Waiting for a supported FD50 identity before connecting",
                self.address,
            )
        self._awaiting_fd50_identity = True
        return True

    async def _discard_client(self, client: BleakClientWithServiceCache) -> None:
        """Close a connection that will not be used, without treating it as a drop."""
        self._discarded_client = client
        try:
            await client.disconnect()
        except BLE_CONNECTION_EXCEPTIONS:
            _LOGGER.debug(
                "%s: Closing the unused connection failed", self.address, exc_info=True
            )

    def _start_identity_connect(self) -> None:
        """Connect in the background once the awaited FD50 identity is known."""
        if self._expected_disconnect:
            return
        pending = self._identity_connect_task
        if pending is not None and not pending.done():
            return
        self._identity_connect_task = self._spawn_background_task(
            self._connect_after_identity(), "FD50 identity connect task"
        )

    async def _connect_after_identity(self) -> None:
        """Run the connection that waited for the FD50 identity.

        Unlike ``_reconnect`` this keeps the sequence number: another caller may
        have connected meanwhile, and protocol-4 firmware drops the link when it
        does not increase.
        """
        try:
            await self._ensure_connected()
        except BLE_CONNECTION_EXCEPTIONS:
            _LOGGER.debug(
                "%s: Connecting after the FD50 identity arrived failed",
                self.address,
                exc_info=True,
            )

    def _select_characteristics(self, client: BleakClientWithServiceCache) -> None:
        """Choose the notify/write characteristic pair for this connection.

        Protocol 2/3 devices always use the classic pair. A protocol-4 device
        uses the SDK's FD50 pair when its GATT services offer both
        characteristics, and the classic pair otherwise.
        """
        self._notify_char = CHARACTERISTIC_NOTIFY
        self._write_char = CHARACTERISTIC_WRITE
        if self._protocol_version != PROTOCOL_VERSION_V4:
            return
        services = client.services
        if (
            services.get_characteristic(CHARACTERISTIC_NOTIFY_V4) is not None
            and services.get_characteristic(CHARACTERISTIC_WRITE_V4) is not None
        ):
            self._notify_char = CHARACTERISTIC_NOTIFY_V4
            self._write_char = CHARACTERISTIC_WRITE_V4
        _LOGGER.debug(
            "%s: Using notify %s and write %s for protocol %s",
            self.address,
            self._notify_char,
            self._write_char,
            self._protocol_version,
        )

    def _build_device_info_request(self) -> bytes:
        """Return the device info request payload for the current protocol.

        The version comes from the decoded advertisement, or from byte 2 of the
        previous device info response, which overwrites it. Protocol 4 firmware
        disconnects unless the request carries exactly two bytes: the app's
        preferred data length, which the device then uses to size its own
        fragments. Earlier protocols take an empty request.
        """
        if self._protocol_version == PROTOCOL_VERSION_V4:
            return pack(">H", GATT_MTU)
        return b""

    async def _try_start_notifications(self) -> bool:
        """Start GATT notifications. Returns True on success."""
        client = self._client
        if client is None or not client.is_connected:
            return False

        last_notification: bytes | None = None
        last_notification_at = 0.0

        def handle_notification(sender: Any, data: bytearray) -> None:
            nonlocal last_notification, last_notification_at
            # A backend can still deliver notifications queued by a previous client.
            if self._client is not client or self._expected_disconnect:
                return
            now = time.monotonic()
            notification = bytes(data)
            # A packet 0 outside reassembly may be a complete message that needs an ACK.
            if (
                data
                and (data[0] != 0 or self._input_expected_packet_num > 0)
                and notification == last_notification
                and now - last_notification_at <= INPUT_REASSEMBLY_TIMEOUT
            ):
                return
            last_notification = notification
            last_notification_at = now
            self._safe_notification_handler(sender, data)

        try:
            self._select_characteristics(client)
            handler = (
                handle_notification
                if self._is_fingerbot_touch
                else self._safe_notification_handler
            )
            await client.start_notify(self._notify_char, handler)
            return True
        except BLE_CONNECTION_EXCEPTIONS as err:
            self._client = None
            _LOGGER.warning(
                "%s: starting notifications failed, retrying: %s",
                self.address,
                err,
            )
            return False

    async def _try_send_device_info(self) -> bool:
        """Send device info request. Returns True on success."""
        if not self._client or not self._client.is_connected:
            return False
        _LOGGER.debug("%s: Sending device info request", self.address)
        try:
            if not await self._send_packet_while_connected(
                TuyaBLECode.FUN_SENDER_DEVICE_INFO,
                self._build_device_info_request(),
                0,
                True,
            ):
                self._client = None
                _LOGGER.error(
                    "%s: Sending device info request failed",
                    self.address,
                )
                return False
            return True
        except BLE_CONNECTION_EXCEPTIONS:
            self._client = None
            _LOGGER.warning(
                "%s: Sending device info request failed",
                self.address,
                exc_info=True,
            )
            return False

    async def _try_send_pairing(self) -> bool:
        """Send pairing request. Returns True on success."""
        if not self._client or not self._client.is_connected:
            return False
        _LOGGER.debug("%s: Sending pairing request", self.address)
        try:
            if not await self._send_packet_while_connected(
                TuyaBLECode.FUN_SENDER_PAIR,
                self._build_pairing_request(),
                0,
                True,
            ):
                self._client = None
                _LOGGER.error(
                    "%s: Sending pairing request failed",
                    self.address,
                )
                return False
            return True
        except BLE_CONNECTION_EXCEPTIONS:
            self._client = None
            _LOGGER.warning(
                "%s: Sending pairing request failed",
                self.address,
                exc_info=True,
            )
            return False

    def _log_connection_status(self) -> None:
        """Log final connection status after setup."""
        if not self._client:
            _LOGGER.error("%s: No client device", self.address)
            return
        if not self._client.is_connected:
            _LOGGER.error("%s: Not connected", self.address)
            return
        if self._is_paired:
            self._awaiting_fd50_identity = False
            _LOGGER.debug("%s: Successfully connected", self.address)
            self._fire_connected_callbacks()
        else:
            _LOGGER.error("%s: Connected but not paired", self.address)

    async def _reconnect(self) -> None:
        """Attempt reconnection with exponential backoff, retrying on BLE errors."""
        _LOGGER.debug("%s: Reconnect, ensuring connection", self.address)
        async with self._seq_num_lock:
            self._current_seq_num = 1
        try:
            if self._expected_disconnect:
                return
            await self._ensure_connected()
            if self._expected_disconnect:
                return
            _LOGGER.debug("%s: Reconnect, connection ensured", self.address)
        except BLE_CONNECTION_EXCEPTIONS:
            # Includes TuyaBLEError, e.g. no session key or no device info: the
            # device stays unreachable until those are resolved, so keep retrying
            # rather than leaving it permanently disconnected.
            _LOGGER.warning(
                "%s: Reconnect failed, retrying",
                self.address,
                exc_info=True,
            )
            await asyncio.sleep(BLEAK_BACKOFF_TIME)
            if self._expected_disconnect:
                return
            _LOGGER.debug("%s: Reconnecting again", self.address)
            self._schedule_reconnect()
