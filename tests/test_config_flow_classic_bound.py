"""Bound classic records whose identity does not decode (#155)."""

# pylint: disable=protected-access

from __future__ import annotations

from collections.abc import Iterator
import logging
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest

from custom_components.tuya_ble.config_flow import TuyaBLEConfigFlow
from custom_components.tuya_ble.const import (
    CONF_ADVERTISED_UUID,
    CONF_CLOUD_UUID,
    CONF_UUID,
)
from custom_components.tuya_ble.tuya_ble import (
    MANUFACTURER_DATA_ID,
    SERVICE_UUID,
    TuyaBLEDeviceCredentials,
)
from custom_components.tuya_ble.tuya_ble.probe import ProbeOutcome
from tests.conftest import FakeAdvertisementData, FakeBLEAddress, make_credentials
from tests.test_config_flow import FakeDiscovery, FakeManager, build_flow
from tests.test_config_flow_probe import _setup

_SCAN_PATCH = (
    "custom_components.tuya_ble.config_flow_scan.bluetooth.async_process_advertisements"
)
_PROBE_PATCH = "custom_components.tuya_ble.config_flow.async_probe_authenticated_status"

# Captured from the reporter's Fingerbot Touch (#155).
ISSUE_155_SERVICE = bytes.fromhex("006273337562736c6f")
ISSUE_155_MANUFACTURER = bytes.fromhex("8003000001000787bd4688072ba81c56f054895a98be")


class RecordingManager(FakeManager):
    """Cloud cache that records how probe candidates were requested."""

    def __init__(self, candidates: list[TuyaBLEDeviceCredentials]) -> None:
        super().__init__()
        self.candidates = candidates
        self.candidate_calls: list[tuple[str, str | None]] = []

    async def get_probe_candidates(
        self, advertised_uuid: str, product_id: str | None
    ) -> tuple[int, Iterator[TuyaBLEDeviceCredentials]]:
        self.candidate_calls.append((advertised_uuid, product_id))
        return len(self.candidates), iter(self.candidates)


def _discovery(
    service: bytes = ISSUE_155_SERVICE, manufacturer: bytes = ISSUE_155_MANUFACTURER
) -> FakeDiscovery:
    discovery = FakeDiscovery()
    discovery.device = FakeBLEAddress()
    discovery.service_data = {SERVICE_UUID: service}
    discovery.manufacturer_data = {MANUFACTURER_DATA_ID: manufacturer}
    discovery.advertisement = FakeAdvertisementData(
        service_data=discovery.service_data,
        manufacturer_data=discovery.manufacturer_data,
    )
    return discovery


def _flow(
    hass: HomeAssistant,
    discovery: FakeDiscovery,
    candidates: list[TuyaBLEDeviceCredentials],
) -> tuple[TuyaBLEConfigFlow, RecordingManager]:
    flow = build_flow(hass)
    manager = RecordingManager(candidates)
    flow._manager = cast(Any, manager)
    flow._discovered_devices[discovery.address] = discovery  # type: ignore[assignment]
    return flow, manager


async def test_undecoded_bound_classic_is_matched_by_probe(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Skip the UUID lookup and verify cloud candidates by authenticated read."""
    discovery = _discovery()
    candidate = make_credentials(
        uuid="cloud-uuid",
        product_id="bs3ubslo",
        local_key="private-local-key",
        category="kg",
    )
    flow, manager = _flow(hass, discovery, [candidate])
    with (
        patch(
            _PROBE_PATCH, new_callable=AsyncMock, return_value=ProbeOutcome.MATCHED
        ) as probe,
        caplog.at_level(logging.DEBUG),
    ):
        result, error = await _setup(flow, discovery)
    assert error is None
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert not manager.calls
    assert manager.candidate_calls == [("", "bs3ubslo")]
    # The empty advertised UUID is never tried as a pairing identity.
    assert [call.args[3] for call in probe.await_args_list] == ["cloud-uuid"]
    assert result["data"][CONF_UUID] == "cloud-uuid"
    assert result["data"][CONF_CLOUD_UUID] == "cloud-uuid"
    assert result["data"][CONF_ADVERTISED_UUID] is None
    assert "bound classic identity not decoded, probing cloud candidates" in (
        caplog.text
    )
    assert "private-local-key" not in caplog.text


async def test_undecoded_bound_classic_without_match(hass: HomeAssistant) -> None:
    """A rejected probe reports an unregistered device, not a decode error."""
    discovery = _discovery()
    candidate = make_credentials(uuid="cloud-uuid", product_id="bs3ubslo")
    flow, manager = _flow(hass, discovery, [candidate])
    with patch(
        _PROBE_PATCH, new_callable=AsyncMock, return_value=ProbeOutcome.REJECTED
    ):
        result, error = await _setup(flow, discovery)
    assert result is None
    assert error == "device_not_registered"
    assert not manager.calls


async def test_undecoded_unbound_classic_is_not_probed(hass: HomeAssistant) -> None:
    """An unbound record cannot be probed: keep the decode error."""
    manufacturer = (
        bytes((ISSUE_155_MANUFACTURER[0] & 0x7F,)) + (ISSUE_155_MANUFACTURER[1:])
    )
    discovery = _discovery(manufacturer=manufacturer)
    flow, manager = _flow(hass, discovery, [make_credentials()])
    with patch.object(flow, "_async_scan_device", return_value=discovery):
        result, error = await flow._async_setup_address(discovery.address)
    assert result is None
    assert error == "identity_not_decoded"
    assert not manager.calls
    assert not manager.candidate_calls


async def test_scan_waits_for_a_decodable_classic_pair(hass: HomeAssistant) -> None:
    """Keep scanning past an undecodable snapshot and accept a decoded one."""
    flow = build_flow(hass)
    undecoded = _discovery()
    decoded = FakeDiscovery()

    async def process(
        _hass: HomeAssistant, predicate: Any, *_args: Any
    ) -> FakeDiscovery:
        assert predicate(undecoded) is False
        assert predicate(decoded) is True
        return decoded

    with patch(_SCAN_PATCH, side_effect=process):
        result = await flow._async_scan_device(decoded.address)
    assert result is cast(Any, decoded)


@pytest.mark.parametrize("classic_seen", [True, False])
async def test_scan_timeout_falls_back_to_classic_record(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture, classic_seen: bool
) -> None:
    """Use the last well-formed classic record only when the scan times out."""
    flow = build_flow(hass)
    first = _discovery()
    latest = _discovery()
    malformed = _discovery(manufacturer=ISSUE_155_MANUFACTURER[:7])

    async def process(_hass: HomeAssistant, predicate: Any, *_args: Any) -> None:
        if classic_seen:
            assert predicate(first) is False
            assert predicate(latest) is False
        assert predicate(malformed) is False
        raise TimeoutError

    with patch(_SCAN_PATCH, side_effect=process), caplog.at_level(logging.DEBUG):
        result = await flow._async_scan_device(first.address)
    if classic_seen:
        assert result is cast(Any, latest)
        assert "active scan timed out, using the last classic record" in caplog.text
    else:
        assert result is None
        assert "active scan timed out" in caplog.text
