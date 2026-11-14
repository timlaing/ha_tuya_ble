"""Probe failures advance identities after at most one transient-failure retry."""

# pylint: disable=protected-access

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
import logging
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import AbortFlow, FlowResultType
import pytest

from custom_components.tuya_ble.config_flow import _probe_state
from custom_components.tuya_ble.const import CONF_DEVICE_ID, CONF_UUID
from custom_components.tuya_ble.tuya_ble import ProbeOutcome
import custom_components.tuya_ble.tuya_ble.probe as probe_module
from custom_components.tuya_ble.tuya_ble.probe import _ProbeDevice
from tests.conftest import FakeBleakClient, make_credentials
from tests.test_config_flow_probe import _flow, _setup


@dataclass
class CleanupRetryGate:
    """Hold cancellation-resistant cleanup while a retry waits to connect."""

    original_wait: Callable[[str], Awaitable[bool]]
    clients: list[FakeBleakClient] = field(
        default_factory=lambda: [FakeBleakClient(), FakeBleakClient()]
    )
    devices: list[_ProbeDevice] = field(default_factory=list)
    release: asyncio.Event = field(default_factory=asyncio.Event)
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)
    retry_started: asyncio.Event = field(default_factory=asyncio.Event)
    finished: asyncio.Event = field(default_factory=asyncio.Event)
    waits: int = 0

    async def authenticated_read(
        self, device: _ProbeDevice, _received: asyncio.Future[None], _enable_status: Any
    ) -> ProbeOutcome:
        """Reject any new connection while an earlier connection remains active."""
        if self.devices:
            assert self.finished.is_set()
            assert not self.clients[0].is_connected
        device._client = self.clients[len(self.devices)]  # type: ignore[assignment]
        self.devices.append(device)
        if len(self.devices) == 1:
            raise TimeoutError()
        return ProbeOutcome.MATCHED

    async def close_probe(self, device: _ProbeDevice) -> None:
        """Simulate a backend that continues disconnecting after cancellation."""
        if device is self.devices[0]:
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                await self.release.wait()
            await self.clients[0].disconnect()
            self.finished.set()
        else:
            await self.clients[1].disconnect()

    async def wait_for_cleanup(self, address: str) -> bool:
        """Expose the retry's wait so removal or completion can be driven safely."""
        self.waits += 1
        if self.waits == 2:
            self.retry_started.set()
        return await self.original_wait(address)


