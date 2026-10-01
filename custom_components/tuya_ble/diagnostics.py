"""Diagnostics support for Tuya BLE devices.

A bug report almost always needs the same two things: the data points the device
is actually reporting, and the schema the cloud claims it has. The integration
only keeps a narrow slice of the schema — the codes present in
``function``/``status_range``, reduced to ``code``/``dp_id``/``type``/``values``
— and captures it once at setup. That is enough to drive the entities but not
enough to explain a mis-scaled or mis-decoded value, so the full local strategy
is captured at setup and exported here alongside the live data points.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util

from .cloud import HASSTuyaBLEDeviceManager, capture_local_schema
from .const import (
    CONF_CATEGORY,
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_ENDPOINT,
    CONF_FUNCTIONS,
    CONF_LOCAL_KEY,
    CONF_LOCAL_SCHEMA,
    CONF_PRODUCT_ID,
    CONF_PRODUCT_MODEL,
    CONF_PRODUCT_NAME,
    CONF_STATUS_RANGE,
    CONF_TERMINAL_ID,
    CONF_TOKEN_INFO,
    CONF_USER_CODE,
    CONF_UUID,
    DOMAIN,
)
from .device_registry import get_registry

if TYPE_CHECKING:
    from .tuya_ble import TuyaBLEDevice

_LOGGER = logging.getLogger(__name__)

# The BLE local key is the only secret needed to talk to the device, and the
# token set is enough to act on the user's cloud account. `uuid` and `device_id`
# stay in the clear: they identify the device but cannot reach it, and without
# them a diagnostics download cannot be matched to a report.
_TO_REDACT = frozenset({
    CONF_ENDPOINT,
    CONF_LOCAL_KEY,
    CONF_TERMINAL_ID,
    CONF_TOKEN_INFO,
    CONF_USER_CODE,
    "access_token",
    "refresh_token",
    "t",
})

# Only a re-authentication leaves tokens on the entry; the QR login of the
# initial config flow never persists them.
_ACCESS_TOKEN = "access_token"


def _serialize_value(value: Any) -> Any:
    """Return a JSON-safe representation of a data point value."""
    if isinstance(value, bytes):
        return value.hex()
    return value


async def _async_refresh_schema(
    hass: HomeAssistant,
    entry: ConfigEntry,
    uuid: str,
) -> dict[str, Any] | None:
    """Re-fetch the cloud schema, but only if re-auth tokens are on hand.

    Diagnostics must never be the thing that fails, and must never persist new
    credentials, so this returns None — letting the caller fall back to the
    schema captured at setup — whenever the entry carries no usable token.
    """
    token_info = entry.options.get(CONF_TOKEN_INFO) or {}
    if not token_info.get(_ACCESS_TOKEN):
        return None

    manager = HASSTuyaBLEDeviceManager(hass, dict(entry.options))
    try:
        device = await manager.get_cloud_device_by_uuid(uuid, force_update=True)
    except Exception as err:  # pylint: disable=broad-exception-caught
        # Deliberately broad: the SDK raises its own error types, and a
        # diagnostics download must degrade to the stored schema rather than
        # surface an error to the user.
        _LOGGER.debug("Diagnostics schema refresh failed: %s", err)
        return None

    if device is None:
        _LOGGER.debug("Diagnostics schema refresh found no matching cloud device")
        return None

    return capture_local_schema(device)


def _device_section(device: TuyaBLEDevice | None) -> dict[str, Any]:
    """Describe the BLE device as the integration currently sees it."""
    if device is None:
        # The entry is set up but its device was not constructed, so nothing
        # live can be reported. `rssi` being null means the same thing.
        return {"loaded": False}

    return {
        "loaded": True,
        "address": device.address,
        "advertised_name": device.advertised_name,
        "cloud_name": device.cloud_name,
        "rssi": device.rssi,
        "device_version": device.device_version,
        "hardware_version": device.hardware_version,
        "protocol_version": device.protocol_version,
    }


def _live_section(device: TuyaBLEDevice | None) -> dict[str, Any]:
    """Report every data point the device has sent so far, in dp id order."""
    if device is None:
        return {}

    return {
        str(dp.dp_id): {
            "type": dp.dp_type.name if dp.dp_type is not None else None,
            "value": _serialize_value(dp.value),
            "raw": dp.raw_value.hex() if dp.raw_value is not None else None,
            "changed_by_device": dp.changed_by_device,
        }
        for dp in device.datapoints.values()
    }


def _descriptor_section(category: str, product_id: str) -> dict[str, Any]:
    """Map the YAML descriptor this product resolves to, entity by entity."""
    entities = get_registry().get(category, product_id)
    if entities is None:
        return {"resolved": False}

    return {
        "resolved": True,
        "model_name": entities.model_name,
        "device_name": entities.device_name,
        "manufacturer": entities.manufacturer,
        "inherits_category_defaults": sorted(
            set(entities.category_defaults) - set(entities.entities)
        ),
        "entities": {
            platform: [
                {
                    "dp_id": descriptor.dp_id,
                    "translation_key": descriptor.translation_key,
                    "dp_type": descriptor.dp_type,
                }
                for descriptor in descriptors
            ]
            for platform, descriptors in sorted(entities.entities.items())
        },
    }


def _registry_device(device: dr.DeviceEntry) -> dict[str, Any]:
    """Summarise a device registry entry."""
    return {
        "name": device.name,
        "name_by_user": device.name_by_user,
        "model": device.model,
        "manufacturer": device.manufacturer,
        "sw_version": device.sw_version,
        "hw_version": device.hw_version,
        "identifiers": [list(identifier) for identifier in device.identifiers],
        "connections": [list(connection) for connection in device.connections],
    }


async def _async_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
    device: dr.DeviceEntry | None = None,
) -> dict[str, Any]:
    """Build the diagnostics payload for an entry, or for one of its devices."""
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    tuya_device: TuyaBLEDevice | None = getattr(data, "device", None)

    category = entry.data[CONF_CATEGORY]
    product_id = entry.data[CONF_PRODUCT_ID]
    uuid = entry.data[CONF_UUID]

    schema = await _async_refresh_schema(hass, entry, uuid)
    if schema is not None:
        schema_source = "cloud_refresh"
    else:
        schema = entry.data.get(CONF_LOCAL_SCHEMA)
        schema_source = "stored" if schema else "missing"

    registry = dr.async_get(hass)
    home_assistant: dict[str, Any] = {
        "version": HA_VERSION,
        "integration_version": entry.version,
        "entry_state": entry.state,
        "disabled_by": entry.disabled_by,
        "captured_at": dt_util.now().isoformat(),
    }
    if device is None:
        home_assistant["devices"] = [
            _registry_device(registered)
            for registered in dr.async_entries_for_config_entry(
                registry, entry.entry_id
            )
        ]
    else:
        home_assistant["device"] = _registry_device(device)

    payload: dict[str, Any] = {
        "schema_captured_at": (schema or {}).get("captured_at"),
        "schema_source": schema_source,
        "device": _device_section(tuya_device),
        "live": _live_section(tuya_device),
        "cloud_schema": {
            # The runtime list is filtered to the codes the device exposes and
            # carries only four fields; the status range is repeated from the
            # captured schema because it is the only place `report_type` lives.
            "function": entry.data.get(CONF_FUNCTIONS) or [],
            "status_range": (
                (schema or {}).get("status_range")
                or entry.data.get(CONF_STATUS_RANGE)
                or []
            ),
            "local_strategy": (schema or {}).get("local_strategy") or [],
        },
        "descriptor": _descriptor_section(category, product_id),
        "identity": {
            "address": entry.data.get(CONF_ADDRESS),
            "uuid": uuid,
            "device_id": entry.data.get(CONF_DEVICE_ID),
            "category": category,
            "product_id": product_id,
            "device_name": entry.data.get(CONF_DEVICE_NAME),
            "product_name": entry.data.get(CONF_PRODUCT_NAME),
            "product_model": entry.data.get(CONF_PRODUCT_MODEL),
        },
        "home_assistant": home_assistant,
    }

    return async_redact_data(payload, _TO_REDACT)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    return await _async_diagnostics(hass, entry)


async def async_get_device_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry, device: dr.DeviceEntry
) -> dict[str, Any]:
    """Return diagnostics for a device entry."""
    return await _async_diagnostics(hass, entry, device)
