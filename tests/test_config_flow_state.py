"""Shared probe ownership stays within the integration's hass.data namespace."""

from __future__ import annotations

import asyncio

from homeassistant.core import HomeAssistant
import pytest

from custom_components.tuya_ble.config_flow_state import probe_state
from custom_components.tuya_ble.const import DOMAIN


@pytest.mark.parametrize("existing_domain", [False, True])
async def test_probe_state_initialises_inside_domain(
    hass: HomeAssistant, existing_domain: bool
) -> None:
    """Initialisation preserves entry data and unrelated integration data."""
    entry_data = object()
    unrelated = object()
    hass.data["unrelated_integration"] = unrelated
    if existing_domain:
        hass.data[DOMAIN] = {"entry-id": entry_data}

    lock, claims = probe_state(hass)

    assert isinstance(lock, asyncio.Lock)
    async with lock:
        assert lock.locked()
    assert claims == {}
    assert hass.data[DOMAIN]["probe_state"] == {"lock": lock, "claims": claims}
    assert f"{DOMAIN}_probe_state" not in hass.data
    assert hass.data["unrelated_integration"] is unrelated
    if existing_domain:
        assert hass.data[DOMAIN]["entry-id"] is entry_data


async def test_probe_state_reuses_lock_and_claims(hass: HomeAssistant) -> None:
    """Concurrent setup flows share the same lock and retain their claims."""
    first_lock, first_claims = probe_state(hass)
    claim = (object(), "AA:BB:CC:DD:EE:FF", 123.0)
    first_claims["device-id"] = claim

    second_lock, second_claims = probe_state(hass)

    assert second_lock is first_lock
    async with first_lock:
        assert second_lock.locked()
    assert second_claims is first_claims
    assert second_claims["device-id"] is claim
