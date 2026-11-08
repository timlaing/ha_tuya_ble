"""FD50 discovery reaches version-aware identity decoding and cloud matching."""

# pylint: disable=protected-access

import asyncio
from typing import Any, cast
from unittest.mock import patch

from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest

from custom_components.tuya_ble.tuya_ble import SERVICE_UUID_V4
from tests.conftest import make_credentials
from tests.test_advertisement import v4_advertisement
from tests.test_config_flow import FakeDiscovery, FakeManager, build_flow


def discovery(version: int = 4) -> FakeDiscovery:
    """Build a synthetic FD50 scan response without real Bluetooth I/O."""
    info = FakeDiscovery()
    adv = v4_advertisement(bytes((version << 4 | 9, 0, 0, 8)) + b"abcdefgh")
    info.service_data = adv.service_data
    info.manufacturer_data = adv.manufacturer_data
    info.advertisement = cast(Any, adv)
    return info


@pytest.mark.parametrize("version", [4, 5])
async def test_fd50_setup(hass: HomeAssistant, version: int) -> None:
    """V4 matches by UUID; v5 cannot complete the active identity scan."""
    flow = build_flow(hass)
    flow.context = {}
    info = discovery(version)
    flow.discovery_info = cast(Any, info)
    manager = FakeManager(make_credentials())
    flow._manager = cast(Any, manager)

    async def process_advertisements(
        _hass: HomeAssistant,
        predicate: Any,
        _matcher: Any,
        _mode: Any,
        _timeout: int,
    ) -> FakeDiscovery:
        assert predicate(info) is (version == 4)
        incomplete = discovery(version)
        incomplete.service_data = {SERVICE_UUID_V4: b"\x41"}
        assert not predicate(incomplete)
        incomplete = discovery(version)
        incomplete.manufacturer_data = {2000: bytes(22)}
        assert not predicate(incomplete)
        incomplete.manufacturer_data = {}
        assert not predicate(incomplete)
        beacon = discovery()
        beacon_adv = v4_advertisement(b"\x48\x00\x00\x08abcdefgh")
        beacon.service_data = beacon_adv.service_data
        beacon.manufacturer_data = beacon_adv.manufacturer_data
        beacon.advertisement = cast(Any, beacon_adv)
        assert not predicate(beacon)
        malformed = discovery()
        malformed_adv = v4_advertisement(plaintext=bytes(16))
        malformed.advertisement = cast(Any, malformed_adv)
        assert not predicate(malformed)
        if version != 4:
            raise TimeoutError
        return info

    with patch(
        "custom_components.tuya_ble.config_flow.bluetooth.async_process_advertisements",
        side_effect=process_advertisements,
    ):
        result = await flow.async_step_discovered_device({})
    if version == 4:
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert manager.calls == [("1234567890abcdef", False)]
        assert result["data"]["uuid"] == "1234567890abcdef"
    else:
        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": "bluetooth_scan_failed"}
        assert not manager.calls


async def test_fd50_manual_discovery(hass: HomeAssistant) -> None:
    """Include FD50 devices in the manual device selector."""
    flow = build_flow(hass)
    flow.context = {}
    info = discovery()
    with (
        patch(
            "custom_components.tuya_ble.config_flow.async_discovered_service_info",
            return_value=[info],
        ),
        patch.object(flow, "_async_current_ids", return_value=set()),
    ):
        result = await flow.async_step_device()
    assert result["type"] == FlowResultType.FORM
    assert (
        info.address in cast(Any, result["data_schema"]).schema[CONF_ADDRESS].container
    )


@pytest.mark.parametrize("version", [4, 5])
async def test_fd50_bluetooth_discovery(hass: HomeAssistant, version: int) -> None:
    """Do not ask for cloud login when the advertised version is unsupported."""
    flow = build_flow(hass)
    info = discovery(version)
    result = await flow.async_step_bluetooth(cast(Any, info))
    if version == 4:
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"
    else:
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "unsupported_device"
        assert flow.discovery_info is None


