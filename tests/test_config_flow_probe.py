"""Automatic fallback is gated by authenticated status, not names or UUID guesses."""

# pylint: disable=protected-access

from __future__ import annotations

import asyncio
from collections.abc import Iterator
import gc
import logging
import time
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tuya_ble.config_flow import TuyaBLEConfigFlow, _probe_state
from custom_components.tuya_ble.const import (
    CONF_ADVERTISED_UUID,
    CONF_CLOUD_UUID,
    CONF_DEVICE_ID,
    CONF_UUID,
    DOMAIN,
)
from custom_components.tuya_ble.tuya_ble import TuyaBLEDeviceCredentials
from custom_components.tuya_ble.tuya_ble.probe import ProbeOutcome
from tests.conftest import FakeBLEAddress, make_credentials
from tests.test_config_flow import FakeDiscovery, FakeManager, build_flow


class CandidateManager(FakeManager):
    """Cloud cache with candidate credentials and no exact UUID match."""

    def __init__(self, candidates: list[TuyaBLEDeviceCredentials]) -> None:
        super().__init__()
        self.candidates = candidates

    async def get_probe_candidates(
        self, advertised_uuid: str, product_id: str | None
    ) -> tuple[int, Iterator[TuyaBLEDeviceCredentials]]:
        return len(self.candidates), iter(self.candidates)


def _flow(
    hass: HomeAssistant, candidates: list[TuyaBLEDeviceCredentials]
) -> tuple[TuyaBLEConfigFlow, FakeDiscovery]:
    flow = build_flow(hass)
    flow._manager = cast(Any, CandidateManager(candidates))
    discovery = FakeDiscovery()
    discovery.device = FakeBLEAddress()  # type: ignore[attr-defined]
    flow._discovered_devices[discovery.address] = discovery  # type: ignore[assignment]
    return flow, discovery


async def _setup(
    flow: TuyaBLEConfigFlow,
    discovery: FakeDiscovery,
    mapped_dp_ids: frozenset[int] = frozenset({1, 2, 3}),
) -> tuple[Any, Any]:
    with (
        patch.object(flow, "_async_scan_device", return_value=discovery),
        patch(
            "custom_components.tuya_ble.config_flow.async_setup_registry",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.tuya_ble.config_flow.get_mapped_dp_ids",
            return_value=mapped_dp_ids,
        ),
        patch.object(flow, "_async_refresh_discovered_devices", new_callable=AsyncMock),
    ):
        result = await flow.async_step_device({"address": discovery.address})
        if result["type"] == FlowResultType.CREATE_ENTRY:
            return result, None
        assert result["type"] == FlowResultType.SHOW_PROGRESS
        assert flow._probe_task is not None
        await flow._probe_task
        done = await flow.async_step_probe()
        assert done["type"] == FlowResultType.SHOW_PROGRESS_DONE
        result = await flow.async_step_probe_finish()
        if result["type"] == FlowResultType.FORM:
            assert result["errors"] is not None
            return None, result["errors"]["base"]
        return result, None


@pytest.mark.parametrize(
    ("results", "selected", "error"),
    [
        ([ProbeOutcome.MATCHED], "1234567890abcdef", None),
        ([ProbeOutcome.REJECTED, ProbeOutcome.MATCHED], "cloud-uuid", None),
        ([ProbeOutcome.REJECTED, ProbeOutcome.REJECTED], None, "device_not_registered"),
        ([ProbeOutcome.INCONCLUSIVE], None, "bluetooth_probe_failed"),
    ],
)
async def test_probe_order_and_outcomes(
    hass: HomeAssistant,
    results: list[ProbeOutcome],
    selected: str | None,
    error: str | None,
) -> None:
    """Test advertised first, cloud UUID only on conclusive pair rejection."""
    candidate = make_credentials(
        uuid="cloud-uuid", product_id="prod", local_key="abcdef"
    )
    flow, discovery = _flow(hass, [candidate])
    with patch(
        "custom_components.tuya_ble.config_flow.async_probe_authenticated_status",
        new_callable=AsyncMock,
        side_effect=results,
    ) as probe:
        outcome, failure = await _setup(flow, discovery)
    assert failure == error
    assert probe.await_count == len(results)
    uuids = [call.args[3] for call in probe.await_args_list]
    assert uuids == ["1234567890abcdef", "cloud-uuid"][: len(results)]
    if selected:
        assert outcome["type"] == FlowResultType.CREATE_ENTRY
        assert outcome["data"][CONF_UUID] == selected
        assert outcome["data"][CONF_CLOUD_UUID] == candidate.uuid
        assert outcome["data"][CONF_ADVERTISED_UUID] == "1234567890abcdef"
    else:
        assert outcome is None


