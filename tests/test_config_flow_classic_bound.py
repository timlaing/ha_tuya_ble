"""Bound classic records whose identity does not decode (#155)."""

# pylint: disable=protected-access

from __future__ import annotations

from collections.abc import Iterator
import logging
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest

from custom_components.tuya_ble.cloud import HASSTuyaBLEDeviceManager
from custom_components.tuya_ble.config_flow import (
    PROBE_MAX_CANDIDATES,
    TuyaBLEConfigFlow,
)
from custom_components.tuya_ble.config_flow_scan import async_scan_device
from custom_components.tuya_ble.const import (
    CONF_ADVERTISED_UUID,
    CONF_CLOUD_UUID,
    CONF_DEVICE_ID,
    CONF_UUID,
)
from custom_components.tuya_ble.tuya_ble import (
    MANUFACTURER_DATA_ID,
    SERVICE_UUID,
    TuyaBLEDeviceCredentials,
)
from custom_components.tuya_ble.tuya_ble.probe import ProbeOutcome
from tests.conftest import FakeAdvertisementData, FakeBLEAddress, make_credentials
from tests.test_cloud import make_device
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

    def __init__(
        self,
        candidates: list[TuyaBLEDeviceCredentials],
        added_after_login: list[TuyaBLEDeviceCredentials] | None = None,
        refresh_error: Exception | None = None,
    ) -> None:
        super().__init__()
        self.candidates = candidates
        self.added_after_login = added_after_login or []
        self.refresh_error = refresh_error
        self.candidate_calls: list[tuple[str, str | None]] = []

    async def async_refresh(self) -> None:
        """Load devices added to the account after login, or fail."""
        await super().async_refresh()
        if self.refresh_error is not None:
            raise self.refresh_error
        self.candidates = self.candidates + self.added_after_login

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
    **manager_kwargs: Any,
) -> tuple[TuyaBLEConfigFlow, RecordingManager]:
    flow = build_flow(hass)
    manager = RecordingManager(candidates, **manager_kwargs)
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
    assert manager.refreshes == 1
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


def _cloud_flow(
    hass: HomeAssistant, discovery: FakeDiscovery, cloud_devices: list[Any]
) -> TuyaBLEConfigFlow:
    """Drive the flow through the real cloud manager's candidate selection."""
    manager = HASSTuyaBLEDeviceManager(hass, {})
    manager._manager = cast(
        Any,
        SimpleNamespace(
            device_map={device.id: device for device in cloud_devices},
            update_device_cache=Mock(),
        ),
    )
    flow = build_flow(hass)
    flow._manager = manager
    flow._discovered_devices[discovery.address] = discovery  # type: ignore[assignment]
    return flow


def _matches(device_id: str) -> Any:
    """Probe side effect that authenticates only one cloud record."""

    async def probe(
        _device: Any, _adv: Any, credentials: TuyaBLEDeviceCredentials, *_args: Any
    ) -> ProbeOutcome:
        if credentials.device_id == device_id:
            return ProbeOutcome.MATCHED
        return ProbeOutcome.REJECTED

    return probe


async def test_undecoded_bound_classic_hint_matches_other_device(
    hass: HomeAssistant,
) -> None:
    """A hint matching another device's product still probes the real one (#182)."""
    discovery = _discovery()
    decoy = make_device(
        id="a-decoy", uuid="decoy-uuid", product_id="bs3ubslo", local_key="123456"
    )
    real = make_device(
        id="b-real", uuid="real-uuid", product_id="other", local_key="123456"
    )
    flow = _cloud_flow(hass, discovery, [real, decoy])
    with patch(_PROBE_PATCH, side_effect=_matches("b-real")) as probe:
        result, error = await _setup(flow, discovery)
    assert error is None
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_DEVICE_ID] == "b-real"
    assert result["data"][CONF_UUID] == "real-uuid"
    # The hint's record is tried first, then the fallback.
    assert [call.args[2].device_id for call in probe.await_args_list] == [
        "a-decoy",
        "b-real",
    ]


async def test_undecoded_bound_classic_fallbacks_stay_within_cap(
    hass: HomeAssistant,
) -> None:
    """Fallbacks only use the cap's remaining slots; the hint keeps its count."""
    discovery = _discovery()
    devices = [
        make_device(
            id="a-hint", uuid="hint-uuid", product_id="bs3ubslo", local_key="123456"
        )
    ] + [
        make_device(
            id=f"b-other-{index}",
            uuid=f"other-{index}",
            product_id="other",
            local_key="123456",
        )
        for index in range(PROBE_MAX_CANDIDATES + 1)
    ]
    flow = _cloud_flow(hass, discovery, devices)
    with patch(
        _PROBE_PATCH, new_callable=AsyncMock, return_value=ProbeOutcome.REJECTED
    ) as probe:
        result, error = await _setup(flow, discovery)
    assert result is None
    assert error == "device_not_registered"
    assert [call.args[2].device_id for call in probe.await_args_list] == [
        "a-hint",
        *(f"b-other-{index}" for index in range(PROBE_MAX_CANDIDATES - 1)),
    ]


