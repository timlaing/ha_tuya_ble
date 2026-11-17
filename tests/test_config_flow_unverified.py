"""Adding a protocol-4 device whose Bluetooth verification failed."""

# pylint: disable=protected-access
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import AbortFlow, FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tuya_ble.config_flow import TuyaBLEConfigFlow
from custom_components.tuya_ble.config_flow_unverified import unverified_error
from custom_components.tuya_ble.const import (
    CONF_CONNECTION_PROFILE,
    CONF_DEVICE_ID,
    CONF_SEC_KEY,
    DOMAIN,
)
from custom_components.tuya_ble.tuya_ble import ConnectionProfile, ProbeStage
from tests import test_advertisement
from tests.conftest import make_credentials, make_device
from tests.test_config_flow import FakeManager, build_flow
from tests.test_config_flow_v4 import discovery

DETECT = "custom_components.tuya_ble.config_flow.async_detect_v4_profile"
V2_SERVICE = b"\x41\x04\x00\x08abcdefgh"
SEC_KEY = "fedcba9876543210"


def _discovery(service: bytes | None = None) -> Any:
    info = discovery()
    if service is not None:
        adv = test_advertisement.v4_advertisement(service)
        info.service_data = adv.service_data
        info.manufacturer_data = adv.manufacturer_data
        info.advertisement = cast(Any, adv)
    info.device = make_device()._ble_device
    return info


async def _fail_verification(
    hass: HomeAssistant,
    *,
    source: str = "discovered_device",
    service: bytes | None = None,
) -> tuple[TuyaBLEConfigFlow, Any, dict[str, Any]]:
    """Run setup until verification has failed and the unverified form shows."""
    flow = build_flow(hass)
    info = _discovery(service)
    credentials = replace(
        make_credentials(local_key="0123456789abcdef"), sec_key=SEC_KEY
    )
    flow._manager = cast(Any, FakeManager(credentials))
    if source == "discovered_device":
        flow.discovery_info = cast(Any, info)
    with (
        patch.object(flow, "_async_scan_device", return_value=info),
        patch(DETECT, new=AsyncMock(return_value=None)),
    ):
        if source == "discovered_device":
            result = await flow.async_step_discovered_device({})
        else:
            result = await flow.async_step_device({CONF_ADDRESS: info.address})
        assert result["type"] == FlowResultType.SHOW_PROGRESS
        assert flow._probe_task is not None
        await flow._probe_task
        result = await flow.async_step_probe_finish()
    return flow, info, cast(dict[str, Any], result)


async def test_failed_verification_shows_error_with_add_option(
    hass: HomeAssistant,
) -> None:
    """The failure is reported and adding stays opt-in."""
    flow, info, result = await _fail_verification(hass)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "unverified"
    assert result["errors"] == {"base": "v4_verification_failed"}
    assert result["description_placeholders"] == {
        CONF_ADDRESS: info.address,
        "name": info.name,
    }
    # Submitting adds the device; cancelling the flow is the only way out.
    assert not cast(Any, result["data_schema"]).schema

    shown = await flow.async_step_unverified()
    assert shown["step_id"] == "unverified"
    assert shown["errors"] == {"base": "v4_verification_failed"}


@pytest.mark.parametrize(
    ("service", "stored", "source"),
    [(V2_SERVICE, True, "discovered_device"), (None, False, "device")],
)
async def test_submit_adds_unverified_entry_without_profile(
    hass: HomeAssistant, service: bytes | None, stored: bool, source: str
) -> None:
    """Submitting the form adds the device with no profile, keeping a V2 sec_key."""
    flow, _, _ = await _fail_verification(hass, source=source, service=service)
    result = await flow.async_step_unverified({})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    data = cast(dict[str, Any], result["data"])
    assert CONF_CONNECTION_PROFILE not in data
    assert (data.get(CONF_SEC_KEY) == SEC_KEY) is stored
    assert flow._unverified_setup is None