async def test_unbound_skips_probe(hass: HomeAssistant) -> None:
    """Unbound devices are not paired merely to identify a candidate."""
    flow, discovery = _flow(hass, [make_credentials(uuid="cloud-uuid")])
    assert discovery.manufacturer_data is not None
    discovery.manufacturer_data[2000] = (
        bytes([0, 2]) + discovery.manufacturer_data[2000][2:]
    )
    discovery.advertisement.manufacturer_data = discovery.manufacturer_data
    with patch(
        "custom_components.tuya_ble.config_flow.async_probe_authenticated_status",
        new_callable=AsyncMock,
    ) as probe:
        result, error = await _setup(flow, discovery)
    assert result is None
    assert error == "device_not_registered"
    probe.assert_not_awaited()


async def test_unmapped_candidate_skips_ble_authentication(hass: HomeAssistant) -> None:
    """Without a known status DP, an authentication response is insufficient."""
    flow, discovery = _flow(hass, [make_credentials(uuid="cloud-uuid")])
    with patch(
        "custom_components.tuya_ble.config_flow.async_probe_authenticated_status",
        new_callable=AsyncMock,
    ) as probe:
        result, error = await _setup(flow, discovery, frozenset())
    assert result is None
    assert error == "device_not_registered"
    probe.assert_not_awaited()


@pytest.mark.parametrize("refresh_failed", [False, True])
async def test_candidate_cap_and_exact_match(
    hass: HomeAssistant, refresh_failed: bool
) -> None:
    """An exact match never probes, and the candidate limit prevents I/O."""
    flow, discovery = _flow(
        hass,
        [
            make_credentials(uuid=f"other-{i}", device_id=f"device-{i}")
            for i in range(5)
        ],
    )
    manager = flow._manager
    assert manager is not None
    with (
        patch(
            "custom_components.tuya_ble.config_flow.async_probe_authenticated_status",
            new_callable=AsyncMock,
        ) as probe,
        patch.object(
            manager,
            "get_device_credentials_by_uuid",
            new_callable=AsyncMock,
            return_value=None,
            side_effect=OSError("cloud unavailable") if refresh_failed else None,
        ),
    ):
        result, error = await _setup(flow, discovery)
    assert result is None
    assert error == "too_many_candidates"
    probe.assert_not_awaited()

    flow._manager = cast(Any, FakeManager(make_credentials()))
    with patch(
        "custom_components.tuya_ble.config_flow.async_probe_authenticated_status",
        new_callable=AsyncMock,
    ) as probe:
        result, error = await _setup(flow, discovery)
    assert error is None
    assert result["type"] == FlowResultType.CREATE_ENTRY
    probe.assert_not_awaited()


async def test_first_candidate_rejected_then_second_matches(
    hass: HomeAssistant,
) -> None:
    """Advance candidates after both pair UUIDs conclusively reject."""
    candidates = [
        make_credentials(uuid="cloud-a", device_id="device-a", product_id="prod"),
        make_credentials(uuid="cloud-b", device_id="device-b", product_id="prod"),
    ]
    flow, discovery = _flow(hass, candidates)
    with patch(
        "custom_components.tuya_ble.config_flow.async_probe_authenticated_status",
        new_callable=AsyncMock,
        side_effect=[
            ProbeOutcome.REJECTED,
            ProbeOutcome.REJECTED,
            ProbeOutcome.MATCHED,
        ],
    ) as probe:
        result, error = await _setup(flow, discovery)
    assert error is None
    assert result["data"]["device_id"] == "device-b"
    assert [call.args[3] for call in probe.await_args_list] == [
        "1234567890abcdef",
        "cloud-a",
        "1234567890abcdef",
    ]