@pytest.mark.parametrize("complete", [True, False])
async def test_fd50_partial_bluetooth_discovery(
    hass: HomeAssistant, complete: bool
) -> None:
    """Wait for the scan response rather than dropping legitimate v4 devices."""
    flow = build_flow(hass)
    info = discovery()
    info.manufacturer_data = {}
    info.advertisement = cast(Any, v4_advertisement())
    info.advertisement.manufacturer_data = {}
    with patch.object(
        flow, "_async_scan_device", return_value=discovery() if complete else None
    ):
        result = await flow.async_step_bluetooth(cast(Any, info))
    assert result["type"] == (FlowResultType.FORM if complete else FlowResultType.ABORT)
    if not complete:
        assert result["reason"] == "unsupported_device"


async def test_fd50_empty_bluetooth_discovery(hass: HomeAssistant) -> None:
    """An empty FD50 record cannot establish a supported protocol version."""
    flow = build_flow(hass)
    info = discovery()
    info.service_data = {SERVICE_UUID_V4: b""}
    info.advertisement.service_data = info.service_data
    result = await flow.async_step_bluetooth(cast(Any, info))
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "unsupported_device"


@pytest.mark.parametrize("invalid", ["ciphertext", "beacon", "compressed"])
async def test_fd50_complete_invalid_bluetooth_discovery(
    hass: HomeAssistant, invalid: str
) -> None:
    """Complete undecodable FD50 records do not wait through an active scan."""
    flow = build_flow(hass)
    info = discovery()
    if invalid == "ciphertext":
        adv = v4_advertisement(plaintext=bytes(16))
    elif invalid == "beacon":
        adv = v4_advertisement(b"\x48\x00\x00\x08abcdefgh")
    else:
        adv = v4_advertisement(flags=1)
    info.service_data = adv.service_data
    info.manufacturer_data = adv.manufacturer_data
    info.advertisement = cast(Any, adv)
    with patch.object(flow, "_async_scan_device") as scan:
        result = await flow.async_step_bluetooth(cast(Any, info))
    scan.assert_not_awaited()
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "unsupported_device"
    assert flow.discovery_info is None


async def test_classic_bluetooth_discovery_without_cached_service(
    hass: HomeAssistant,
) -> None:
    """The shared resolver does not impose FD50 validation on legacy discovery."""
    flow = build_flow(hass)
    info = FakeDiscovery(with_service=False)
    result = await flow.async_step_bluetooth(cast(Any, info))
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_fd50_unsupported_manual_discovery(hass: HomeAssistant) -> None:
    """Unsupported FD50 formats do not appear in the manual device selector."""
    flow = build_flow(hass)
    with (
        patch(
            "custom_components.tuya_ble.config_flow.async_discovered_service_info",
            return_value=[discovery(5)],
        ),
        patch.object(flow, "_async_current_ids", return_value=set()),
    ):
        result = await flow.async_step_device()
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "no_unconfigured_devices"


@pytest.mark.parametrize(
    "partial", ["manufacturer", "manufacturer_none", "header", "payload"]
)
@pytest.mark.parametrize("scan_succeeds", [True, False])
async def test_fd50_partial_manual_discovery(
    hass: HomeAssistant, partial: str, scan_succeeds: bool
) -> None:
    """Complete cached partial v4 identities before deciding selector eligibility."""
    flow = build_flow(hass)
    info = discovery()
    if partial in ("manufacturer", "manufacturer_none"):
        info.manufacturer_data = {} if partial == "manufacturer" else None
        # Deliberately simulate an absent cached response with the test fake.
        info.advertisement.manufacturer_data = info.manufacturer_data  # type: ignore[assignment]
    else:
        service = info.advertisement.service_data[SERVICE_UUID_V4]
        info.service_data = {
            SERVICE_UUID_V4: service[: 1 if partial == "header" else 4]
        }
        info.advertisement.service_data = info.service_data
    complete = discovery()
    with (
        patch(
            "custom_components.tuya_ble.config_flow.async_discovered_service_info",
            return_value=[info],
        ),
        patch.object(flow, "_async_current_ids", return_value=set()),
        patch.object(
            flow, "_async_scan_device", return_value=complete if scan_succeeds else None
        ) as scan,
    ):
        result = await flow.async_step_device()
    scan.assert_awaited_once_with(info.address)
    if scan_succeeds:
        assert result["type"] == FlowResultType.FORM
        assert flow._discovered_devices[info.address] is cast(Any, complete)
    else:
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "no_unconfigured_devices"


