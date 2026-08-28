"""Tuya BLE device protocol implementation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
import hashlib
import json
import logging
from typing import Any

from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from bleak_retry_connector import (
    BLEAK_BACKOFF_TIME,
    BleakClientWithServiceCache,
    BleakNotFoundError,
    establish_connection,
)
from Crypto.Cipher import AES

from ..const import DPType
from .const import (
    CHARACTERISTIC_NOTIFY,
    MANUFACTURER_DATA_ID,
    SERVICE_UUID,
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
            try:
                parsed = json.loads(value)
                if parsed:
                    value = parsed
            except json.JSONDecodeError, TypeError:
                pass
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
        self._input_expected_responses: dict[int, asyncio.Future[int]] = {}
        self._uuid: str = ""

        self._datapoints = TuyaBLEDataPoints(self)
        self._function: dict[str, TuyaBLEDeviceFunction] = {}
        self._status_range: dict[str, TuyaBLEDeviceFunction] = {}

        self._reconnect_task: asyncio.Task[None] | None = None
        self._resend_task: asyncio.Task[None] | None = None
        self._send_response_tasks: set[asyncio.Task[None]] = set()

    def set_ble_device_and_advertisement_data(
        self, ble_device: BLEDevice, advertisement_data: AdvertisementData
    ) -> None:
        """Update the underlying BLE device and advertisement data after a new scan."""
        self._ble_device = ble_device
        self._advertisement_data = advertisement_data

    async def initialize(self) -> None:
        """Initialize the device by loading credentials and decoding advertisements."""
        _LOGGER.debug("%s: Initializing", self.address)
        if await self._update_device_info():
            self._decode_advertisement_data()

    async def initialize_with_credentials(
        self, credentials: TuyaBLEDeviceCredentials
    ) -> None:
        """Initialize the device with pre-built credentials (no cloud needed)."""
        _LOGGER.debug("%s: Initializing with stored credentials", self.address)
        self._device_info = credentials
        if len(credentials.local_key) < 6:
            raise TuyaBLEDeviceError(0)
        self._local_key = credentials.local_key[:6].encode()
        self._login_key = hashlib.md5(self._local_key).digest()  # noqa: S4790
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
        for _ in range(44 - len(result)):
            result += b"\x00"

        return bytes(result)

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
                    self._ble_device.address, False
                )
            if self._device_info:
                if len(self._device_info.local_key) < 6:
                    raise TuyaBLEDeviceError(0)
                self._local_key = self._device_info.local_key[:6].encode()
                self._login_key = hashlib.md5(self._local_key).digest()  # noqa: S4790
                self.append_functions(
                    self._device_info.functions if self._device_info.functions else [],
                    self._device_info.status_range
                    if self._device_info.status_range
                    else [],
                )

        return self._device_info is not None

    def _decode_advertisement_data(self) -> None:
        if not self._advertisement_data:
            return
        raw_product_id = self._parse_product_id_from_service_data()
        self._parse_manufacturer_data(raw_product_id)

    def _parse_product_id_from_service_data(self) -> bytes | None:
        """Extract raw product ID from BLE service data."""
        if not self._advertisement_data or not self._advertisement_data.service_data:
            return None
        service_data = self._advertisement_data.service_data.get(SERVICE_UUID)
        if not service_data or len(service_data) <= 1:
            return None
        match service_data[0]:
            case 0:
                return service_data[1:]
        return None

    def _parse_manufacturer_data(self, raw_product_id: bytes | None) -> None:
        """Extract device flags and UUID from manufacturer data."""
        if (
            not self._advertisement_data
            or not self._advertisement_data.manufacturer_data
        ):
            return
        manufacturer_data = self._advertisement_data.manufacturer_data.get(
            MANUFACTURER_DATA_ID
        )
        if not manufacturer_data or len(manufacturer_data) <= 6:
            return
        self._is_bound = (manufacturer_data[0] & 0x80) != 0
        self._protocol_version = manufacturer_data[1]
        raw_uuid = manufacturer_data[6:]
        if raw_product_id:
            key = hashlib.md5(raw_product_id).digest()  # noqa: S4790
            cipher = AES.new(key, AES.MODE_CBC, key)  # noqa: S5542
            raw_uuid = cipher.decrypt(raw_uuid)
            self._uuid = raw_uuid.decode("utf-8")

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
        if function:
            for f in function:
                dpcode = f.get("code")
                if dpcode:
                    self._function[dpcode] = TuyaBLEDeviceFunction(**f)
        if status_range:
            for f in status_range:
                dpcode = f.get("code")
                if dpcode:
                    self._status_range[dpcode] = TuyaBLEDeviceFunction(**f)

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
        """Invoke all registered connected callbacks."""
        for callback in self._connected_callbacks:
            callback()

    def register_connected_callback(
        self, callback: Callable[[], None]
    ) -> Callable[[], None]:
        """Register a callback to be called when the device connects."""

        def unregister_callback() -> None:
            self._connected_callbacks.remove(callback)

        self._connected_callbacks.append(callback)
        return unregister_callback

    def register_callback(
        self,
        callback: Callable[[list[TuyaBLEDataPoint]], None],
    ) -> Callable[[], None]:
        """Register a callback to be called when the state changes."""

        def unregister_callback() -> None:
            self._callbacks.remove(callback)

        self._callbacks.append(callback)
        return unregister_callback

    def _fire_disconnected_callbacks(self) -> None:
        """Invoke all registered disconnected callbacks."""
        for callback in self._disconnected_callbacks:
            callback()

    def register_disconnected_callback(
        self, callback: Callable[[], None]
    ) -> Callable[[], None]:
        """Register a callback to be called when device disconnected."""

        def unregister_callback() -> None:
            self._disconnected_callbacks.remove(callback)

        self._disconnected_callbacks.append(callback)
        return unregister_callback

    async def start(self) -> None:
        """Start the TuyaBLE."""
        _LOGGER.debug("%s: Starting...", self.address)

    async def stop(self) -> None:
        """Stop the TuyaBLE and cancel any in-flight background tasks."""
        _LOGGER.debug("%s: Stop", self.address)
        tasks = (
            self._reconnect_task,
            self._resend_task,
            *self._send_response_tasks,
        )
        for task in tasks:
            if task is not None and not task.done():
                task.cancel()
        await self._execute_disconnect()
        for task in tasks:
            if task is not None and not task.done():
                with suppress(asyncio.CancelledError, Exception):
                    await task

    def _disconnected(self, client: BleakClientWithServiceCache) -> None:
        """Handle BLE disconnection: fire callbacks and reconnect."""
        was_paired = self._is_paired
        self._is_paired = False
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
            self._reconnect_task = asyncio.create_task(self._reconnect())

    async def _execute_disconnect(self) -> None:
        """Execute disconnection."""
        async with self._connect_lock:
            client = self._client
            self._expected_disconnect = True
            self._client = None
            if client and client.is_connected:
                await client.stop_notify(CHARACTERISTIC_NOTIFY)
                await client.disconnect()
        async with self._seq_num_lock:
            self._current_seq_num = 1

    async def _ensure_connected(self) -> None:
        """Ensure connection to device is established."""
        if self._expected_disconnect:
            return
        if self._connect_lock.locked():
            _LOGGER.debug(
                "%s: Connection already in progress,"
                " waiting for it to complete; RSSI: %s",
                self.address,
                self.rssi,
            )
        if self._is_ready():
            return
        async with self._connect_lock:
            await asyncio.sleep(0.01)
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
        self._client = client
        return (
            await self._try_start_notifications()
            and await self._try_send_device_info()
            and await self._try_send_pairing()
        )

    async def _connect_with_retries(self) -> None:
        """Try connecting up to 100 times, raising on failure."""
        for _ in range(100):
            if await self._try_connect_and_configure():
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
            _LOGGER.exception(
                "%s: device not found, not in range, or poor RSSI: %s",
                self.address,
                self.rssi,
                exc_info=True,
            )
            return None
        except BLEAK_EXCEPTIONS:
            _LOGGER.debug("%s: communication failed", self.address, exc_info=True)
            return None
        except BLE_CONNECTION_EXCEPTIONS:
            _LOGGER.debug("%s: unexpected error", self.address, exc_info=True)
            return None

        if client and client.is_connected:
            _LOGGER.debug("%s: Connected; RSSI: %s", self.address, self.rssi)
            return client
        return None

    async def _try_start_notifications(self) -> bool:
        """Start GATT notifications. Returns True on success."""
        if not self._client or not self._client.is_connected:
            return False
        try:
            await self._client.start_notify(
                CHARACTERISTIC_NOTIFY, self._notification_handler
            )
            return True
        except BLE_CONNECTION_EXCEPTIONS:
            self._client = None
            _LOGGER.exception(
                "%s: starting notifications failed",
                self.address,
                exc_info=True,
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
                bytes(0),
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
            _LOGGER.exception(
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
            _LOGGER.exception(
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
        except BLEAK_EXCEPTIONS:
            _LOGGER.debug(
                "%s: Reconnect, failed to ensure connection - backing off",
                self.address,
                exc_info=True,
            )
            await asyncio.sleep(BLEAK_BACKOFF_TIME)
            if self._expected_disconnect:
                return
            _LOGGER.debug("%s: Reconnecting again", self.address)
            self._reconnect_task = asyncio.create_task(self._reconnect())