async def test_probe_reservation_and_expiration(hass: HomeAssistant) -> None:
    """Overlapping flows cannot claim one cloud ID; expired claims can retry."""
    candidate = make_credentials(
        uuid="cloud-uuid", product_id="prod", local_key="abcdef"
    )
    first, discovery = _flow(hass, [candidate])
    with patch(
        "custom_components.tuya_ble.config_flow.async_probe_authenticated_status",
        new_callable=AsyncMock,
        return_value=ProbeOutcome.MATCHED,
    ) as probe:
        result, error = await _setup(first, discovery)
        assert error is None
        assert result["type"] == FlowResultType.CREATE_ENTRY
        second, _ = _flow(hass, [candidate])
        result, error = await _setup(second, discovery)
        assert result is None
        assert error == "device_not_registered"
        assert probe.await_count == 1
        _probe_state(hass)[1][candidate.device_id] = (
            first._probe_claim_owner,
            discovery.address,
            time.monotonic() - 1,
        )
        result, error = await _setup(second, discovery)
        assert error is None
        assert result["type"] == FlowResultType.CREATE_ENTRY
    assert probe.await_count == 2


async def test_probe_claim_released_on_entry_failure(hass: HomeAssistant) -> None:
    """Failed config-entry creation must not reserve a candidate indefinitely."""
    candidate = make_credentials(uuid="cloud-uuid", product_id="prod")
    flow, discovery = _flow(hass, [candidate])
    with (
        patch(
            "custom_components.tuya_ble.config_flow.async_probe_authenticated_status",
            new_callable=AsyncMock,
            return_value=ProbeOutcome.MATCHED,
        ),
        patch.object(flow, "async_create_entry", side_effect=RuntimeError("synthetic")),
        pytest.raises(RuntimeError, match="synthetic"),
    ):
        await _setup(flow, discovery)
    assert candidate.device_id not in _probe_state(hass)[1]


async def test_progress_resumes_without_repeating_scan_or_probe(
    hass: HomeAssistant,
) -> None:
    """Re-entering an in-progress flow keeps one probe task and the same BLE scan."""
    flow, discovery = _flow(hass, [make_credentials(uuid="cloud-uuid")])
    started = asyncio.Event()
    release = asyncio.Event()

    async def pending_probe(*args: Any) -> tuple[None, None, str]:
        started.set()
        await release.wait()
        return None, None, "device_not_registered"

    with (
        patch.object(flow, "_async_probe_fallback", side_effect=pending_probe) as probe,
        patch.object(flow, "_async_scan_device", return_value=discovery) as scan,
        patch.object(flow, "_async_refresh_discovered_devices", new_callable=AsyncMock),
    ):
        first, _ = await flow._async_setup_address(discovery.address)
        assert first is not None
        assert first["type"] == FlowResultType.SHOW_PROGRESS
        assert first["progress_action"] == "verify_device"
        await started.wait()
        task = flow._probe_task
        second, _ = await flow._async_setup_address(discovery.address)
        assert second is not None
        assert second["type"] == FlowResultType.SHOW_PROGRESS
        assert flow._probe_task is task
        scan.assert_awaited_once()
        probe.assert_awaited_once()
        release.set()
        assert task is not None
        await task
        assert (await flow.async_step_probe())[
            "type"
        ] == FlowResultType.SHOW_PROGRESS_DONE
        form = await flow.async_step_probe_finish()
        assert form["errors"] is not None
        assert form["errors"]["base"] == "device_not_registered"


async def test_cancelled_progress_returns_safe_retry_form(hass: HomeAssistant) -> None:
    """Closing a progress dialog cannot create an entry or restart the probe."""
    flow, discovery = _flow(hass, [make_credentials(uuid="cloud-uuid")])
    started = asyncio.Event()

    async def pending_probe(*args: Any) -> None:
        started.set()
        await asyncio.Event().wait()

    with (
        patch.object(flow, "_async_probe_fallback", side_effect=pending_probe),
        patch.object(flow, "_async_scan_device", return_value=discovery),
        patch.object(flow, "_async_refresh_discovered_devices", new_callable=AsyncMock),
    ):
        progress, _ = await flow._async_setup_address(discovery.address)
        assert progress is not None
        assert progress["type"] == FlowResultType.SHOW_PROGRESS
        await started.wait()
        task = flow._probe_task
        assert task is not None
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await flow.async_step_probe()
        result = await flow.async_step_probe_finish()
        assert result["errors"] is not None
        assert result["errors"]["base"] == "bluetooth_probe_failed"