@pytest.mark.parametrize("invalid", ["version", "empty", "ciphertext"])
async def test_fd50_complete_invalid_manual_discovery(
    hass: HomeAssistant, invalid: str
) -> None:
    """Reject known unsupported or complete undecodable records without scanning."""
    flow = build_flow(hass)
    info = discovery(5 if invalid == "version" else 4)
    if invalid == "empty":
        info.service_data = {SERVICE_UUID_V4: b""}
        info.advertisement.service_data = info.service_data
    elif invalid == "ciphertext":
        adv = v4_advertisement(plaintext=bytes(16))
        info.manufacturer_data = adv.manufacturer_data
        info.advertisement = cast(Any, adv)
    with (
        patch(
            "custom_components.tuya_ble.config_flow.async_discovered_service_info",
            return_value=[info],
        ),
        patch.object(flow, "_async_current_ids", return_value=set()),
        patch.object(flow, "_async_scan_device") as scan,
    ):
        result = await flow.async_step_device()
    scan.assert_not_awaited()
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "no_unconfigured_devices"


@pytest.mark.parametrize("rejection", ["unsupported", "scan_timeout"])
@pytest.mark.parametrize("has_remaining_device", [True, False])
async def test_fd50_retry_removes_stale_selector_entry(
    hass: HomeAssistant, rejection: str, has_remaining_device: bool
) -> None:
    """A rejected latest record evicts old eligibility and cannot remain default."""
    flow = build_flow(hass)
    stale = discovery()
    flow._discovered_devices[stale.address] = cast(Any, stale)
    current = discovery(5 if rejection == "unsupported" else 4)
    if rejection == "scan_timeout":
        current.manufacturer_data = {}
        current.advertisement.manufacturer_data = {}
    remaining = discovery()
    remaining.address = "11:22:33:44:55:66"
    current_records = [current, remaining] if has_remaining_device else [current]
    with (
        patch(
            "custom_components.tuya_ble.config_flow.async_discovered_service_info",
            return_value=current_records,
        ),
        patch.object(flow, "_async_current_ids", return_value=set()),
        patch.object(
            flow, "_async_setup_address", return_value=(None, "bluetooth_scan_failed")
        ),
        patch.object(flow, "_async_scan_device", return_value=None),
    ):
        result = await flow.async_step_device({CONF_ADDRESS: stale.address})
    if has_remaining_device:
        assert result["type"] == FlowResultType.FORM
        schema = cast(Any, result["data_schema"])
        assert stale.address not in schema.schema[CONF_ADDRESS].container
        assert schema({}) == {CONF_ADDRESS: remaining.address}
    else:
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "no_unconfigured_devices"


@pytest.mark.parametrize("record_in_history", [True, False])
@pytest.mark.parametrize("has_remaining_device", [True, False])
async def test_manual_discovery_purges_configured_cached_address(
    hass: HomeAssistant, record_in_history: bool, has_remaining_device: bool
) -> None:
    """Configured cached devices disappear even when scanner history omits them."""
    flow = build_flow(hass)
    configured = discovery()
    flow._discovered_devices[configured.address] = cast(Any, configured)
    remaining = discovery()
    remaining.address = "11:22:33:44:55:66"
    records = [configured] if record_in_history else []
    if has_remaining_device:
        records.append(remaining)
    with (
        patch(
            "custom_components.tuya_ble.config_flow.async_discovered_service_info",
            return_value=records,
        ),
        patch.object(
            flow, "_async_current_ids", return_value={configured.address, None}
        ),
    ):
        result = await flow.async_step_device()
    if has_remaining_device:
        assert result["type"] == FlowResultType.FORM
        schema = cast(Any, result["data_schema"])
        assert configured.address not in schema.schema[CONF_ADDRESS].container
        assert schema({}) == {CONF_ADDRESS: remaining.address}
    else:
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "no_unconfigured_devices"


