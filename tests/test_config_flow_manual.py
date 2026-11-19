"""Manual credential entry is a verified fallback to the QR-code login."""

# pylint: disable=protected-access

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
import logging
import time
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import AbortFlow, FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tuya_ble.config_flow import TuyaBLEConfigFlow
from custom_components.tuya_ble.config_flow_manual import (
    parse_manual_credentials,
    validate_key_pair,
)
from custom_components.tuya_ble.config_flow_state import probe_state
from custom_components.tuya_ble.const import (
    CONF_CATEGORY,
    CONF_CLOUD_UUID,
    CONF_CONNECTION_PROFILE,
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_LOCAL_KEY,
    CONF_PRODUCT_ID,
    CONF_SEC_KEY,
    CONF_UUID,
    DOMAIN,
)
from custom_components.tuya_ble.tuya_ble import (
    MANUFACTURER_DATA_ID,
    SERVICE_UUID,
    ConnectionProfile,
    ProbeOutcome,
)
from tests import test_advertisement
from tests.conftest import FakeAdvertisementData
from tests.test_config_flow import FakeDiscovery, build_flow
from tests.test_config_flow_v4 import discovery as v4_discovery

MANUAL = "custom_components.tuya_ble.config_flow_manual"
FLOW = "custom_components.tuya_ble.config_flow"
ADDRESS = "AA:BB:CC:DD:EE:FF"
ADVERTISED_UUID = "1234567890abcdef"
LOCAL_KEY = "0123456789abcdef"
SEC_KEY = "fedcba9876543210"
V2_SERVICE = b"\x41\x04\x00\x08abcdefgh"


def manual_input(**overrides: str | None) -> dict[str, Any]:
    """Build a manual form submission; ``None`` omits a field."""
    data: dict[str, str | None] = {
        CONF_ADDRESS: ADDRESS,
        CONF_UUID: f" {ADVERTISED_UUID} ",
        CONF_LOCAL_KEY: LOCAL_KEY,
        CONF_DEVICE_ID: "device123",
        CONF_CATEGORY: "wk",
        CONF_PRODUCT_ID: "drlajpqc",
        CONF_DEVICE_NAME: "Thermostat",
    }
    data.update(overrides)
    return {key: value for key, value in data.items() if value is not None}


def discovered_flow(
    hass: HomeAssistant, discovery: Any | None = None
) -> tuple[TuyaBLEConfigFlow, Any]:
    """Build a flow started by Bluetooth discovery, without a cloud manager."""
    flow = build_flow(hass)
    info = discovery if discovery is not None else FakeDiscovery()
    flow.discovery_info = cast(Any, info)
    return flow, info


@contextmanager
def verification(
    flow: TuyaBLEConfigFlow,
    discovery: Any,
    outcome: ProbeOutcome = ProbeOutcome.MATCHED,
    mapped: frozenset[int] = frozenset({1, 2}),
) -> Iterator[AsyncMock]:
    """Mock scanning, the registry and the authenticated Bluetooth read."""
    with (
        patch.object(flow, "_async_scan_device", return_value=discovery),
        patch(f"{MANUAL}.async_setup_registry", new_callable=AsyncMock),
        patch(f"{FLOW}.async_setup_registry", new_callable=AsyncMock),
        patch(f"{MANUAL}.get_mapped_dp_ids", return_value=mapped),
        patch(f"{FLOW}.get_mapped_dp_ids", return_value=mapped),
        patch(
            f"{FLOW}.async_probe_authenticated_status",
            new_callable=AsyncMock,
            return_value=outcome,
        ) as probe,
    ):
        yield probe


async def submit(
    flow: TuyaBLEConfigFlow, user_input: dict[str, Any]
) -> ConfigFlowResult:
    """Submit the manual form and run any started verification to completion."""
    result = await flow.async_step_manual(user_input)
    if result["type"] != FlowResultType.SHOW_PROGRESS:
        return result
    assert flow._probe_task is not None
    await flow._probe_task
    done = await flow.async_step_probe()
    assert done["type"] == FlowResultType.SHOW_PROGRESS_DONE
    return await flow.async_step_probe_finish()


