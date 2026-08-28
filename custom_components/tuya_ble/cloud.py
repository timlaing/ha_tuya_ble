"""Cloud credential manager for Tuya BLE devices via the Tuya Sharing SDK."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from tuya_sharing import CustomerDevice, Manager, SharingTokenListener

from .const import (
    CONF_ENDPOINT,
    CONF_TERMINAL_ID,
    CONF_TOKEN_INFO,
    CONF_USER_CODE,
    TUYA_API_FACTORY_INFO_URL,
    TUYA_CLIENT_ID,
    TUYA_FACTORY_INFO_MAC,
)
from .tuya_ble import (
    AbstractTuyaBLEDeviceManager,
    TuyaBLEDeviceCredentials,
)

_LOGGER = logging.getLogger(__name__)


class TokenRefreshListener(SharingTokenListener):  # type: ignore[misc]
    """Persist refreshed tokens back to config entry data."""

    def __init__(self, hass: HomeAssistant, data: dict[str, Any]) -> None:
        self._hass = hass
        self._data = data

    def update_token(self, token_info: dict[str, Any]) -> None:
        """Update stored token info when refreshed."""
        self._data.update(token_info)
        _LOGGER.debug("Token refreshed for Tuya BLE")


class HASSTuyaBLEDeviceManager(AbstractTuyaBLEDeviceManager):
    """Cloud connected manager of the Tuya BLE devices credentials."""

    def __init__(self, hass: HomeAssistant, data: dict[str, Any]) -> None:
        if hass is None:
            raise ValueError("hass must not be None")
        self._hass = hass
        self._data = data
        self._manager: Manager | None = None

    async def initialize(self) -> None:
        """Create tuya_sharing.Manager from stored token info."""
        token_info = self._data.get(CONF_TOKEN_INFO, {})

        self._manager = Manager(
            client_id=TUYA_CLIENT_ID,
            user_code=self._data.get(CONF_USER_CODE, ""),
            terminal_id=self._data.get(CONF_TERMINAL_ID, ""),
            end_point=self._data.get(CONF_ENDPOINT, ""),
            token_response=token_info,
            listener=TokenRefreshListener(self._hass, self._data),
        )

        await self._hass.async_add_executor_job(self._manager.update_device_cache)
        _LOGGER.debug(
            "Tuya BLE manager initialized, found %d devices",
            len(self._manager.device_map),
        )

    async def get_device_credentials(
        self,
        address: str,
        force_update: bool = False,
        save_data: bool = False,
    ) -> TuyaBLEDeviceCredentials | None:
        """Get credentials of the Tuya BLE device by MAC address."""
        if self._manager is None:
            await self.initialize()

        if self._manager is None:
            raise ConfigEntryNotReady("Failed to initialize Tuya cloud manager")

        if force_update:
            await self._hass.async_add_executor_job(self._manager.update_device_cache)

        for device in self._manager.device_map.values():
            if result := await self._fetch_device_credentials(device, address):
                _LOGGER.debug("Retrieved credentials for %s", address)
                if save_data:
                    self._data[CONF_ADDRESS] = address
                return result

        _LOGGER.warning("No Tuya credentials found for MAC %s", address)
        return None

    async def _fetch_device_credentials(
        self,
        device: CustomerDevice,
        address: str,
    ) -> TuyaBLEDeviceCredentials | None:
        """Build a device's credentials if its factory MAC matches the address."""
        if self._manager is None:
            raise ConfigEntryNotReady("Cloud manager not initialized")

        response = await self._hass.async_add_executor_job(
            self._manager.customer_api.get,
            TUYA_API_FACTORY_INFO_URL % device.id,
        )
        result = response.get("result") if response else None
        if not result:
            return None

        factory_info = result[0]
        if TUYA_FACTORY_INFO_MAC not in factory_info:
            return None

        if _normalize_mac(factory_info[TUYA_FACTORY_INFO_MAC]) != address:
            return None

        return _build_credentials(device)

    @property
    def data(self) -> dict[str, Any]:
        """Return the stored configuration data."""
        return self._data


def _normalize_mac(mac: str) -> str:
    """Normalize a MAC address to uppercase, colon-separated form."""
    return ":".join(mac[i : i + 2] for i in range(0, 12, 2)).upper()


def _extract_functions(device: CustomerDevice) -> list[dict[str, str]]:
    """Extract function specifications from a device."""
    if not device.function:
        return []
    return [
        {
            "code": f.code,
            "desc": f.desc,
            "name": f.name,
            "type": f.type,
            "values": f.values,
        }
        for f in device.function.values()
    ]


def _extract_status_range(device: CustomerDevice) -> list[dict[str, str]]:
    """Extract status range specifications from a device."""
    if not device.status_range:
        return []
    return [
        {
            "code": s.code,
            "type": s.type,
            "values": s.values,
        }
        for s in device.status_range.values()
    ]


def _build_credentials(device: CustomerDevice) -> TuyaBLEDeviceCredentials:
    """Build Tuya BLE credentials from a cloud device."""
    return TuyaBLEDeviceCredentials(
        uuid=device.uuid,
        local_key=device.local_key,
        device_id=device.id,
        category=device.category,
        product_id=device.product_id,
        device_name=device.name,
        product_model=None,
        product_name=device.product_name,
        functions=_extract_functions(device),
        status_range=_extract_status_range(device),
    )
