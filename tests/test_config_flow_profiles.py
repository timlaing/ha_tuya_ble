"""Automatic v4 config-flow validation and persisted offline restart data."""

# pylint: disable=protected-access
from __future__ import annotations

import asyncio
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tuya_ble import _build_credentials_from_entry
from custom_components.tuya_ble.config_flow import TuyaBLEConfigFlow
from custom_components.tuya_ble.config_flow_state import probe_state as _probe_state
from custom_components.tuya_ble.const import (
    CONF_CONNECTION_PROFILE,
    CONF_DEVICE_ID,
    DOMAIN,
)
from custom_components.tuya_ble.tuya_ble import (
    ConnectionProfile,
    ProbeOutcome,
    decode_tuya_ble_advertisement,
)
from tests.conftest import make_credentials, make_device
from tests.test_config_flow import FakeDiscovery, FakeManager, build_flow
from tests.test_config_flow_v4 import discovery

DETECT = "custom_components.tuya_ble.config_flow.async_detect_v4_profile"


async def setup_flow(
    hass: HomeAssistant, profile: ConnectionProfile | None
) -> tuple[TuyaBLEConfigFlow, dict[str, Any]]:
    """Complete the existing device form followed by automatic verification."""
    flow = build_flow(hass)
    info = discovery()
    info.device = make_device()._ble_device
    flow.discovery_info = cast(Any, info)
    flow._manager = cast(Any, FakeManager(make_credentials()))
    with (
        patch.object(flow, "_async_scan_device", return_value=info),
        patch(DETECT, new=AsyncMock(return_value=profile)) as detect,
    ):
        result = await flow.async_step_discovered_device({})
        assert result["type"] in (
            FlowResultType.SHOW_PROGRESS,
            FlowResultType.SHOW_PROGRESS_DONE,
        )
        assert result["progress_action"] == "verify_device"
        assert flow._probe_task is not None
        assert flow._probe_task is not None
        await flow._probe_task
        progress = await flow.async_step_probe()
        assert progress["type"] == FlowResultType.SHOW_PROGRESS_DONE
        result = await flow.async_step_probe_finish()
        assert detect.call_count == 1
    return flow, cast(dict[str, Any], result)


@pytest.mark.parametrize("profile", list(ConnectionProfile))
async def test_automatic_setup_persists_verified_profile(
    hass: HomeAssistant, profile: ConnectionProfile
) -> None:
    """No user setting is needed and startup receives the selected profile."""
    _, result = await setup_flow(hass, profile)
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_CONNECTION_PROFILE] == profile.value
    entry = MockConfigEntry(domain=DOMAIN, data=result["data"])
    credentials = _build_credentials_from_entry(entry)
    assert credentials.connection_profile == profile
    device = make_device()
    device.initialize_with_credentials(credentials)
    assert device.connection_profile == profile
    assert device._protocol_version == 4


async def test_unverified_profile_never_creates_entry(hass: HomeAssistant) -> None:
    """A cloud match alone no longer creates an unavailable v4 config entry."""
    _, result = await setup_flow(hass, None)
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "v4_verification_failed"}


async def test_active_claim_blocks_duplicate_verification(hass: HomeAssistant) -> None:
    """Two setup flows cannot simultaneously claim the same cloud record."""
    flow = build_flow(hass)
    info = discovery()
    info.device = make_device()._ble_device
    credentials = make_credentials()
    _probe_state(hass)[1][credentials.device_id] = (
        object(),
        info.address,
        float("inf"),
    )
    with patch(DETECT, new=AsyncMock()) as detect:
        result = await flow._async_verify_v4(cast(Any, info), credentials)
    assert result == (None, None, "device_verification_expired")
    detect.assert_not_called()


async def configured_flow(
    hass: HomeAssistant,
) -> tuple[TuyaBLEConfigFlow, MockConfigEntry]:
    """Build an existing entry retaining all credential/schema fields."""
    _, result = await setup_flow(hass, ConnectionProfile.V4_20)
    _probe_state(hass)[1].clear()
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**result["data"], "preserved_schema": {"value": 123}},
        title="Shelf Shade",
        unique_id=discovery().address,
    )
    entry.add_to_hass(hass)
    flow = build_flow(hass)
    flow.context = {"source": "reconfigure", "entry_id": entry.entry_id}
    return flow, entry


