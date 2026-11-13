"""Current BlueZ state supersedes stale flags after real backend cleanup."""

# pylint: disable=protected-access
# cspell:ignore dbus

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

from bleak.backends.bluezdbus import defs
import bleak.backends.bluezdbus.client as bluez_client
from bleak.backends.bluezdbus.client import BleakClientBlueZDBus
from bleak.backends.bluezdbus.manager import BlueZManager
from bleak.backends.bluezdbus.version import BlueZFeatures
from bleak.backends.device import BLEDevice
from bleak.backends.service import BleakGATTServiceCollection
from bleak_retry_connector import BleakConnectionError
from dbus_fast.constants import MessageType
import pytest

from custom_components.tuya_ble.tuya_ble import (
    ProbeOutcome,
    async_probe_authenticated_status,
)
import custom_components.tuya_ble.tuya_ble.probe as probe_module
from custom_components.tuya_ble.tuya_ble.probe import _ProbeDevice
from tests.conftest import make_credentials
from tests.test_probe import _input

PATH = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"


@dataclass
class BlueZStateHarness:
    """Keep real manager properties independent from a backend's removed watcher."""

    manager: BlueZManager = field(default_factory=BlueZManager)
    clients: list[Any] = field(default_factory=list)
    deferred_disconnect: bool = True
    unavailable: bool = False
    hide_backend_link: bool = False
    live_recovery: bool = False
    live_disconnects: list[str] = field(default_factory=list)
    pending_connect: bool = False
    fail_live_disconnect: bool = False
    live_error: str | None = None
    fail_discovery: bool = True
    session_buses: list[Any] = field(default_factory=list)
    notify_disconnect: bool = True

    def set_connected(self, connected: bool, notify: bool = True) -> None:
        """Update transport state; removed per-client watchers cannot see it."""
        self.manager._properties[PATH][defs.DEVICE_INTERFACE]["Connected"] = connected
        if notify and not connected:
            for watcher in tuple(self.manager._device_watchers.get(PATH, ())):
                watcher.on_connected_changed(False)

    async def get_manager(self) -> BlueZManager:
        """Return live state or simulate an unavailable transport manager."""
        if self.unavailable:
            raise OSError("private-manager-failure")
        return self.manager

    async def establish(
        self,
        client_class: Any,
        device: BLEDevice,
        _name: str,
        disconnected_callback: Any,
        **kwargs: Any,
    ) -> Any:
        """Use the real native backend's connect and cleanup implementation."""
        assert kwargs.pop("max_attempts") == 1
        kwargs.pop("use_services_cache")
        kwargs.pop("ble_device_callback")
        client = client_class(
            device,
            backend=BleakClientBlueZDBus,
            bluez={},
            disconnected_callback=disconnected_callback,
            **kwargs,
        )
        self.clients.append(client)
        await client.connect()
        return client


class SyntheticBus:
    """Replace only D-Bus transport beneath the real native BlueZ implementation."""

    def __init__(self, harness: BlueZStateHarness, live: bool = False) -> None:
        self.harness = harness
        self.live = live
        self.closed = False
        if not live:
            harness.session_buses.append(self)

    async def connect(self) -> None:
        """No real system bus is opened in these socket-disabled tests."""

    async def call(self, message: Any) -> Any:
        """Acknowledge calls while optionally deferring the physical disconnect."""
        if self.closed and not self.live:
            raise OSError("old session bus is closed")
        if message.member == "Connect":
            if self.harness.pending_connect and len(self.harness.clients) == 1:
                raise ConnectionError("Connect reply lost while request is pending")
            self.harness.set_connected(True)
        elif message.member == "Disconnect":
            if self.live:
                self.harness.live_disconnects.append(message.path)
                if self.harness.fail_live_disconnect:
                    raise OSError("Live Disconnect failed")
                if self.harness.live_error is not None:
                    return SimpleNamespace(
                        message_type=MessageType.ERROR,
                        error_name=self.harness.live_error,
                        body=["Disconnect rejected"],
                    )
                self.harness.pending_connect = False
            elif self.harness.pending_connect:
                raise OSError("Session Disconnect failed")
            if not self.harness.deferred_disconnect or (
                self.live and self.harness.live_recovery
            ):
                self.harness.set_connected(False, notify=self.harness.notify_disconnect)
        return SimpleNamespace(message_type=MessageType.METHOD_RETURN)

    def disconnect(self) -> None:
        """Close a session bus without changing physical transport state."""
        self.closed = True

    async def wait_for_disconnect(self) -> None:
        """Session-bus shutdown is immediate and independent from the device link."""