@pytest.mark.parametrize(
    ("first_failure", "attempts"),
    [
        (ProbeOutcome.INCONCLUSIVE, 2),
        (ProbeOutcome.RETRYABLE, 4),
        (OSError("private-probe-failure"), 2),
        (TimeoutError("private-probe-failure"), 4),
        (ConnectionError("private-probe-failure"), 4),
    ],
)
async def test_failed_candidate_does_not_block_later_credentials(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
    first_failure: ProbeOutcome | Exception,
    attempts: int,
) -> None:
    """Exhaust one failed credential's UUIDs before verifying the next credential."""
    candidates = [
        make_credentials(uuid="cloud-a", device_id="device-a", product_id="prod"),
        make_credentials(uuid="cloud-b", device_id="device-b", product_id="prod"),
    ]
    flow, discovery = _flow(hass, candidates)
    with (
        patch(
            "custom_components.tuya_ble.config_flow.async_probe_authenticated_status",
            new_callable=AsyncMock,
            side_effect=[first_failure] * attempts + [ProbeOutcome.MATCHED],
        ) as probe,
        caplog.at_level(logging.DEBUG),
    ):
        result, error = await _setup(flow, discovery)
    assert error is None
    assert result["data"][CONF_DEVICE_ID] == "device-b"
    assert result["data"][CONF_UUID] == "1234567890abcdef"
    assert probe.await_count == attempts + 1
    assert [call.args[2].device_id for call in probe.await_args_list] == [
        "device-a"
    ] * attempts + ["device-b"]
    assert [call.args[3] for call in probe.await_args_list] == ["1234567890abcdef"] * (
        attempts // 2
    ) + ["cloud-a"] * (attempts // 2) + ["1234567890abcdef"]
    assert "device-a" not in _probe_state(hass)[1]
    assert "private-probe-failure" not in caplog.text


@pytest.mark.parametrize(
    ("first_failure", "selected"),
    [
        (TimeoutError("private-probe-failure"), "1234567890abcdef"),
        (ConnectionError("private-probe-failure"), "1234567890abcdef"),
        (OSError("private-probe-failure"), "cloud-uuid"),
    ],
)
async def test_probe_exception_retry_policy(
    hass: HomeAssistant, first_failure: Exception, selected: str
) -> None:
    """Retry transient exceptions once; other exceptions immediately advance UUID."""
    candidate = make_credentials(uuid="cloud-uuid", product_id="prod")
    flow, discovery = _flow(hass, [candidate])
    with patch(
        "custom_components.tuya_ble.config_flow.async_probe_authenticated_status",
        new_callable=AsyncMock,
        side_effect=[first_failure, ProbeOutcome.MATCHED],
    ) as probe:
        result, error = await _setup(flow, discovery)
    assert error is None
    assert result["data"][CONF_UUID] == selected
    assert [call.args[3] for call in probe.await_args_list] == [
        "1234567890abcdef",
        selected,
    ]


@pytest.mark.parametrize(
    ("outcome", "attempts", "error"),
    [
        (ProbeOutcome.REJECTED, 1, "device_not_registered"),
        (ProbeOutcome.INCONCLUSIVE, 1, "bluetooth_probe_failed"),
        (ProbeOutcome.RETRYABLE, 2, "bluetooth_probe_failed"),
    ],
)
async def test_equal_cloud_and_advertised_uuids_are_not_probed_twice(
    hass: HomeAssistant, outcome: ProbeOutcome, attempts: int, error: str
) -> None:
    """An identical UUID gets only one initial attempt and at most one retry."""
    flow, discovery = _flow(hass, [make_credentials(product_id="prod")])
    with patch(
        "custom_components.tuya_ble.config_flow.async_probe_authenticated_status",
        new_callable=AsyncMock,
        return_value=outcome,
    ) as probe:
        result, failure = await _setup(flow, discovery)
    assert result is None
    assert failure == error
    assert probe.await_count == attempts
    assert [call.args[3] for call in probe.await_args_list] == [
        "1234567890abcdef"
    ] * attempts
    assert _probe_state(hass)[1] == {}


@pytest.mark.parametrize("cleanup_result", ["finish", "cancel", "remain_pending"])
async def test_retry_waits_for_cancellation_resistant_cleanup(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, cleanup_result: str
) -> None:
    """Cancellation-resistant cleanup cannot overlap a new connection."""
    flow, discovery = _flow(
        hass, [make_credentials(uuid="cloud-uuid", product_id="prod")]
    )
    gate = CleanupRetryGate(probe_module._async_wait_for_previous_cleanup)
    monkeypatch.setattr(probe_module, "_pending_probe_cleanup", {})
    monkeypatch.setattr(probe_module, "PROBE_CLEANUP_TIMEOUT", 0.05)

    async def authenticated_read(
        device: _ProbeDevice, received: asyncio.Future[None], enable_status: Any
    ) -> ProbeOutcome:
        """Bind the gate's read double to the isolated probe device."""
        return await gate.authenticated_read(device, received, enable_status)

    async def close_probe(device: _ProbeDevice) -> None:
        """Bind the gate's cancellation-resistant cleanup to the probe device."""
        await gate.close_probe(device)

    monkeypatch.setattr(_ProbeDevice, "authenticated_read", authenticated_read)
    monkeypatch.setattr(_ProbeDevice, "close_probe", close_probe)
    monkeypatch.setattr(
        probe_module, "_async_wait_for_previous_cleanup", gate.wait_for_cleanup
    )
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
    ):
        progress = await flow.async_step_device({"address": discovery.address})
        assert progress["type"] == FlowResultType.SHOW_PROGRESS
        await gate.cancelled.wait()
        await gate.retry_started.wait()
        assert len(gate.devices) == 1
        assert gate.clients[0].is_connected
        assert not gate.finished.is_set()
        task = flow._probe_task
        assert task is not None
        try:
            if cleanup_result == "cancel":
                flow.async_remove()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                if cleanup_result == "finish":
                    gate.release.set()
                await task
                result = await flow.async_step_probe_finish()
                if cleanup_result == "finish":
                    assert result["type"] == FlowResultType.CREATE_ENTRY
                    assert result["data"][CONF_UUID] == "1234567890abcdef"
                else:
                    assert result["type"] == FlowResultType.FORM
                    assert result["errors"] is not None
                    assert result["errors"]["base"] == "bluetooth_probe_failed"
                    assert gate.clients[0].is_connected
                    assert gate.waits == 4
        finally:
            gate.release.set()
            await asyncio.wait_for(gate.finished.wait(), 0.5)
            await hass.async_block_till_done()
    assert len(gate.devices) == (2 if cleanup_result == "finish" else 1)
    assert all(not client.is_connected for client in gate.clients[: len(gate.devices)])
    assert not probe_module._pending_probe_cleanup