async def test_reconfigure_updates_existing_entry_and_reloads(
    hass: HomeAssistant,
) -> None:
    """Existing shades can learn a profile without removal or entity-ID changes."""
    flow, entry = await configured_flow(hass)
    old_data = dict(entry.data)
    info = discovery()
    info.device = make_device()._ble_device
    with (
        patch.object(flow, "_async_scan_device", return_value=info),
        patch(DETECT, new=AsyncMock(return_value=ConnectionProfile.V2_MTU)),
        patch.object(hass.config_entries, "async_unload", return_value=True) as unload,
        patch.object(
            hass.config_entries, "async_reload", return_value=True
        ) as reload_entry,
    ):
        form = await flow.async_step_reconfigure()
        assert form["type"] == FlowResultType.FORM
        result = await flow.async_step_reconfigure({})
        assert result["type"] in (
            FlowResultType.SHOW_PROGRESS,
            FlowResultType.SHOW_PROGRESS_DONE,
        )
        assert flow._probe_task is not None
        await flow._probe_task
        result = await flow.async_step_probe_finish()
        await hass.async_block_till_done()
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.unique_id == info.address
    assert entry.data == {**old_data, CONF_CONNECTION_PROFILE: "v2_mtu"}
    assert entry.data[CONF_DEVICE_ID] == old_data[CONF_DEVICE_ID]
    unload.assert_awaited_once_with(entry.entry_id)
    reload_entry.assert_awaited_once_with(entry.entry_id)
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_reconfigure_failure_restores_old_runtime(hass: HomeAssistant) -> None:
    """Failed detection preserves the last good profile and reloads the entry."""
    flow, entry = await configured_flow(hass)
    old_data = dict(entry.data)
    info = discovery()
    info.device = make_device()._ble_device
    with (
        patch.object(flow, "_async_scan_device", return_value=info),
        patch(DETECT, new=AsyncMock(return_value=None)),
        patch.object(hass.config_entries, "async_unload", return_value=True),
        patch.object(
            hass.config_entries, "async_reload", return_value=True
        ) as reload_entry,
    ):
        await flow.async_step_reconfigure({})
        # Repeated submissions reuse the in-progress verification.
        result = await flow.async_step_reconfigure({})
        assert result["type"] in (
            FlowResultType.SHOW_PROGRESS,
            FlowResultType.SHOW_PROGRESS_DONE,
        )
        assert flow._probe_task is not None
        await flow._probe_task
        result = await flow.async_step_probe_finish()
    assert result["errors"] == {"base": "v4_verification_failed"}
    assert entry.data == old_data
    reload_entry.assert_awaited_once_with(entry.entry_id)


@pytest.mark.parametrize("failure", ["scan", "identity", "version", "unload"])
async def test_reconfigure_stops_before_probing_unsafe_input(
    hass: HomeAssistant, failure: str
) -> None:
    """Unavailable/unsupported devices never start a compatibility probe."""
    flow, entry = await configured_flow(hass)
    info = discovery(5 if failure == "identity" else 4)
    if failure == "version":
        info = FakeDiscovery()
    with (
        patch.object(
            flow, "_async_scan_device", return_value=None if failure == "scan" else info
        ),
        patch.object(hass.config_entries, "async_unload", return_value=False),
        patch(DETECT, new=AsyncMock()) as detect,
    ):
        result = await flow.async_step_reconfigure({})
    detect.assert_not_called()
    assert result["type"] == (
        FlowResultType.ABORT if failure == "version" else FlowResultType.FORM
    )
    assert entry.data[CONF_CONNECTION_PROFILE] == "v4_20"


async def test_cancelled_reconfigure_restores_runtime_after_probe(
    hass: HomeAssistant,
) -> None:
    """Removing the progress flow releases its task before restoring runtime."""
    flow, entry = await configured_flow(hass)
    info = discovery()
    info.device = make_device()._ble_device
    started = asyncio.Event()
    ended = asyncio.Event()

    async def detect(*_args: Any) -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            ended.set()

    async def reload_entry(_entry_id: str) -> bool:
        assert ended.is_set()
        return True

    with (
        patch.object(flow, "_async_scan_device", return_value=info),
        patch(DETECT, side_effect=detect),
        patch.object(hass.config_entries, "async_unload", return_value=True),
        patch.object(
            hass.config_entries, "async_reload", side_effect=reload_entry
        ) as reload_mock,
    ):
        await flow.async_step_reconfigure({})
        await started.wait()
        flow.async_remove()
        await hass.async_block_till_done()
    assert ended.is_set()
    reload_mock.assert_awaited_once_with(entry.entry_id)


async def test_expired_v4_claim_does_not_create_entry(hass: HomeAssistant) -> None:
    """Verification remains subject to the existing atomic claim checks."""
    flow = build_flow(hass)
    info = discovery()
    info.device = make_device()._ble_device
    flow.discovery_info = cast(Any, info)
    flow._manager = cast(Any, FakeManager(make_credentials()))
    with (
        patch.object(flow, "_async_scan_device", return_value=info),
        patch(DETECT, new=AsyncMock(return_value=ConnectionProfile.V2_20)),
    ):
        await flow.async_step_discovered_device({})
        assert flow._probe_task is not None
        await flow._probe_task
        _probe_state(hass)[1].clear()
        result = await flow.async_step_probe_finish()
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "device_verification_expired"}


@pytest.mark.parametrize("profile", [None, ConnectionProfile.V2_MTU])
async def test_v4_fallback_verifies_uuid_and_keeps_selected_profile(
    hass: HomeAssistant, profile: ConnectionProfile | None
) -> None:
    """Candidate matching uses the same v4 profiles rather than classic probes."""
    flow = build_flow(hass)
    info = discovery()
    advertisement = decode_tuya_ble_advertisement(cast(Any, info.advertisement))
    assert advertisement is not None
    credentials = make_credentials(uuid="fedcba0987654321")
    with patch(DETECT, return_value=profile) as detector:
        result = await flow._async_probe_cloud_candidate(
            cast(Any, info), advertisement, credentials
        )
    assert result == (
        (ProbeOutcome.MATCHED, advertisement.uuid)
        if profile
        else (ProbeOutcome.INCONCLUSIVE, None)
    )
    assert detector.call_count == (1 if profile else 2)
    assert flow._verified_profile == profile