@pytest.fixture(name="bluez_state")
def fixture_bluez_state(monkeypatch: pytest.MonkeyPatch) -> BlueZStateHarness:
    """Run real BlueZ connect and cleanup over an independent synthetic bus."""
    harness = BlueZStateHarness()
    harness.manager._properties[PATH] = {
        defs.DEVICE_INTERFACE: {
            "Connected": False,
            "Address": "AA:BB:CC:DD:EE:FF",
            "Alias": "probe-test",
        }
    }
    harness.manager._bus = cast(Any, SyntheticBus(harness, live=True))

    async def services(
        backend: BleakClientBlueZDBus, **_kwargs: Any
    ) -> BleakGATTServiceCollection:
        """Fail initial discovery only after real connect has opened its link."""
        if len(harness.clients) == 1 and harness.fail_discovery:
            if harness.hide_backend_link:
                cast(Any, backend)._is_connected = False
            raise BleakConnectionError("private-service-discovery-failure")
        backend.services = BleakGATTServiceCollection()
        return backend.services

    async def read(
        device: _ProbeDevice, _received: Any, _enable_status: Any
    ) -> ProbeOutcome:
        """Use real establishment without issuing hardware protocol commands."""
        client = await device._try_establish_connection()
        assert client is not None
        device._client = client
        return ProbeOutcome.MATCHED

    monkeypatch.setattr(BlueZFeatures, "checked_bluez_version", True)
    monkeypatch.setattr(BlueZFeatures, "supported_version", True)
    monkeypatch.setattr(
        bluez_client, "MessageBus", lambda **_kwargs: SyntheticBus(harness)
    )
    monkeypatch.setattr(bluez_client, "get_global_bluez_manager", harness.get_manager)
    monkeypatch.setattr(
        "bleak.backends.bluezdbus.manager.get_global_bluez_manager", harness.get_manager
    )
    monkeypatch.setattr(BleakClientBlueZDBus, "_get_services", services)
    monkeypatch.setattr(probe_module, "establish_connection", harness.establish)
    monkeypatch.setattr(probe_module, "_pending_probe_cleanup", {})
    monkeypatch.setattr(probe_module, "PROBE_CLEANUP_TIMEOUT", 0.05)
    monkeypatch.setattr(_ProbeDevice, "authenticated_read", read)
    monkeypatch.setattr(probe_module._ProbeClient, "stop_notify", AsyncMock())
    return harness


@pytest.mark.parametrize("recovery", ["external_close", "live_bus"])
async def test_real_bluez_stale_backend_flag_does_not_block_later_probe(
    bluez_state: BlueZStateHarness, recovery: str
) -> None:
    """A removed watcher leaves a stale flag, but current state safely releases it."""
    _, advertisement = _input()
    device = BLEDevice("AA:BB:CC:DD:EE:FF", "probe-test", {"path": PATH})
    first = await async_probe_authenticated_status(
        device, advertisement, make_credentials(), "1234567890abcdef", frozenset({1})
    )
    assert first == ProbeOutcome.RETRYABLE
    backend = bluez_state.clients[0]._probe_backends[0]
    assert isinstance(backend, BleakClientBlueZDBus)
    assert backend._remove_device_watcher is None
    assert backend.is_connected
    assert bluez_state.manager.is_connected(PATH)
    blocked = await async_probe_authenticated_status(
        device, advertisement, make_credentials(), "1234567890abcdef", frozenset({1})
    )
    assert blocked == ProbeOutcome.RETRYABLE
    assert len(bluez_state.clients) == 1
    bluez_state.deferred_disconnect = False
    if recovery == "external_close":
        bluez_state.set_connected(False)
        assert backend.is_connected
        assert not bluez_state.manager.is_connected(PATH)
    resumed = await async_probe_authenticated_status(
        device, advertisement, make_credentials(), "1234567890abcdef", frozenset({1})
    )
    assert resumed == ProbeOutcome.MATCHED
    assert len(bluez_state.clients) == 2
    assert not bluez_state.manager.is_connected(PATH)
    assert not probe_module._pending_probe_cleanup