@pytest.mark.parametrize("profile", [ConnectionProfile.V4_20])
async def test_unverified_entry_is_not_offered_after_success(
    hass: HomeAssistant, profile: ConnectionProfile
) -> None:
    """A verified device is created directly, never through the unverified step."""
    flow = build_flow(hass)
    info = _discovery()
    flow.discovery_info = cast(Any, info)
    flow._manager = cast(Any, FakeManager(make_credentials()))
    with (
        patch.object(flow, "_async_scan_device", return_value=info),
        patch(DETECT, new=AsyncMock(return_value=profile)),
    ):
        await flow.async_step_discovered_device({})
        assert flow._probe_task is not None
        await flow._probe_task
        result = await flow.async_step_probe_finish()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_CONNECTION_PROFILE] == profile.value
    assert flow._unverified_setup is None


async def test_unverified_step_without_pending_setup_aborts(
    hass: HomeAssistant,
) -> None:
    """The step cannot be reached without a failed verification."""
    result = await build_flow(hass).async_step_unverified({})
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "unknown"


async def test_add_unverified_rejects_configured_cloud_device(
    hass: HomeAssistant,
) -> None:
    """A cloud device already configured elsewhere is not added twice."""
    flow, _, _ = await _fail_verification(hass)
    MockConfigEntry(
        domain=DOMAIN,
        unique_id="11:22:33:44:55:66",
        data={CONF_DEVICE_ID: make_credentials().device_id},
    ).add_to_hass(hass)
    result = await flow.async_step_unverified({})
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_add_unverified_stops_after_flow_removal(hass: HomeAssistant) -> None:
    """A removed flow never creates the entry."""
    flow, _, _ = await _fail_verification(hass)
    flow._flow_removed = True
    with pytest.raises(AbortFlow):
        await flow.async_step_unverified({})


def test_every_stage_has_a_translated_error() -> None:
    """Each failure reason shown to the user has text in both string files."""
    for path in ("strings.json", "translations/en.json"):
        strings = json.loads(
            (Path("custom_components/tuya_ble") / path).read_text(encoding="utf-8")
        )
        errors = strings["config"]["error"]
        for stage in ProbeStage:
            assert unverified_error([stage]) in errors
        assert unverified_error([]) in errors


@pytest.mark.parametrize(
    ("stages", "error"),
    [
        ([], "v4_verification_failed"),
        ([ProbeStage.NOTIFY], "v4_failed_connect"),
        ([ProbeStage.CONNECT, ProbeStage.DEVICE_INFO], "v4_failed_no_reply"),
        ([ProbeStage.DATAPOINTS, ProbeStage.NOTIFY], "v4_failed_no_data"),
        ([ProbeStage.UNBOUND], "v4_failed_not_added"),
        ([ProbeStage.PAIR, ProbeStage.UNSUPPORTED], "v4_failed_login"),
    ],
)
def test_furthest_stage_explains_the_failure(
    stages: list[ProbeStage], error: str
) -> None:
    """The attempt that got furthest gives the most useful explanation."""
    assert unverified_error(stages) == error


async def _detect_failing_at(*_args: Any, stages: list[ProbeStage], **_kw: Any) -> None:
    stages.extend((ProbeStage.CONNECT, ProbeStage.DEVICE_INFO))


async def test_unverified_form_shows_why_verification_failed(
    hass: HomeAssistant,
) -> None:
    """The add-anyway page shows the reason from the verification step."""
    flow = build_flow(hass)
    info = _discovery()
    flow.discovery_info = cast(Any, info)
    flow._manager = cast(Any, FakeManager(make_credentials()))
    with (
        patch.object(flow, "_async_scan_device", return_value=info),
        patch(DETECT, side_effect=_detect_failing_at),
    ):
        await flow.async_step_discovered_device({})
        assert flow._probe_task is not None
        await flow._probe_task
        result = await flow.async_step_probe_finish()
    assert result["step_id"] == "unverified"
    assert result["errors"] == {"base": "v4_failed_no_reply"}
    shown = await flow.async_step_unverified()
    assert shown["errors"] == {"base": "v4_failed_no_reply"}