async def test_undecoded_bound_classic_too_many_hint_matches(
    hass: HomeAssistant,
) -> None:
    """More hint-matching records than the cap still probes nothing."""
    discovery = _discovery()
    devices = [
        make_device(
            id=f"hint-{index}",
            uuid=f"hint-{index}",
            product_id="bs3ubslo",
            local_key="123456",
        )
        for index in range(PROBE_MAX_CANDIDATES + 1)
    ]
    flow = _cloud_flow(hass, discovery, devices)
    with patch(_PROBE_PATCH, new_callable=AsyncMock) as probe:
        result, error = await _setup(flow, discovery)
    assert result is None
    assert error == "too_many_candidates"
    probe.assert_not_awaited()


async def test_undecoded_unbound_classic_is_not_probed(hass: HomeAssistant) -> None:
    """An unbound record cannot be probed: keep the decode error."""
    manufacturer = (
        bytes((ISSUE_155_MANUFACTURER[0] & 0x7F,)) + (ISSUE_155_MANUFACTURER[1:])
    )
    discovery = _discovery(manufacturer=manufacturer)
    flow, manager = _flow(hass, discovery, [make_credentials()])
    flow.discovery_info = cast(Any, discovery)
    with patch.object(flow, "_async_scan_device", return_value=discovery):
        result = await flow.async_step_discovered_device({})
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "identity_not_decoded"}
    assert not manager.calls
    assert not manager.refreshes
    assert not manager.candidate_calls


async def test_undecoded_bound_classic_finds_device_added_after_login(
    hass: HomeAssistant,
) -> None:
    """Refresh the cloud list so a device added after login becomes a candidate."""
    discovery = _discovery()
    added = make_credentials(uuid="cloud-uuid", product_id="bs3ubslo", category="kg")
    flow, manager = _flow(hass, discovery, [], added_after_login=[added])
    with patch(_PROBE_PATCH, new_callable=AsyncMock, return_value=ProbeOutcome.MATCHED):
        result, error = await _setup(flow, discovery)
    assert error is None
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_UUID] == "cloud-uuid"
    assert manager.refreshes == 1


async def test_undecoded_bound_classic_refresh_failure(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A failed refresh still probes the cached list, and reports the refresh."""
    discovery = _discovery()
    flow, manager = _flow(
        hass, discovery, [], refresh_error=OSError("private-refresh-failure")
    )
    with patch(_PROBE_PATCH, new_callable=AsyncMock) as probe:
        result, error = await _setup(flow, discovery)
    assert result is None
    assert error == "cloud_refresh_failed"
    assert manager.candidate_calls == [("", "bs3ubslo")]
    probe.assert_not_awaited()
    assert "Tuya cloud refresh failed" in caplog.text
    assert "private-refresh-failure" not in caplog.text


async def test_scan_waits_for_a_decodable_classic_pair(hass: HomeAssistant) -> None:
    """Keep scanning past an undecodable snapshot and accept a decoded one."""
    undecoded = _discovery()
    decoded = FakeDiscovery()

    async def process(
        _hass: HomeAssistant, predicate: Any, *_args: Any
    ) -> FakeDiscovery:
        assert predicate(undecoded) is False
        assert predicate(decoded) is True
        return decoded

    with patch(_SCAN_PATCH, side_effect=process):
        result = await async_scan_device(hass, decoded.address)
    assert result is cast(Any, decoded)


@pytest.mark.parametrize("classic_seen", [True, False])
async def test_scan_timeout_falls_back_to_classic_record(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture, classic_seen: bool
) -> None:
    """Use the last well-formed classic record only when the scan times out."""
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
        result = await async_scan_device(hass, first.address)
    # Scan messages stay on the documented config-flow logger.
    assert {record.name for record in caplog.records} == {
        "custom_components.tuya_ble.config_flow"
    }
    if classic_seen:
        assert result is cast(Any, latest)
        assert "active scan timed out, using the last classic record" in caplog.text
    else:
        assert result is None
        assert "active scan timed out" in caplog.text


async def test_scan_timeout_keeps_a_readable_product_id(hass: HomeAssistant) -> None:
    """A later record without a product ID does not replace one that has it."""
    readable = _discovery()
    product_key = _discovery(service=b"\x01" + bytes(16))
    obfuscated = _discovery(service=b"\x00" + bytes(8))
    newer_readable = _discovery()

    async def process(_hass: HomeAssistant, predicate: Any, *_args: Any) -> None:
        for service_info in (product_key, readable, obfuscated):
            assert predicate(service_info) is False
        raise TimeoutError

    with patch(_SCAN_PATCH, side_effect=process):
        assert await async_scan_device(hass, readable.address) is cast(Any, readable)

    async def process_newer(_hass: HomeAssistant, predicate: Any, *_args: Any) -> None:
        for service_info in (product_key, obfuscated, readable, newer_readable):
            assert predicate(service_info) is False
        raise TimeoutError

    with patch(_SCAN_PATCH, side_effect=process_newer):
        result = await async_scan_device(hass, readable.address)
    assert result is cast(Any, newer_readable)


async def test_scan_timeout_without_product_id_keeps_latest(
    hass: HomeAssistant,
) -> None:
    """Without any readable product ID, the latest classic record is kept."""
    product_key = _discovery(service=b"\x01" + bytes(16))
    obfuscated = _discovery(service=b"\x00" + bytes(8))

    async def process(_hass: HomeAssistant, predicate: Any, *_args: Any) -> None:
        assert predicate(product_key) is False
        assert predicate(obfuscated) is False
        raise TimeoutError

    with patch(_SCAN_PATCH, side_effect=process):
        result = await async_scan_device(hass, obfuscated.address)
    assert result is cast(Any, obfuscated)