async def test_pending_native_connect_requires_acknowledged_cancellation(
    bluez_state: BlueZStateHarness,
) -> None:
    """Connected=False cannot release a pending native request after bus failure."""
    bluez_state.pending_connect = True
    bluez_state.fail_live_disconnect = True
    _, advertisement = _input()
    device = BLEDevice("AA:BB:CC:DD:EE:FF", "probe-test", {"path": PATH})
    first = await async_probe_authenticated_status(
        device, advertisement, make_credentials(), "1234567890abcdef", frozenset({1})
    )
    assert first == ProbeOutcome.RETRYABLE
    assert not bluez_state.manager.is_connected(PATH)
    assert bluez_state.pending_connect
    blocked = await async_probe_authenticated_status(
        device, advertisement, make_credentials(), "1234567890abcdef", frozenset({1})
    )
    assert blocked == ProbeOutcome.RETRYABLE
    assert len(bluez_state.clients) == 1
    # BlueZ could complete the original request after its local coroutine exited.
    bluez_state.set_connected(True)
    bluez_state.fail_live_disconnect = False
    bluez_state.live_recovery = True
    bluez_state.deferred_disconnect = False
    resumed = await async_probe_authenticated_status(
        device, advertisement, make_credentials(), "1234567890abcdef", frozenset({1})
    )
    assert resumed == ProbeOutcome.MATCHED
    assert not bluez_state.pending_connect
    assert not bluez_state.manager.is_connected(PATH)
    assert len(bluez_state.clients) == 2
    assert all(bus.closed for bus in bluez_state.session_buses)
    assert not probe_module._pending_probe_cleanup


