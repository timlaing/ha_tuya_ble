"""Connection-local GATT selection and verified FD50 fragment capacities."""

from __future__ import annotations

from abc import abstractmethod
import logging
from struct import pack

from bleak_retry_connector import BleakClientWithServiceCache

from .const import (
    CHARACTERISTIC_NOTIFY,
    CHARACTERISTIC_NOTIFY_V4,
    CHARACTERISTIC_WRITE,
    CHARACTERISTIC_WRITE_V4,
    GATT_MTU,
    PROTOCOL_VERSION_V4,
    ConnectionProfile,
)
from .exceptions import TuyaBLEDeviceError

_LOGGER = logging.getLogger(__name__)


class TuyaBLEConnectionProfile:
    """Select transport parameters afresh for every physical BLE connection."""

    _protocol_version: int
    _connection_profile: ConnectionProfile | None
    _packet_size: int
    _notify_char: str
    _write_char: str

    @property
    @abstractmethod
    def address(self) -> str:
        """Return the implementing device's address."""

    @property
    def connection_profile(self) -> ConnectionProfile | None:
        """Return the verified handshake profile, without any credentials."""
        return self._connection_profile

    def _select_characteristics(self, client: BleakClientWithServiceCache) -> None:
        """Use FD50 only for v4 devices offering both, otherwise classic."""
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
        """v4 requests exactly two bytes specifying safe ATT data capacity."""
        if self._protocol_version == PROTOCOL_VERSION_V4:
            return pack(">H", self._packet_size)
        return b""

    def _configure_packet_size(self, client: BleakClientWithServiceCache) -> None:
        """Recompute safe write capacity on every connection, never from cache."""
        self._packet_size = GATT_MTU
        profile = self._connection_profile
        if profile is not None and profile.negotiated_mtu:
            characteristic = client.services.get_characteristic(self._write_char)
            capacity = getattr(characteristic, "max_write_without_response_size", 20)
            if not isinstance(capacity, int) or capacity <= GATT_MTU:
                raise TuyaBLEDeviceError(0)
            # The documented Tuya BLE SDK ATT data limit is 244 bytes.
            self._packet_size = min(capacity, 244)
        _LOGGER.debug(
            "%s: Connection profile %s, fragment capacity %s",
            self.address,
            profile or "default",
            self._packet_size,
        )
