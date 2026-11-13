"""The real Home Assistant wrapper cannot hide a probe's leaked backend link."""

# pylint: disable=protected-access
# cspell:ignore habluetooth

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock
from weakref import WeakSet

from bleak.backends.device import BLEDevice
from bleak_retry_connector import BleakConnectionError
from habluetooth.usage import HaBleakClientWithServiceCache
from habluetooth.wrappers import HaBleakClientWrapper
import pytest

from custom_components.tuya_ble.tuya_ble import (
    ProbeOutcome,
    async_probe_authenticated_status,
)
import custom_components.tuya_ble.tuya_ble.probe as probe_module
from custom_components.tuya_ble.tuya_ble.probe import _ProbeClient, _ProbeDevice
from tests.conftest import make_credentials
from tests.test_probe import _input


@dataclass
class LeakedBackend:
    """Model a physical link that survives a failed connect and delayed shutdown."""

    fail_connect: bool
    is_connected: bool = False
    disconnect_cancelled: asyncio.Event = field(default_factory=asyncio.Event)
    release_disconnect: asyncio.Event = field(default_factory=asyncio.Event)

    async def connect(self, *_args: Any, **_kwargs: Any) -> None:
        """Open the link before reporting failed service discovery."""
        self.is_connected = True
        if self.fail_connect:
            raise BleakConnectionError("private-connect-failure")

    async def disconnect(self) -> None:
        """Delay actual release even after the caller cancels cleanup."""
        if self.fail_connect:
            try:
                await self.release_disconnect.wait()
            except asyncio.CancelledError:
                self.disconnect_cancelled.set()
                await self.release_disconnect.wait()
        self.is_connected = False


@dataclass
class HaWrapperHarness:
    """Use real wrapper selection and backend dropping with deterministic adapters."""

    original_wait: Callable[[str], Awaitable[bool]]
    backends: list[LeakedBackend] = field(
        default_factory=lambda: [LeakedBackend(True), LeakedBackend(False)]
    )
    clients: list[Any] = field(default_factory=list)
    created_backends: list[LeakedBackend] = field(default_factory=list)
    retry_started: asyncio.Event = field(default_factory=asyncio.Event)
    waits: int = 0

    def create_backend(self, *_args: Any, **_kwargs: Any) -> LeakedBackend:
        """Ensure the previous link is closed before selecting another adapter."""
        if self.created_backends:
            assert not self.backends[0].is_connected
        backend = self.backends[len(self.created_backends)]
        self.created_backends.append(backend)
        return backend

    async def establish(
        self,
        client_class: Any,
        device: BLEDevice,
        _name: str,
        disconnected_callback: Any,
        **kwargs: Any,
    ) -> Any:
        """Keep the connector's public ordering without real adapter backoff."""
        assert kwargs.pop("max_attempts") == 1
        kwargs.pop("use_services_cache")
        kwargs.pop("ble_device_callback")
        client = client_class(
            device,
            disconnected_callback=disconnected_callback,
            _is_retry_client=True,
            **kwargs,
        )
        self.clients.append(client)
        await client.connect()
        return client

    async def wait(self, address: str) -> bool:
        """Expose the second public probe waiting for the first leaked link."""
        self.waits += 1
        if self.waits == 2:
            self.retry_started.set()
        return await self.original_wait(address)


@pytest.fixture(name="ha_wrapper_harness")
def fixture_ha_wrapper_harness(monkeypatch: pytest.MonkeyPatch) -> HaWrapperHarness:
    """Layer the probe subclass on the real HA wrapper, not a wrapper imitation."""
    harness = HaWrapperHarness(probe_module._async_wait_for_previous_cleanup)
    scanner = SimpleNamespace(
        source="probe-test",
        name="probe-test",
        _clients=WeakSet(),
        _add_connecting=Mock(),
        _finished_connecting=Mock(),
        get_discovered_device_advertisement_data=Mock(
            return_value=(
                BLEDevice("AA:BB:CC:DD:EE:FF", "probe-test", {}),
                SimpleNamespace(rssi=-60),
            )
        ),
    )
    manager = SimpleNamespace(
        shutdown=False, async_scanner_by_source=Mock(return_value=scanner)
    )

    class HaProbeClient(_ProbeClient, HaBleakClientWithServiceCache):
        """Exercise the same Home Assistant wrapper inherited in production."""

    def select_backend(_client: Any, _manager: Any) -> Any:
        """Provide a backend factory while retaining real wrapper connect logic."""
        return SimpleNamespace(
            device=BLEDevice("AA:BB:CC:DD:EE:FF", "probe-test", {}),
            scanner=scanner,
            client=harness.create_backend,
            source=scanner.source,
            backend_name="test",
        )

    async def read(
        device: _ProbeDevice, _received: asyncio.Future[None], _enable_status: Any
    ) -> ProbeOutcome:
        """Exercise owned client construction and real wrapper establishment."""
        client = await device._try_establish_connection()
        assert client is not None
        device._client = client
        return ProbeOutcome.MATCHED

    async def stop_notify(_client: Any, _characteristic: Any) -> None:
        """No real notification subscription is opened by this transport test."""

    monkeypatch.setattr("habluetooth.wrappers.get_manager", lambda: manager)
    monkeypatch.setattr(
        HaBleakClientWrapper,
        "_async_get_best_available_backend_and_device",
        select_backend,
    )
    monkeypatch.setattr(HaBleakClientWrapper, "_load_conn_params", Mock())
    monkeypatch.setattr(HaProbeClient, "stop_notify", stop_notify)
    monkeypatch.setattr(probe_module, "_ProbeClient", HaProbeClient)
    monkeypatch.setattr(probe_module, "establish_connection", harness.establish)
    monkeypatch.setattr(probe_module, "_async_wait_for_previous_cleanup", harness.wait)
    monkeypatch.setattr(probe_module, "_pending_probe_cleanup", {})
    monkeypatch.setattr(probe_module, "PROBE_CLEANUP_TIMEOUT", 0.05)
    monkeypatch.setattr(_ProbeDevice, "authenticated_read", read)
    return harness


async def test_discarded_ha_backend_stays_owned_until_physical_disconnection(
    ha_wrapper_harness: HaWrapperHarness,
) -> None:
    """Two public probes cannot overlap when HA drops a still-connected backend."""
    _, advertisement = _input()
    device = BLEDevice("AA:BB:CC:DD:EE:FF", "probe-test", {})
    first = asyncio.create_task(
        async_probe_authenticated_status(
            device,
            advertisement,
            make_credentials(),
            "1234567890abcdef",
            frozenset({1}),
        )
    )
    await ha_wrapper_harness.backends[0].disconnect_cancelled.wait()
    assert await first == ProbeOutcome.RETRYABLE
    client = ha_wrapper_harness.clients[0]
    assert client._backend is None
    assert cast(Any, HaBleakClientWrapper).is_connected.fget(client) is False
    assert client.is_connected
    second = asyncio.create_task(
        async_probe_authenticated_status(
            device,
            advertisement,
            make_credentials(),
            "1234567890abcdef",
            frozenset({1}),
        )
    )
    try:
        await ha_wrapper_harness.retry_started.wait()
        assert len(ha_wrapper_harness.created_backends) == 1
        assert ha_wrapper_harness.backends[0].is_connected
    finally:
        ha_wrapper_harness.backends[0].release_disconnect.set()
    assert await second == ProbeOutcome.MATCHED
    assert len(ha_wrapper_harness.created_backends) == 2
    assert all(not backend.is_connected for backend in ha_wrapper_harness.backends)
    assert not probe_module._pending_probe_cleanup
