"""Connection-local GATT selection and verified FD50 fragment capacities."""

from __future__ import annotations

from abc import abstractmethod
import asyncio
from collections.abc import Callable
import logging
from struct import pack
from typing import Any

from bleak_retry_connector import BleakClientWithServiceCache

from .const import (
    APP_DEVICE_INFO_CAPACITY,
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
MTU_NEGOTIATION_TIMEOUT = 2.0
MTU_NEGOTIATION_POLL_INTERVAL = 0.05


class TuyaBLEConnectionProfile:
    """Select transport parameters afresh for every physical BLE connection."""

    _protocol_version: int
    _connection_profile: ConnectionProfile | None
    _packet_size: int
    _notify_char: str
    _write_char: str
    _client: Any
    _expected_disconnect: bool
    # The protocol version and bound flag of this connection's encrypted
    # DEVICE_INFO reply. Unlike _protocol_version and _is_bound, advertisements
    # never change them during a handshake.
    _device_info_protocol: int | None = None
    _device_info_bound: bool | None = None

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
        self._device_info_protocol = None
        self._device_info_bound = None
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

    def _may_pair(self) -> bool:
        """Never pair a v4 device whose device-info reply reports it unbound.

        Pairing an unbound device would register it with these credentials,
        which setup verification also refuses. Classic devices keep their
        existing behaviour.
        """
        if (
            self._device_info_protocol == PROTOCOL_VERSION_V4
            and not self._device_info_bound
        ):
            _LOGGER.debug(
                "%s: Device reports it is not added to an account; not pairing",
                self.address,
            )
            return False
        return True

    async def _start_notify(
        self,
        client: BleakClientWithServiceCache,
        handler: Callable[[Any, bytearray], None],
    ) -> None:
        """Subscribe, using BlueZ StartNotify on FD50; AcquireNotify can lose reports.

        Other backends ignore the BlueZ-specific argument.
        """
        kwargs: dict[str, Any] = {}
        if self._notify_char == CHARACTERISTIC_NOTIFY_V4:
            kwargs["bluez"] = {"use_start_notify": True}
        await client.start_notify(self._notify_char, handler, **kwargs)

    def _build_device_info_request(self) -> bytes:
        """v4 requests exactly two bytes specifying safe ATT data capacity."""
        if self._protocol_version != PROTOCOL_VERSION_V4:
            return b""
        profile = self._connection_profile
        if profile is not None and profile.app_capacity:
            return pack(">H", APP_DEVICE_INFO_CAPACITY)
        return pack(">H", self._packet_size)

    async def _configure_packet_size(self, client: BleakClientWithServiceCache) -> None:
        """Recompute safe write capacity on every connection, never from cache."""
        self._packet_size = GATT_MTU
        profile = self._connection_profile
        if profile is not None and profile.negotiated_mtu:
            self._packet_size = await self._wait_for_packet_capacity(client)
        _LOGGER.debug(
            "%s: Connection profile %s, fragment capacity %s",
            self.address,
            profile or "default",
            self._packet_size,
        )

    async def _wait_for_packet_capacity(
        self, client: BleakClientWithServiceCache
    ) -> int:
        """Wait briefly for negotiation, without writing or exceeding ATT limits."""
        try:
            async with asyncio.timeout(MTU_NEGOTIATION_TIMEOUT):
                while True:
                    if (
                        self._expected_disconnect
                        or self._client is not client
                        or not client.is_connected
                    ):
                        raise TuyaBLEDeviceError(0)
                    characteristic = client.services.get_characteristic(
                        self._write_char
                    )
                    capacity = getattr(
                        characteristic, "max_write_without_response_size", GATT_MTU
                    )
                    if isinstance(capacity, int) and capacity > GATT_MTU:
                        # The documented Tuya BLE SDK ATT data limit is 244 bytes.
                        return min(capacity, 244)
                    await asyncio.sleep(MTU_NEGOTIATION_POLL_INTERVAL)
        except TimeoutError:
            raise TuyaBLEDeviceError(0) from None