def suggestions(result: ConfigFlowResult) -> dict[str, Any]:
    """Return the values a form suggests to the frontend."""
    return {
        str(key.schema): key.description["suggested_value"]
        for key in cast(Any, result["data_schema"]).schema
        if key.description and "suggested_value" in key.description
    }


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def test_parse_manual_credentials_strips_and_omits_empty_optionals() -> None:
    """Whitespace is trimmed, and empty optional fields stay unset."""
    credentials, errors = parse_manual_credentials(
        manual_input(**{CONF_SEC_KEY: "  ", CONF_DEVICE_NAME: ""})
    )
    assert errors == {}
    assert credentials is not None
    assert credentials.uuid == ADVERTISED_UUID
    assert credentials.local_key == LOCAL_KEY
    assert credentials.sec_key is None
    assert credentials.device_name is None
    assert credentials.functions is None
    assert LOCAL_KEY not in str(credentials)


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        (CONF_UUID, "", "invalid_uuid"),
        (CONF_UUID, "x" * 17, "invalid_uuid"),
        (CONF_UUID, "has space", "invalid_uuid"),
        (CONF_LOCAL_KEY, "short", "invalid_local_key"),
        (CONF_LOCAL_KEY, "spaced key 123", "invalid_local_key"),
        (CONF_LOCAL_KEY, None, "invalid_local_key"),
        (CONF_SEC_KEY, "tooshort", "invalid_sec_key"),
        (CONF_SEC_KEY, "sixteen chars ok", "invalid_sec_key"),
        (CONF_DEVICE_ID, "d" * 21, "invalid_device_id"),
        (CONF_CATEGORY, "", "invalid_category"),
        (CONF_PRODUCT_ID, "p" * 33, "invalid_product_id"),
    ],
)
def test_parse_manual_credentials_reports_each_invalid_field(
    field: str, value: str | None, error: str
) -> None:
    """Every invalid field is reported, and no credentials are built."""
    credentials, errors = parse_manual_credentials(manual_input(**{field: value}))
    assert credentials is None
    assert errors == {field: error}


def test_validate_key_pair_keeps_a_valid_security_key() -> None:
    """A valid security key forms a pair with the local key."""
    keys, errors = validate_key_pair({
        CONF_LOCAL_KEY: LOCAL_KEY,
        CONF_SEC_KEY: SEC_KEY,
    })
    assert errors == {}
    assert keys == (LOCAL_KEY, SEC_KEY)


# --------------------------------------------------------------------------
# Form
# --------------------------------------------------------------------------


async def test_manual_form_offers_only_the_discovered_device(
    hass: HomeAssistant,
) -> None:
    """A discovery flow offers its own device and never prefills a key."""
    flow, info = discovered_flow(hass)
    with patch.object(flow, "_async_refresh_discovered_devices") as refresh:
        result = await flow.async_step_manual()
    refresh.assert_not_called()
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "manual"
    schema = cast(Any, result["data_schema"])
    assert list(schema.schema[CONF_ADDRESS].container) == [info.address]
    assert suggestions(result) == {CONF_ADDRESS: info.address}
    # The flow manager validates submissions with this schema.
    assert schema(manual_input())[CONF_ADDRESS] == ADDRESS
    assert CONF_SEC_KEY not in schema(manual_input())


async def test_manual_form_lists_unconfigured_devices(hass: HomeAssistant) -> None:
    """Without discovery, every unconfigured Tuya BLE device can be chosen."""
    flow = build_flow(hass)
    first, second = FakeDiscovery(), FakeDiscovery("11:22:33:44:55:66", name="")

    async def refresh() -> None:
        flow._discovered_devices = {
            info.address: cast(Any, info) for info in (first, second)
        }

    with patch.object(flow, "_async_refresh_discovered_devices", side_effect=refresh):
        result = await flow.async_step_manual()
    choices = cast(Any, result["data_schema"]).schema[CONF_ADDRESS].container
    assert choices == {
        first.address: f"FakeDevice ({first.address})",
        second.address: f"{second.address} ({second.address})",
    }


async def test_manual_form_aborts_without_devices(hass: HomeAssistant) -> None:
    """Manual entry still needs a Bluetooth device to verify against."""
    flow = build_flow(hass)
    with patch.object(
        flow, "_async_refresh_discovered_devices", new_callable=AsyncMock
    ):
        result = await flow.async_step_manual()
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "no_unconfigured_devices"


async def test_invalid_manual_input_is_not_scanned(hass: HomeAssistant) -> None:
    """Field errors are shown without Bluetooth access, and keys are not echoed."""
    flow, _ = discovered_flow(hass)
    with patch.object(flow, "_async_scan_device") as scan:
        result = await flow.async_step_manual(
            manual_input(**{CONF_UUID: "", CONF_SEC_KEY: SEC_KEY})
        )
    scan.assert_not_called()
    assert result["errors"] == {CONF_UUID: "invalid_uuid"}
    suggested = suggestions(result)
    assert suggested[CONF_DEVICE_ID] == "device123"
    assert CONF_LOCAL_KEY not in suggested
    assert CONF_SEC_KEY not in suggested


# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------


async def test_verified_manual_credentials_create_an_entry(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """An authenticated read proves the entered credentials before saving."""
    flow, info = discovered_flow(hass)
    assert flow._manager is None
    with verification(flow, info) as probe, caplog.at_level(logging.DEBUG):
        result = await submit(flow, manual_input(**{CONF_SEC_KEY: SEC_KEY}))
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Thermostat"
    data = cast(dict[str, Any], result["data"])
    assert data[CONF_ADDRESS] == ADDRESS
    assert data[CONF_UUID] == ADVERTISED_UUID
    assert data[CONF_CLOUD_UUID] == ADVERTISED_UUID
    assert data[CONF_LOCAL_KEY] == LOCAL_KEY
    assert data[CONF_DEVICE_ID] == "device123"
    assert (data[CONF_CATEGORY], data[CONF_PRODUCT_ID]) == ("wk", "drlajpqc")
    # A classic advertisement never announces Security V2, so no key is kept.
    assert CONF_SEC_KEY not in data
    probe.assert_awaited_once()
    assert probe.await_args is not None
    assert probe.await_args.args[3] == ADVERTISED_UUID
    assert probe.await_args.args[4] == frozenset({1, 2})
    assert "verifying manually entered credentials" in caplog.text
    assert LOCAL_KEY not in caplog.text
    assert SEC_KEY not in caplog.text


async def test_advertised_uuid_is_tried_before_the_entered_one(
    hass: HomeAssistant,
) -> None:
    """A differing entered UUID is kept as the cloud UUID once verified."""
    flow, info = discovered_flow(hass)
    with verification(flow, info) as probe:
        probe.side_effect = lambda *args: (
            ProbeOutcome.MATCHED if args[3] == "entereduuid" else ProbeOutcome.REJECTED
        )
        result = await submit(flow, manual_input(**{CONF_UUID: "entereduuid"}))
    data = cast(dict[str, Any], result["data"])
    assert [call.args[3] for call in probe.await_args_list] == [
        ADVERTISED_UUID,
        "entereduuid",
    ]
    assert data[CONF_UUID] == "entereduuid"
    assert data[CONF_CLOUD_UUID] == "entereduuid"


@pytest.mark.parametrize(
    ("outcome", "error"),
    [
        (ProbeOutcome.REJECTED, "manual_credentials_rejected"),
        (ProbeOutcome.INCONCLUSIVE, "bluetooth_probe_failed"),
        (ProbeOutcome.RETRYABLE, "bluetooth_probe_failed"),
    ],
)
async def test_unverified_manual_credentials_return_to_the_form(
    hass: HomeAssistant, outcome: ProbeOutcome, error: str
) -> None:
    """A failed read saves nothing and asks again without echoing the keys."""
    flow, info = discovered_flow(hass)
    with verification(flow, info, outcome):
        result = await submit(flow, manual_input(**{CONF_SEC_KEY: SEC_KEY}))
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "manual"
    assert result["errors"] == {"base": error}
    suggested = suggestions(result)
    assert suggested[CONF_UUID] == f" {ADVERTISED_UUID} "
    assert LOCAL_KEY not in suggested.values()
    assert SEC_KEY not in suggested.values()
    assert probe_state(hass)[1] == {}
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_unknown_product_cannot_be_verified(hass: HomeAssistant) -> None:
    """Without a descriptor, no expected data points can prove a match."""
    flow, info = discovered_flow(hass)
    with verification(flow, info, mapped=frozenset()) as probe:
        result = await submit(flow, manual_input())
    probe.assert_not_awaited()
    assert result["errors"] == {"base": "unknown_product"}


@pytest.mark.parametrize(
    ("scanned", "error"),
    [(None, "bluetooth_scan_failed"), ("undecodable", "identity_not_decoded")],
)
async def test_scan_failures_return_to_the_form(
    hass: HomeAssistant, scanned: str | None, error: str
) -> None:
    """The selected device must be scanned and identified before any login."""
    flow, info = discovered_flow(hass)
    scan_result: Any = None
    if scanned is not None:
        scan_result = FakeDiscovery()
        scan_result.advertisement = FakeAdvertisementData(
            service_data={SERVICE_UUID: b"\x00prod"},
            manufacturer_data={MANUFACTURER_DATA_ID: b"\x80\x02\x00"},
        )
    with (
        verification(flow, info) as probe,
        patch.object(flow, "_async_scan_device", return_value=scan_result),
    ):
        result = await submit(flow, manual_input())
    probe.assert_not_awaited()
    assert result["errors"] == {"base": error}


async def test_device_id_of_existing_entry_aborts(hass: HomeAssistant) -> None:
    """Credentials of an already configured device cannot add it twice."""
    MockConfigEntry(
        domain=DOMAIN, unique_id="11:22:33:44:55:66", data={CONF_DEVICE_ID: "device123"}
    ).add_to_hass(hass)
    flow, info = discovered_flow(hass)
    with verification(flow, info) as probe:
        result = await submit(flow, manual_input())
    probe.assert_not_awaited()
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_configured_address_aborts(hass: HomeAssistant) -> None:
    """The Bluetooth address stays the entry's unique ID."""
    MockConfigEntry(domain=DOMAIN, unique_id=ADDRESS, data={}).add_to_hass(hass)
    flow, info = discovered_flow(hass)
    flow.handler = DOMAIN
    user_input = manual_input()
    with verification(flow, info) as probe, pytest.raises(AbortFlow):
        await flow.async_step_manual(user_input)
    probe.assert_not_awaited()


async def test_device_claimed_by_another_flow_is_not_probed(
    hass: HomeAssistant,
) -> None:
    """A live reservation from another flow blocks a second verification."""
    claims = probe_state(hass)[1]
    other = (object(), "11:22:33:44:55:66", time.monotonic() + 60)
    claims["device123"] = other
    flow, info = discovered_flow(hass)
    with verification(flow, info) as probe:
        result = await submit(flow, manual_input())
    probe.assert_not_awaited()
    assert result["errors"] == {"base": "device_verification_expired"}
    assert claims == {"device123": other}


async def test_pending_verification_is_resumed(hass: HomeAssistant) -> None:
    """A second submission shows the running check instead of starting another."""
    flow, info = discovered_flow(hass)
    release = asyncio.Event()

    async def held_probe(*_args: Any) -> ProbeOutcome:
        await release.wait()
        return ProbeOutcome.MATCHED

    with verification(flow, info) as probe:
        probe.side_effect = held_probe
        first = await flow.async_step_manual(manual_input())
        task = flow._probe_task
        second = await flow.async_step_manual(manual_input())
        assert flow._probe_task is task is not None
        release.set()
        await task
    assert first["type"] == second["type"] == FlowResultType.SHOW_PROGRESS
    probe.assert_awaited_once()


@pytest.mark.parametrize(
    ("profile", "step"),
    [(ConnectionProfile.V4_20, None), (None, "unverified")],
)
async def test_protocol_v4_uses_the_profile_verification(
    hass: HomeAssistant, profile: ConnectionProfile | None, step: str | None
) -> None:
    """A v4 device is verified like a cloud match and may be added unverified."""
    flow, info = discovered_flow(hass, v4_discovery())
    with (
        verification(flow, info) as probe,
        patch(f"{FLOW}.async_detect_v4_profile", return_value=profile) as detect,
    ):
        result = await submit(flow, manual_input())
    probe.assert_not_awaited()
    assert detect.await_args is not None
    assert detect.await_args.args[2].local_key == LOCAL_KEY
    if step is None:
        assert result["type"] == FlowResultType.CREATE_ENTRY
        data = cast(dict[str, Any], result["data"])
        assert data[CONF_CONNECTION_PROFILE] == ConnectionProfile.V4_20.value
    else:
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == step


async def test_security_v2_device_keeps_the_entered_security_key(
    hass: HomeAssistant,
) -> None:
    """The entered security key is stored only for a Security V2 device."""
    info = v4_discovery()
    adv = test_advertisement.v4_advertisement(V2_SERVICE)
    info.service_data = adv.service_data
    info.manufacturer_data = adv.manufacturer_data
    info.advertisement = cast(Any, adv)
    flow, _ = discovered_flow(hass, info)
    with (
        verification(flow, info),
        patch(f"{FLOW}.async_detect_v4_profile", return_value=ConnectionProfile.V2_APP),
    ):
        result = await submit(flow, manual_input(**{CONF_SEC_KEY: SEC_KEY}))
    assert cast(dict[str, Any], result["data"])[CONF_SEC_KEY] == SEC_KEY


async def test_bluetooth_discovery_reaches_manual_entry(hass: HomeAssistant) -> None:
    """A discovered device can be set up manually from the user menu."""
    flow = build_flow(hass)
    menu = await flow.async_step_bluetooth(cast(Any, FakeDiscovery()))
    assert menu["type"] == FlowResultType.MENU
    assert "manual" in menu["menu_options"]
    result = await flow.async_step_manual()
    assert result["step_id"] == "manual"
    assert suggestions(result) == {CONF_ADDRESS: ADDRESS}
