"""Automatic fallback is gated by authenticated status, not names or UUID guesses."""

# pylint: disable=protected-access

from __future__ import annotations

import time
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest

from custom_components.tuya_ble.config_flow import TuyaBLEConfigFlow, _probe_state
from custom_components.tuya_ble.const import CONF_CLOUD_UUID, CONF_UUID
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
    ) -> list[TuyaBLEDeviceCredentials]:
        return self.candidates


def _flow(
    hass: HomeAssistant, candidates: list[TuyaBLEDeviceCredentials]
) -> tuple[TuyaBLEConfigFlow, FakeDiscovery]:
    flow = build_flow(hass)
    flow._manager = cast(Any, CandidateManager(candidates))
    discovery = FakeDiscovery()
    discovery.device = FakeBLEAddress()  # type: ignore[attr-defined]
    return flow, discovery


async def _setup(flow: TuyaBLEConfigFlow, discovery: FakeDiscovery) -> tuple[Any, Any]:
    with (
        patch.object(flow, "_async_scan_device", return_value=discovery),
        patch(
            "custom_components.tuya_ble.config_flow.async_setup_registry",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.tuya_ble.config_flow.get_mapped_dp_ids",
            return_value=frozenset({1, 2, 3}),
        ),
    ):
        return await flow._async_setup_address(discovery.address)


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
        if selected != candidate.uuid:
            assert outcome["data"][CONF_CLOUD_UUID] == candidate.uuid
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
    assert result is None and error == "device_not_registered"
    probe.assert_not_awaited()


async def test_candidate_cap_and_exact_match(hass: HomeAssistant) -> None:
    """An exact match never probes, and the candidate limit prevents I/O."""
    flow, discovery = _flow(
        hass,
        [
            make_credentials(uuid=f"other-{i}", device_id=f"device-{i}")
            for i in range(5)
        ],
    )
    with patch(
        "custom_components.tuya_ble.config_flow.async_probe_authenticated_status",
        new_callable=AsyncMock,
    ) as probe:
        result, error = await _setup(flow, discovery)
    assert result is None and error == "device_not_registered"
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
        assert error is None and result["type"] == FlowResultType.CREATE_ENTRY
        second, _ = _flow(hass, [candidate])
        result, error = await _setup(second, discovery)
        assert result is None and error == "device_not_registered"
        assert probe.await_count == 1
        _probe_state(hass)[1][candidate.device_id] = (
            discovery.address,
            time.monotonic() - 1,
        )
        result, error = await _setup(second, discovery)
        assert error is None and result["type"] == FlowResultType.CREATE_ENTRY
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
