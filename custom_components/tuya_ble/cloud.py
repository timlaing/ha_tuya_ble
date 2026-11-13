"""Cloud credential manager for Tuya BLE devices via the Tuya Sharing SDK."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.util import dt as dt_util
from tuya_sharing import CustomerDevice, Manager, SharingTokenListener

from .const import (
    CONF_DEVICE_ID,
    CONF_ENDPOINT,
    CONF_TERMINAL_ID,
    CONF_TOKEN_INFO,
    CONF_USER_CODE,
    DOMAIN,
    TUYA_CLIENT_ID,
)
from .tuya_ble import TuyaBLEDeviceCredentials

_LOGGER = logging.getLogger(__name__)


def _is_probe_candidate(
    device: CustomerDevice, advertised_uuid: str, configured: set[str | None]
) -> bool:
    """Allow only complete, unclaimed BLE cloud records with frame-safe IDs.

    A product/name resemblance is never proof: filtering only bounds the set
    that the encrypted read-only authentication probe may attempt.
    """
    if getattr(device, "uuid", None) == advertised_uuid:
        return False
    if getattr(device, "id", None) in configured:
        return False
    fields = ("uuid", "local_key", "id", "category", "product_id")
    if not all(
        isinstance(getattr(device, field, None), str) and getattr(device, field)
        for field in fields
    ):
        return False
    return (
        len(device.local_key) >= 6 and len(device.uuid) <= 16 and len(device.id) <= 20
    )


class TokenRefreshListener(SharingTokenListener):  # type: ignore[misc]
    """Persist refreshed tokens back to config entry data."""

    def __init__(self, hass: HomeAssistant, data: dict[str, Any]) -> None:
        """Retain the Home Assistant instance and mutable token storage."""
        self._hass = hass
        self._data = data

    def update_token(self, token_info: dict[str, Any]) -> None:
        """Update stored token info when refreshed."""
        self._data.update(token_info)
        _LOGGER.debug("Token refreshed for Tuya BLE")


class HASSTuyaBLEDeviceManager:
    """Cloud connected manager of the Tuya BLE devices credentials."""

    def __init__(self, hass: HomeAssistant, data: dict[str, Any]) -> None:
        """Store cloud login data without starting an SDK connection."""
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

    async def get_probe_candidates(
        self, advertised_uuid: str, product_id: str | None
    ) -> tuple[int, Iterator[TuyaBLEDeviceCredentials]]:
        """Count eligible records and lazily build credentials in sorted order.

        The caller checks the count against its safety cap before consuming the
        iterator, so an oversized cloud account builds no probe credentials.
        """
        if self._manager is None:
            await self.initialize()
        if self._manager is None:
            raise ConfigEntryNotReady("Failed to initialize Tuya cloud manager")
        configured = {
            entry.data.get(CONF_DEVICE_ID)
            for entry in self._hass.config_entries.async_entries(DOMAIN)
        }
        candidates = sorted(
            (
                device
                for device in self._manager.device_map.values()
                if _is_probe_candidate(device, advertised_uuid, configured)
            ),
            key=lambda device: (device.product_id != product_id, device.id),
        )
        return len(candidates), _iter_probe_credentials(candidates)

    @property
    def data(self) -> dict[str, Any]:
        """Return the stored configuration data."""
        return self._data


def _iter_probe_credentials(
    candidates: list[CustomerDevice],
) -> Iterator[TuyaBLEDeviceCredentials]:
    """Build lazily while isolating malformed metadata to its own cloud record."""
    for device in candidates:
        try:
            credentials = _build_credentials(device)
        except Exception:  # pylint: disable=broad-exception-caught
            _LOGGER.debug("Skipping malformed Tuya BLE probe candidate")
            continue
        yield credentials


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


# Allowlist for the cloud device snapshot: only product identity, version and
# capability metadata that diagnostics need. The sharing SDK builds the device
# straight from the raw API payload, which also carries identifying fields
# (``ip``, ``asset_id``, ``time_zone``, and potentially ``lat``/``lon`` or
# owner/user ids); anything not named here is dropped, so those can never
# reach the config entry or a diagnostics download.
_CLOUD_INFO_ALLOW: frozenset[str] = frozenset({
    "category",
    "firmware_version",
    "mcu_sw_version",
    "online",
    "product_id",
    "product_model",
    "product_name",
    "pv",
    "sub",
    "support_local",
    "sw_version",
})

# What an exported snapshot may contain: the allowlisted fields plus the
# capture timestamp this module adds itself.
_CLOUD_INFO_EXPORT: frozenset[str] = _CLOUD_INFO_ALLOW | {"captured_at"}


_JSON_SCALARS = str | int | float | bool


def _is_json_scalar(value: Any) -> bool:
    """Return True for values JSON can represent directly."""
    return value is None or isinstance(value, _JSON_SCALARS)


def capture_cloud_info(device: CustomerDevice) -> dict[str, Any]:
    """Capture the allowlisted product/version fields the cloud reports.

    The sharing SDK builds the device from the raw API payload, so the field
    set is restricted to ``_CLOUD_INFO_ALLOW`` rather than denylisted — the
    raw payload also carries network/location identifiers that must not be
    persisted. Only scalars are kept so the entry stays JSON-safe.
    """
    info: dict[str, Any] = {"captured_at": dt_util.utcnow().isoformat()}
    for key, value in vars(device).items():
        if key not in _CLOUD_INFO_ALLOW:
            continue
        if _is_json_scalar(value):
            info[key] = value
    return info


def filter_cloud_info(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Strip a stored cloud snapshot down to the allowlisted export fields.

    Entries captured before the allowlist existed may carry identifying
    fields; filtering on export keeps a diagnostics download free of them
    without rewriting stored entry data.
    """
    return {
        key: value
        for key, value in snapshot.items()
        if key in _CLOUD_INFO_EXPORT and _is_json_scalar(value)
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
        cloud_info=capture_cloud_info(device),
    )
