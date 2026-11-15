"""Shared ownership and diagnostics for configuration-time BLE verification."""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .tuya_ble import MANUFACTURER_DATA_ID, SERVICE_UUIDS

PROBE_CLAIM_TTL = 120
_PROBE_STATE = f"{DOMAIN}_probe_state"
ProbeClaim = tuple[object, str, float]


def _consume_probe_result(task: asyncio.Task[Any]) -> None:
    """Observe probe failures without logging exception text after flow removal."""
    if not task.cancelled():
        task.exception()


def probe_state(
    hass: HomeAssistant,
) -> tuple[asyncio.Lock, dict[str, ProbeClaim]]:
    """Serialise probes and reserve matched cloud IDs until entry creation or TTL."""
    state = hass.data.setdefault(_PROBE_STATE, {"lock": asyncio.Lock(), "claims": {}})
    return state["lock"], state["claims"]


def advertisement_hex(discovery_info: BluetoothServiceInfoBleak) -> str:
    """Render Tuya advertisement payloads, never credentials, for diagnostics."""
    advertisement = discovery_info.advertisement
    service_data = advertisement.service_data or {}
    manufacturer_data = advertisement.manufacturer_data or {}
    return (
        " ".join(
            f"service_data[{uuid}]={service_data.get(uuid, b'').hex()}"
            for uuid in SERVICE_UUIDS
        )
        + " "
        f"manufacturer_data[{MANUFACTURER_DATA_ID}]="
        f"{manufacturer_data.get(MANUFACTURER_DATA_ID, b'').hex()}"
    )