async def test_manual_discovery_purges_address_configured_during_scan(
    hass: HomeAssistant,
) -> None:
    """Recheck configured IDs after awaited scans before constructing the form."""
    flow = build_flow(hass)
    partial = discovery()
    partial.manufacturer_data = {}
    partial.advertisement.manufacturer_data = {}
    complete = discovery()
    with (
        patch(
            "custom_components.tuya_ble.config_flow.async_discovered_service_info",
            return_value=[partial],
        ),
        patch.object(
            flow,
            "_async_current_ids",
            side_effect=[set(), {partial.address}, {partial.address}],
        ),
        patch.object(flow, "_async_scan_device", return_value=complete) as scan,
    ):
        result = await flow.async_step_device()
    scan.assert_awaited_once_with(partial.address)
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "no_unconfigured_devices"


def partial_candidates(count: int) -> list[FakeDiscovery]:
    """Build distinct partial FD50 records requiring an active scan."""
    candidates = []
    for index in range(count):
        info = discovery()
        info.address = f"AA:BB:CC:DD:EE:{index:02X}"
        info.manufacturer_data = {}
        info.advertisement.manufacturer_data = {}
        candidates.append(info)
    return candidates


async def test_partial_candidates_are_resolved_concurrently(
    hass: HomeAssistant,
) -> None:
    """Every scan can start before any timeout result is returned."""
    flow = build_flow(hass)
    candidates = partial_candidates(3)
    started: set[str] = set()
    all_started = asyncio.Event()
    release = asyncio.Event()

    async def scan(address: str) -> None:
        started.add(address)
        if len(started) == len(candidates):
            all_started.set()
        await release.wait()

    with (
        patch(
            "custom_components.tuya_ble.config_flow.async_discovered_service_info",
            return_value=candidates,
        ),
        patch.object(flow, "_async_current_ids", return_value=set()),
        patch.object(flow, "_async_scan_device", side_effect=scan),
    ):
        step = asyncio.create_task(flow.async_step_device())
        try:
            await asyncio.wait_for(all_started.wait(), 0.5)
            release.set()
            result = await asyncio.wait_for(step, 0.5)
        finally:
            step.cancel()
            await asyncio.gather(step, return_exceptions=True)
    assert started == {candidate.address for candidate in candidates}
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "no_unconfigured_devices"


async def test_ready_device_does_not_wait_for_partial_scans(
    hass: HomeAssistant,
) -> None:
    """Show ready devices promptly and cancel/await unrelated unfinished scans."""
    flow = build_flow(hass)
    candidates = partial_candidates(3)
    ready = discovery()
    ready.address = "11:22:33:44:55:66"
    blocked = asyncio.Event()
    started: set[str] = set()
    cancelled: set[str] = set()

    async def scan(address: str) -> None:
        started.add(address)
        try:
            await blocked.wait()
        finally:
            cancelled.add(address)

    with (
        patch(
            "custom_components.tuya_ble.config_flow.async_discovered_service_info",
            return_value=[*candidates, ready],
        ),
        patch.object(flow, "_async_current_ids", return_value=set()),
        patch.object(flow, "_async_scan_device", side_effect=scan),
    ):
        result = await asyncio.wait_for(flow.async_step_device(), 0.5)
    assert started == cancelled == {candidate.address for candidate in candidates}
    assert result["type"] == FlowResultType.FORM
    schema = cast(Any, result["data_schema"])
    assert schema({}) == {CONF_ADDRESS: ready.address}
    assert set(schema.schema[CONF_ADDRESS].container) == {ready.address}