async def test_retry_budget_is_bounded_across_all_candidates(
    hass: HomeAssistant,
) -> None:
    """Four credentials with two UUIDs each cannot exceed sixteen probe attempts."""
    candidates = [
        make_credentials(uuid=f"cloud-{i}", device_id=f"device-{i}", product_id="prod")
        for i in range(4)
    ]
    flow, discovery = _flow(hass, candidates)
    with patch(
        "custom_components.tuya_ble.config_flow.async_probe_authenticated_status",
        new_callable=AsyncMock,
        return_value=ProbeOutcome.RETRYABLE,
    ) as probe:
        result, error = await _setup(flow, discovery)
    assert result is None
    assert error == "bluetooth_probe_failed"
    assert probe.await_count == 16
    assert [call.args[2].device_id for call in probe.await_args_list] == [
        candidate.device_id for candidate in candidates for _ in range(4)
    ]
    assert _probe_state(hass)[1] == {}


async def test_candidate_setup_error_advances_to_next_credentials(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A malformed candidate cannot prevent a later candidate's authenticated match."""
    candidates = [
        make_credentials(uuid="cloud-a", device_id="device-a", product_id="prod"),
        make_credentials(uuid="cloud-b", device_id="device-b", product_id="prod"),
    ]
    flow, discovery = _flow(hass, candidates)
    with (
        patch.object(flow, "_async_scan_device", return_value=discovery),
        patch(
            "custom_components.tuya_ble.config_flow.async_setup_registry",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.tuya_ble.config_flow.get_mapped_dp_ids",
            side_effect=[ValueError("private-candidate-failure"), frozenset({1})],
        ),
        patch(
            "custom_components.tuya_ble.config_flow.async_probe_authenticated_status",
            new_callable=AsyncMock,
            return_value=ProbeOutcome.MATCHED,
        ) as probe,
        caplog.at_level(logging.DEBUG),
    ):
        progress = await flow.async_step_device({"address": discovery.address})
        assert progress["type"] == FlowResultType.SHOW_PROGRESS
        assert flow._probe_task is not None
        await flow._probe_task
        result = await flow.async_step_probe_finish()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_DEVICE_ID] == "device-b"
    probe.assert_awaited_once()
    assert "private-candidate-failure" not in caplog.text


@pytest.mark.parametrize("completed_attempts", [0, 1])
@pytest.mark.parametrize("error_type", [asyncio.CancelledError, AbortFlow])
async def test_probe_cancellation_or_abort_never_advances_candidates(
    hass: HomeAssistant,
    completed_attempts: int,
    error_type: type[BaseException],
) -> None:
    """Cancellation and flow aborts are not treated as errors to retry or skip."""
    candidates = [
        make_credentials(uuid="cloud-a", device_id="device-a", product_id="prod"),
        make_credentials(uuid="cloud-b", device_id="device-b", product_id="prod"),
    ]
    flow, discovery = _flow(hass, candidates)
    with (
        patch(
            "custom_components.tuya_ble.config_flow.async_probe_authenticated_status",
            new_callable=AsyncMock,
            side_effect=[ProbeOutcome.RETRYABLE] * completed_attempts
            + [error_type("unknown")],
        ) as probe,
        patch.object(flow, "async_create_entry") as create_entry,
    ):
        with pytest.raises(error_type):
            await _setup(flow, discovery)
        create_entry.assert_not_called()
    assert probe.await_count == completed_attempts + 1
    assert [call.args[3] for call in probe.await_args_list] == ["1234567890abcdef"] * (
        completed_attempts + 1
    )
    assert _probe_state(hass)[1] == {}


async def test_removing_flow_during_retry_stops_remaining_uuids(
    hass: HomeAssistant,
) -> None:
    """Removing a flow during its one retry cannot start another pairing UUID."""
    flow, discovery = _flow(
        hass, [make_credentials(uuid="cloud-uuid", product_id="prod")]
    )
    started = asyncio.Event()
    attempts = 0

    async def probe_attempt(*_args: Any) -> ProbeOutcome:
        """Hold the retry so the public removal hook can cancel it."""
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return ProbeOutcome.RETRYABLE
        started.set()
        await asyncio.Event().wait()
        return ProbeOutcome.INCONCLUSIVE

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
            side_effect=probe_attempt,
        ) as probe,
    ):
        progress = await flow.async_step_device({"address": discovery.address})
        assert progress["type"] == FlowResultType.SHOW_PROGRESS
        await started.wait()
        task = flow._probe_task
        assert task is not None
        flow.async_remove()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert probe.await_count == 2
    assert [call.args[3] for call in probe.await_args_list] == ["1234567890abcdef"] * 2
    assert _probe_state(hass)[1] == {}
