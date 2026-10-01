"""Cloud credential manager for Tuya BLE devices via the Tuya Sharing SDK."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.util import dt as dt_util
from tuya_sharing import CustomerDevice, Manager, SharingTokenListener

from .const import (
    CONF_ENDPOINT,
    CONF_TERMINAL_ID,
    CONF_TOKEN_INFO,
    CONF_USER_CODE,
    TUYA_CLIENT_ID,
)
from .tuya_ble import TuyaBLEDeviceCredentials

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


class HASSTuyaBLEDeviceManager:
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

    async def get_cloud_device_by_uuid(
        self,
        uuid: str,
        *,
        force_update: bool = False,
    ) -> CustomerDevice | None:
        """Return the raw cloud device matching a decoded BLE device UUID."""
        if self._manager is None:
            await self.initialize()

        if self._manager is None:
            raise ConfigEntryNotReady("Failed to initialize Tuya cloud manager")

        if force_update:
            await self._hass.async_add_executor_job(self._manager.update_device_cache)

        for device in self._manager.device_map.values():
            if device.uuid == uuid:
                return device
        return None

    async def get_device_credentials_by_uuid(
        self,
        uuid: str,
        *,
        force_update: bool = False,
    ) -> TuyaBLEDeviceCredentials | None:
        """Get cloud credentials matching a decoded BLE device UUID."""
        device = await self.get_cloud_device_by_uuid(uuid, force_update=force_update)
        if device is None:
            _LOGGER.warning("No Tuya credentials found for UUID %s", uuid)
            return None

        _LOGGER.debug("Retrieved credentials for Tuya UUID %s", uuid)
        return _build_credentials(device)

    @property
    def data(self) -> dict[str, Any]:
        """Return the stored configuration data."""
        return self._data


def _extract_local_strategy(
    device: CustomerDevice,
    supported_codes: set[str],
) -> list[dict[str, Any]]:
    """Extract DP metadata for selected codes from the local strategy."""
    result: list[dict[str, Any]] = []
    for dp_id, strategy in (getattr(device, "local_strategy", None) or {}).items():
        code = strategy.get("status_code")
        config = strategy.get("config_item") or {}
        dp_type = config.get("valueType")
        if code not in supported_codes or not isinstance(dp_id, int) or not dp_type:
            continue
        result.append({
            "code": code,
            "dp_id": dp_id,
            "type": dp_type,
            "values": config.get("valueDesc"),
        })
    return result


def _extract_functions(device: CustomerDevice) -> list[dict[str, Any]]:
    """Extract writable function specifications with their BLE DP IDs."""
    return _extract_local_strategy(device, set(device.function or {}))


def _extract_status_range(device: CustomerDevice) -> list[dict[str, Any]]:
    """Extract readable status specifications with their BLE DP IDs."""
    return _extract_local_strategy(device, set(device.status_range or {}))


def _extract_full_status_range(device: CustomerDevice) -> list[dict[str, Any]]:
    """Extract every status specification, including the SDK-resolved report type."""
    status_range: dict[str, Any] = getattr(device, "status_range", None) or {}
    return [
        {
            "code": code,
            "type": getattr(spec, "type", None),
            "values": getattr(spec, "values", None),
            "report_type": getattr(spec, "report_type", None),
        }
        for code, spec in status_range.items()
    ]


def _extract_full_local_strategy(device: CustomerDevice) -> list[dict[str, Any]]:
    """Return the device's local strategy unfiltered, keyed by dp id.

    The runtime ``function``/``status_range`` lists drop every field but
    ``code``/``dp_id``/``type``/``values``, so they cannot explain why a data
    point decodes or scales the way it does. Persist the strategy as the cloud
    returned it, keeping ``statusFormat``, ``enumMappingMap`` and
    ``valueConvert``, which are what the DP value handlers actually read.
    """
    strategy: dict[int, dict[str, Any]] = getattr(device, "local_strategy", None) or {}
    return [{"dp_id": dp_id, **details} for dp_id, details in sorted(strategy.items())]


def capture_local_schema(device: CustomerDevice) -> dict[str, Any]:
    """Capture the device's complete local schema for later diagnostics.

    This is DP metadata only — it deliberately carries no credentials, so it is
    safe to persist alongside the config entry. ``captured_at`` records when the
    cloud answered, because the schema is a one-off snapshot and never
    refreshed by the integration.
    """
    return {
        "captured_at": dt_util.utcnow().isoformat(),
        "local_strategy": _extract_full_local_strategy(device),
        "status_range": _extract_full_status_range(device),
    }


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
        local_schema=capture_local_schema(device),
    )