async def test_cancelled_discovery_cleans_up_all_scans(hass: HomeAssistant) -> None:
    """Cancelling the parent flow cannot leave registered scanner tasks behind."""
    flow = build_flow(hass)
    candidates = partial_candidates(2)
    started: set[str] = set()
    finished: set[str] = set()
    all_started = asyncio.Event()
    blocked = asyncio.Event()

    async def scan(address: str) -> None:
        started.add(address)
        if len(started) == len(candidates):
            all_started.set()
        try:
            await blocked.wait()
        finally:
            finished.add(address)

    with (
        patch(
            "custom_components.tuya_ble.config_flow.async_discovered_service_info",
            return_value=candidates,
        ),
        patch.object(flow, "_async_current_ids", return_value=set()),
        patch.object(flow, "_async_scan_device", side_effect=scan),
    ):
        step = asyncio.create_task(flow.async_step_device())
        try:
            await asyncio.wait_for(all_started.wait(), 0.5)
            step.cancel()
            with pytest.raises(asyncio.CancelledError):
                await step
        finally:
            step.cancel()
            await asyncio.gather(step, return_exceptions=True)
    assert finished == started == {candidate.address for candidate in candidates}


@pytest.mark.parametrize("has_current_device", [True, False])
async def test_selector_refresh_removes_absent_addresses(
    hass: HomeAssistant, has_current_device: bool
) -> None:
    """Selector eligibility is rebuilt from the current discovery snapshot."""
    flow = build_flow(hass)
    absent = discovery()
    flow._discovered_devices[absent.address] = cast(Any, absent)
    current = discovery()
    current.address = "11:22:33:44:55:66"
    with (
        patch(
            "custom_components.tuya_ble.config_flow.async_discovered_service_info",
            return_value=[current] if has_current_device else [],
        ),
        patch.object(flow, "_async_current_ids", return_value=set()),
    ):
        result = await flow.async_step_device()
    if has_current_device:
        assert result["type"] == FlowResultType.FORM
        schema = cast(Any, result["data_schema"])
        assert set(schema.schema[CONF_ADDRESS].container) == {current.address}
        assert schema({}) == {CONF_ADDRESS: current.address}
    else:
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "no_unconfigured_devices"


async def test_selector_refresh_keeps_current_updated_record(
    hass: HomeAssistant,
) -> None:
    """Still-discovered addresses remain eligible with their newest metadata."""
    flow = build_flow(hass)
    cached = discovery()
    cached.name = "Old name"
    flow._discovered_devices[cached.address] = cast(Any, cached)
    current = discovery()
    current.name = "Updated name"
    with (
        patch(
            "custom_components.tuya_ble.config_flow.async_discovered_service_info",
            return_value=[current],
        ),
        patch.object(flow, "_async_current_ids", return_value=set()),
    ):
        result = await flow.async_step_device()
    assert result["type"] == FlowResultType.FORM
    schema = cast(Any, result["data_schema"])
    assert schema.schema[CONF_ADDRESS].container[current.address] == "Updated name"


async def test_discovery_error_cleans_up_other_scans(hass: HomeAssistant) -> None:
    """An unexpected scan failure cannot orphan the other candidate tasks."""
    flow = build_flow(hass)
    candidates = partial_candidates(2)
    started: set[str] = set()
    finished: set[str] = set()
    all_started = asyncio.Event()
    blocked = asyncio.Event()

    async def scan(address: str) -> None:
        started.add(address)
        if len(started) == len(candidates):
            all_started.set()
        try:
            await all_started.wait()
            if address == candidates[0].address:
                raise RuntimeError("Test scan failure")
            await blocked.wait()
        finally:
            finished.add(address)

    with (
        patch(
            "custom_components.tuya_ble.config_flow.async_discovered_service_info",
            return_value=candidates,
        ),
        patch.object(flow, "_async_current_ids", return_value=set()),
        patch.object(flow, "_async_scan_device", side_effect=scan),
    ):
        step = asyncio.create_task(flow.async_step_device())
        try:
            with pytest.raises(RuntimeError, match="Test scan failure"):
                await step
        finally:
            step.cancel()
            await asyncio.gather(step, return_exceptions=True)
    assert finished == started == {candidate.address for candidate in candidates}