async def test_native_session_bus_closes_after_notification_shutdown_failure(
    bluez_state: BlueZStateHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Physical recovery also releases the session that stop_notify abandoned."""
    bluez_state.fail_discovery = False
    bluez_state.live_recovery = True
    monkeypatch.setattr(
        probe_module._ProbeClient,
        "stop_notify",
        AsyncMock(side_effect=OSError("notification shutdown failed")),
    )
    _, advertisement = _input()
    device = BLEDevice("AA:BB:CC:DD:EE:FF", "probe-test", {"path": PATH})
    result = await async_probe_authenticated_status(
        device, advertisement, make_credentials(), "1234567890abcdef", frozenset({1})
    )
    assert result == ProbeOutcome.INCONCLUSIVE
    assert not bluez_state.manager.is_connected(PATH)
    assert all(bus.closed for bus in bluez_state.session_buses)
    assert bluez_state.clients[0]._probe_backends[0]._bus is None
    assert all(
        task.done() and client is not None and not client.is_connected
        for task, client in probe_module._pending_probe_cleanup.values()
    )


async def test_forced_native_teardown_completes_monitor_without_disconnect_callback(
    bluez_state: BlueZStateHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lost property callback cannot leave the native disconnect monitor pending."""
    previous_tasks = set(bluez_client._background_tasks)
    bluez_state.fail_discovery = False
    bluez_state.live_recovery = True
    bluez_state.notify_disconnect = False
    monkeypatch.setattr(
        probe_module._ProbeClient,
        "stop_notify",
        AsyncMock(side_effect=OSError("notification shutdown failed")),
    )
    _, advertisement = _input()
    device = BLEDevice("AA:BB:CC:DD:EE:FF", "probe-test", {"path": PATH})
    result = await async_probe_authenticated_status(
        device, advertisement, make_credentials(), "1234567890abcdef", frozenset({1})
    )
    await asyncio.sleep(0)
    assert result == ProbeOutcome.INCONCLUSIVE
    backend = bluez_state.clients[0]._probe_backends[0]
    assert backend._disconnect_monitor_event is None
    assert backend._remove_device_watcher is None
    assert backend._bus is None
    assert not bluez_state.manager.is_connected(PATH)
    assert all(task.done() for task in bluez_client._background_tasks - previous_tasks)


@pytest.mark.parametrize("failure", ["dbus_error", "missing_bus", "removed_object"])
async def test_native_cancellation_requires_a_valid_live_bus_reply(
    bluez_state: BlueZStateHarness, failure: str
) -> None:
    """Only successful cancellation or a removed device can release a pending link."""
    bluez_state.pending_connect = True
    bus = bluez_state.manager._bus
    if failure == "missing_bus":
        bluez_state.manager._bus = None
    else:
        bluez_state.live_error = (
            "org.freedesktop.DBus.Error.UnknownObject"
            if failure == "removed_object"
            else "org.bluez.Error.Failed"
        )
    _, advertisement = _input()
    device = BLEDevice("AA:BB:CC:DD:EE:FF", "probe-test", {"path": PATH})
    result = await async_probe_authenticated_status(
        device, advertisement, make_credentials(), "1234567890abcdef", frozenset({1})
    )
    assert result == ProbeOutcome.RETRYABLE
    if failure == "removed_object":
        assert not probe_module._pending_probe_cleanup
        assert all(session.closed for session in bluez_state.session_buses)
    else:
        assert probe_module._pending_probe_cleanup
        bluez_state.manager._bus = bus
        bluez_state.live_error = None
        bluez_state.deferred_disconnect = False
        resumed = await async_probe_authenticated_status(
            device,
            advertisement,
            make_credentials(),
            "1234567890abcdef",
            frozenset({1}),
        )
        assert resumed == ProbeOutcome.MATCHED
        assert not probe_module._pending_probe_cleanup


async def test_unavailable_bluez_state_cannot_confirm_link_closure(
    bluez_state: BlueZStateHarness,
) -> None:
    """Failure to query live state cannot unblock an unconfirmed connection."""
    _, advertisement = _input()
    device = BLEDevice("AA:BB:CC:DD:EE:FF", "probe-test", {"path": PATH})
    await async_probe_authenticated_status(
        device, advertisement, make_credentials(), "1234567890abcdef", frozenset({1})
    )
    bluez_state.set_connected(False)
    bluez_state.unavailable = True
    blocked = await async_probe_authenticated_status(
        device, advertisement, make_credentials(), "1234567890abcdef", frozenset({1})
    )
    assert blocked == ProbeOutcome.RETRYABLE
    assert len(bluez_state.clients) == 1


async def test_final_probe_recovers_native_link_hidden_by_backend_flag(
    bluez_state: BlueZStateHarness,
) -> None:
    """Cleanup closes a detected native link without requiring another probe."""
    bluez_state.hide_backend_link = True
    bluez_state.live_recovery = True
    _, advertisement = _input()
    device = BLEDevice("AA:BB:CC:DD:EE:FF", "probe-test", {"path": PATH})
    result = await async_probe_authenticated_status(
        device, advertisement, make_credentials(), "1234567890abcdef", frozenset({1})
    )
    assert result == ProbeOutcome.RETRYABLE
    assert len(bluez_state.clients) == 1
    assert bluez_state.live_disconnects == [PATH]
    assert not bluez_state.manager.is_connected(PATH)
    assert not probe_module._pending_probe_cleanup
