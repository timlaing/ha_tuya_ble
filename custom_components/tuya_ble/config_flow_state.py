"""Shared ownership and diagnostics for configuration-time BLE verification."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.core import HomeAssistant

from .const import CONF_CONNECTION_PROFILE, CONF_SEC_KEY, CONF_SECURITY_MODE, DOMAIN
from .tuya_ble import (
    MANUFACTURER_DATA_ID,
    SERVICE_UUIDS,
    TuyaBLEDeviceCredentials,
    is_valid_sec_key,
)

PROBE_CLAIM_TTL = 120
_PROBE_STATE = "probe_state"
ProbeClaim = tuple[object, str, float]
ProbeResult = tuple[TuyaBLEDeviceCredentials | None, str | None, str]

# Verification messages stay on the documented config-flow logger.
_LOGGER = logging.getLogger(f"{__package__}.config_flow")


def verified_login_data(credentials: TuyaBLEDeviceCredentials) -> dict[str, str]:
    """Return the verified handshake profile and login to save in the entry."""
    data: dict[str, str] = {}
    if credentials.connection_profile is not None:
        data[CONF_CONNECTION_PROFILE] = credentials.connection_profile.value
    if credentials.security_mode is not None:
        data[CONF_SECURITY_MODE] = credentials.security_mode.value
    return data


def sec_key_data(
    credentials: TuyaBLEDeviceCredentials, security_v2: bool
) -> dict[str, str]:
    """Return the sec_key to store: only for a device advertising Security V2.

    A malformed key is never stored, because runtime could not load the entry.
    """
    if (
        credentials.sec_key is not None
        and security_v2
        and is_valid_sec_key(credentials.sec_key)
    ):
        return {CONF_SEC_KEY: credentials.sec_key}
    return {}


def _consume_probe_result(task: asyncio.Task[Any]) -> None:
    """Observe probe failures without logging exception text after flow removal."""
    if not task.cancelled():
        task.exception()


def probe_task_result(task: asyncio.Task[ProbeResult]) -> ProbeResult:
    """Return a finished verification task's result, mapping failures to errors."""
    if task.cancelled():
        return None, None, "bluetooth_probe_failed"
    try:
        return task.result()
    except Exception:  # pylint: disable=broad-exception-caught
        _LOGGER.debug("Cloud credential verification failed")
        return None, None, "probe_setup_failed"


def probe_state(
    hass: HomeAssistant,
) -> tuple[asyncio.Lock, dict[str, ProbeClaim]]:
    """Serialise probes and reserve matched cloud IDs until entry creation or TTL."""
    state = hass.data.setdefault(DOMAIN, {}).setdefault(
        _PROBE_STATE, {"lock": asyncio.Lock(), "claims": {}}
    )
    return state["lock"], state["claims"]


def claimed_elsewhere(
    claims: dict[str, ProbeClaim], device_id: str, owner: object
) -> bool:
    """Return whether another flow holds a live reservation for this device."""
    claim = claims.get(device_id)
    return claim is not None and claim[0] is not owner and claim[2] > time.monotonic()


def reserve_device(
    claims: dict[str, ProbeClaim], device_id: str, owner: object, address: str
) -> None:
    """Reserve a verified device for one flow until entry creation or TTL."""
    claims[device_id] = (owner, address, time.monotonic() + PROBE_CLAIM_TTL)


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