async def test_discovery_probe_failure_returns_to_discovered_device(
    hass: HomeAssistant,
) -> None:
    """Bluetooth-initiated setup never switches to the manual device picker."""
    flow, discovery = _flow(hass, [make_credentials(uuid="cloud-uuid")])
    flow.discovery_info = cast(Any, discovery)
    with (
        patch.object(flow, "_async_scan_device", return_value=discovery),
        patch.object(
            flow,
            "_async_probe_fallback",
            new_callable=AsyncMock,
            return_value=(None, None, "device_not_registered"),
        ),
    ):
        progress = await flow.async_step_discovered_device({})
        assert progress["type"] == FlowResultType.SHOW_PROGRESS
        assert flow._probe_task is not None
        await flow._probe_task
        assert (await flow.async_step_probe())[
            "type"
        ] == FlowResultType.SHOW_PROGRESS_DONE
        form = await flow.async_step_probe_finish()
    assert form["step_id"] == "discovered_device"
    assert form["errors"] is not None
    assert form["errors"]["base"] == "device_not_registered"


async def test_progress_exception_has_no_secret_in_error(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Cloud failures present a generic retry error without logging exception text."""
    flow, discovery = _flow(hass, [make_credentials(uuid="cloud-uuid")])
    with (
        patch.object(flow, "_async_scan_device", return_value=discovery),
        patch.object(flow, "_async_refresh_discovered_devices", new_callable=AsyncMock),
        patch.object(
            flow,
            "_async_probe_fallback",
            new_callable=AsyncMock,
            side_effect=RuntimeError("secret-from-cloud"),
        ),
        caplog.at_level(logging.DEBUG),
    ):
        progress, _ = await flow._async_setup_address(discovery.address)
        assert progress is not None
        assert progress["type"] == FlowResultType.SHOW_PROGRESS
        assert flow._probe_task is not None
        with pytest.raises(RuntimeError):
            await flow._probe_task
        await flow.async_step_probe()
        form = await flow.async_step_probe_finish()
    assert form["errors"] is not None
    assert form["errors"]["base"] == "probe_setup_failed"
    assert "secret-from-cloud" not in caplog.text


async def test_forced_cloud_refresh_failure_uses_cached_verified_candidate(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A failed refresh does not discard a candidate proven by authenticated BLE."""
    candidate = make_credentials(uuid="cloud-uuid", product_id="prod")
    flow, discovery = _flow(hass, [candidate])
    manager = flow._manager
    assert manager is not None
    with (
        patch.object(
            manager,
            "get_device_credentials_by_uuid",
            new_callable=AsyncMock,
            side_effect=[None, OSError("private-cloud-error")],
        ) as lookup,
        patch(
            "custom_components.tuya_ble.config_flow.async_probe_authenticated_status",
            new_callable=AsyncMock,
            return_value=ProbeOutcome.MATCHED,
        ),
        caplog.at_level(logging.DEBUG),
    ):
        result, error = await _setup(flow, discovery)
    assert error is None
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert lookup.await_count == 2
    assert "private-cloud-error" not in caplog.text


async def test_refresh_failure_without_verifiable_candidate_is_retryable(
    hass: HomeAssistant,
) -> None:
    """A failed refresh cannot prove the account has no cloud record."""
    flow, discovery = _flow(hass, [])
    manager = flow._manager
    assert manager is not None
    with patch.object(
        manager,
        "get_device_credentials_by_uuid",
        new_callable=AsyncMock,
        side_effect=[None, OSError("cloud unavailable")],
    ):
        result, error = await _setup(flow, discovery)
    assert result is None
    assert error == "cloud_refresh_failed"


async def test_registry_failure_is_not_reported_as_bluetooth_failure(
    hass: HomeAssistant,
) -> None:
    """A registry initialization error cannot be fixed by moving closer to BLE."""
    flow, discovery = _flow(hass, [make_credentials(uuid="cloud-uuid")])
    with (
        patch.object(flow, "_async_scan_device", return_value=discovery),
        patch.object(flow, "_async_refresh_discovered_devices", new_callable=AsyncMock),
        patch(
            "custom_components.tuya_ble.config_flow.async_setup_registry",
            new_callable=AsyncMock,
            side_effect=OSError("unreadable descriptors"),
        ),
    ):
        progress, _ = await flow._async_setup_address(discovery.address)
        assert progress is not None
        assert progress["type"] == FlowResultType.SHOW_PROGRESS
        assert flow._probe_task is not None
        with pytest.raises(OSError):
            await flow._probe_task
        await flow.async_step_probe()
        form = await flow.async_step_probe_finish()
    assert form["errors"] is not None
    assert form["errors"]["base"] == "probe_setup_failed"


@pytest.mark.parametrize("stale_reason", ["expired", "reclaimed", "configured"])
async def test_stale_claim_does_not_create_an_entry(
    hass: HomeAssistant, stale_reason: str
) -> None:
    """Expiry, reassignment, or another entry invalidate a progress result."""
    candidate = make_credentials(uuid="cloud-uuid", product_id="prod")
    flow, discovery = _flow(hass, [candidate])
    with (
        patch.object(flow, "_async_scan_device", return_value=discovery),
        patch.object(flow, "_async_refresh_discovered_devices", new_callable=AsyncMock),
        patch(
            "custom_components.tuya_ble.config_flow.async_setup_registry",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.tuya_ble.config_flow.get_mapped_dp_ids",
            return_value=frozenset({1}),
        ),
        patch(
            "custom_components.tuya_ble.config_flow.async_probe_authenticated_status",
            new_callable=AsyncMock,
            return_value=ProbeOutcome.MATCHED,
        ),
        patch.object(flow, "async_create_entry") as create_entry,
    ):
        progress, _ = await flow._async_setup_address(discovery.address)
        assert progress is not None
        assert progress["type"] == FlowResultType.SHOW_PROGRESS
        assert flow._probe_task is not None
        await flow._probe_task
        claims = _probe_state(hass)[1]
        owner, address, _ = claims[candidate.device_id]
        if stale_reason == "configured":
            MockConfigEntry(
                domain=DOMAIN, data={CONF_DEVICE_ID: candidate.device_id}
            ).add_to_hass(hass)
        else:
            claims[candidate.device_id] = (
                object() if stale_reason == "reclaimed" else owner,
                address,
                time.monotonic() + 120
                if stale_reason == "reclaimed"
                else time.monotonic() - 1,
            )
        await flow.async_step_probe()
        form = await flow.async_step_probe_finish()
        create_entry.assert_not_called()
    assert form["errors"] is not None
    assert form["errors"]["base"] == "device_verification_expired"
    assert (candidate.device_id in claims) is (stale_reason == "reclaimed")


async def test_removing_verified_flow_releases_only_owned_claims(
    hass: HomeAssistant,
) -> None:
    """Cancelling after verification immediately frees this flow's reservations."""
    candidate = make_credentials(uuid="cloud-uuid", product_id="prod")
    flow, discovery = _flow(hass, [candidate])
    flow.discovery_info = cast(Any, discovery)
    with (
        patch.object(flow, "_async_scan_device", return_value=discovery),
        patch(
            "custom_components.tuya_ble.config_flow.async_setup_registry",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.tuya_ble.config_flow.get_mapped_dp_ids",
            return_value=frozenset({1}),
        ),
        patch(
            "custom_components.tuya_ble.config_flow.async_probe_authenticated_status",
            new_callable=AsyncMock,
            return_value=ProbeOutcome.MATCHED,
        ),
    ):
        progress = await flow.async_step_discovered_device({})
        assert progress["type"] == FlowResultType.SHOW_PROGRESS
        assert flow._probe_task is not None
        await flow._probe_task
    claims = _probe_state(hass)[1]
    other_claim = (object(), "another-address", time.monotonic() + 120)
    claims["other-device"] = other_claim
    assert candidate.device_id in claims
    flow.async_remove()
    assert claims == {"other-device": other_claim}
    # Removal is also safe without any task or owned claim.
    empty_flow, _ = _flow(hass, [])
    empty_flow.async_remove()
    assert claims == {"other-device": other_claim}


async def test_removing_flow_cancels_pending_probe(hass: HomeAssistant) -> None:
    """An unfinished probe cannot reserve a device after its flow is removed."""
    flow, discovery = _flow(hass, [make_credentials(uuid="cloud-uuid")])
    flow.discovery_info = cast(Any, discovery)
    started = asyncio.Event()

    async def wait_for_registry(_hass: HomeAssistant) -> None:
        """Hold registry initialization until setup is cancelled."""
        started.set()
        await asyncio.Event().wait()

    with (
        patch.object(flow, "_async_scan_device", return_value=discovery),
        patch(
            "custom_components.tuya_ble.config_flow.async_setup_registry",
            side_effect=wait_for_registry,
        ),
    ):
        await flow.async_step_discovered_device({})
        await started.wait()
        task = flow._probe_task
        assert task is not None
        flow.async_remove()
        with pytest.raises(asyncio.CancelledError):
            await task
        await hass.async_block_till_done()
    assert _probe_state(hass)[1] == {}


async def test_failed_probe_is_observed_without_finish_step(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Discarding a failed flow cannot expose its unobserved exception text."""
    flow, discovery = _flow(hass, [make_credentials(uuid="cloud-uuid")])
    flow.discovery_info = cast(Any, discovery)
    loop = asyncio.get_running_loop()
    with (
        patch.object(flow, "_async_scan_device", return_value=discovery),
        patch(
            "custom_components.tuya_ble.config_flow.async_setup_registry",
            new_callable=AsyncMock,
            side_effect=OSError("private-probe-failure"),
        ),
        patch.object(loop, "call_exception_handler") as exception_handler,
        caplog.at_level(logging.DEBUG),
    ):
        progress = await flow.async_step_discovered_device({})
        assert progress["type"] == FlowResultType.SHOW_PROGRESS
        # Do not await the failed task or request its result: the callback must
        # observe its exception even when the finish step is never entered.
        await hass.async_block_till_done()
        assert flow._probe_task is not None
        assert flow._probe_task.done()
        flow.async_remove()
        del progress, flow
        gc.collect()
        exception_handler.assert_not_called()
    assert "private-probe-failure" not in caplog.text


async def test_overlapping_device_selections_keep_probed_unique_id(
    hass: HomeAssistant,
) -> None:
    """An overlapping submission cannot replace the active probe's address."""
    candidate = make_credentials(uuid="cloud-uuid", product_id="prod")
    flow, discovery = _flow(hass, [candidate])
    scan_started = asyncio.Event()
    finish_scan = asyncio.Event()
    second_started = asyncio.Event()

    async def scan(address: str) -> FakeDiscovery:
        """Hold the first scan so another selection arrives before probing."""
        assert address == discovery.address
        scan_started.set()
        await finish_scan.wait()
        return discovery

    async def select_second() -> Any:
        """Submit a different address while the first scan is awaiting data."""
        second_started.set()
        return await flow.async_step_device({"address": "11:22:33:44:55:66"})

    with (
        patch.object(flow, "_async_scan_device", side_effect=scan) as scan_mock,
        patch(
            "custom_components.tuya_ble.config_flow.async_setup_registry",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.tuya_ble.config_flow.get_mapped_dp_ids",
            return_value=frozenset({1}),
        ),
        patch(
            "custom_components.tuya_ble.config_flow.async_probe_authenticated_status",
            new_callable=AsyncMock,
            return_value=ProbeOutcome.MATCHED,
        ),
    ):
        first = asyncio.create_task(
            flow.async_step_device({"address": discovery.address})
        )
        await scan_started.wait()
        second = asyncio.create_task(select_second())
        await second_started.wait()
        assert flow.unique_id == discovery.address
        finish_scan.set()
        results = await asyncio.gather(first, second)
        assert all(
            result["type"]
            in (FlowResultType.SHOW_PROGRESS, FlowResultType.SHOW_PROGRESS_DONE)
            for result in results
        )
        scan_mock.assert_awaited_once_with(discovery.address)
        assert flow.unique_id == discovery.address
        assert flow._probe_task is not None
        await flow._probe_task
        result = await flow.async_step_probe_finish()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"]["address"] == discovery.address
    assert flow.unique_id == discovery.address


async def test_verified_flow_finishes_while_other_probe_holds_lock(
    hass: HomeAssistant,
) -> None:
    """An unrelated probe cannot delay successful claim validation past its TTL."""
    candidate = make_credentials(uuid="cloud-uuid", product_id="prod")
    flow, discovery = _flow(hass, [candidate])
    flow.discovery_info = cast(Any, discovery)
    with (
        patch.object(flow, "_async_scan_device", return_value=discovery),
        patch(
            "custom_components.tuya_ble.config_flow.async_setup_registry",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.tuya_ble.config_flow.get_mapped_dp_ids",
            return_value=frozenset({1}),
        ),
        patch(
            "custom_components.tuya_ble.config_flow.async_probe_authenticated_status",
            new_callable=AsyncMock,
            return_value=ProbeOutcome.MATCHED,
        ),
    ):
        progress = await flow.async_step_discovered_device({})
        assert progress["type"] == FlowResultType.SHOW_PROGRESS
        assert flow._probe_task is not None
        await flow._probe_task
        lock = _probe_state(hass)[0]
        async with lock:
            result = await asyncio.wait_for(flow.async_step_probe_finish(), 0.1)
            assert lock.locked()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_DEVICE_ID] == candidate.device_id
