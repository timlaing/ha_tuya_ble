"""Probe retries retain ownership of links opened before service discovery ends."""

# pylint: disable=protected-access

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from bleak.backends.device import BLEDevice
from bleak_retry_connector import BleakClientWithServiceCache
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest

from custom_components.tuya_ble.const import CONF_UUID
from custom_components.tuya_ble.tuya_ble import (
    ProbeOutcome,
    async_probe_authenticated_status,
)
import custom_components.tuya_ble.tuya_ble.probe as probe_module
from custom_components.tuya_ble.tuya_ble.probe import _ProbeClient, _ProbeDevice
from tests.conftest import make_credentials
from tests.test_config_flow_probe import _flow
from tests.test_probe import _input


@dataclass
class PartialDiscoveryBackend:
    """Open a fake link before discovery, then delay physical disconnection."""

    partial: bool = True
    is_connected: bool = False
    connect_calls: int = 0
    opened: asyncio.Event = field(default_factory=asyncio.Event)
    discovery: asyncio.Event = field(default_factory=asyncio.Event)
    release_disconnect: asyncio.Event = field(default_factory=asyncio.Event)
    disconnect_cancelled: asyncio.Event = field(default_factory=asyncio.Event)

    async def connect(self) -> None:
        """Expose the physical link before service discovery completes."""
        self.connect_calls += 1
        self.is_connected = True
        self.opened.set()
        if self.partial:
            await self.discovery.wait()

    async def disconnect(self) -> None:
        """Keep a partial link open until its delayed backend shutdown completes."""
        if self.partial:
            try:
                await self.release_disconnect.wait()
            except asyncio.CancelledError:
                self.disconnect_cancelled.set()
                await self.release_disconnect.wait()
        self.is_connected = False


@dataclass
class OwnershipHarness:
    """Construct actual probe clients over a backend double, without a BLE adapter."""

    original_wait: Callable[[str], Awaitable[bool]]
    backends: list[PartialDiscoveryBackend] = field(
        default_factory=lambda: [
            PartialDiscoveryBackend(),
            PartialDiscoveryBackend(partial=False),
        ]
    )
    clients: list[BleakClientWithServiceCache] = field(default_factory=list)
    devices: list[_ProbeDevice] = field(default_factory=list)
    retry_started: asyncio.Event = field(default_factory=asyncio.Event)
    waits: int = 0
    internal_retry: str | None = None

    async def establish(
        self,
        client_class: type[_ProbeClient],
        device: BLEDevice,
        _name: str,
        disconnected_callback: Any,
        *,
        max_attempts: int,
        use_services_cache: bool,
        ble_device_callback: Any,
        **kwargs: Any,
    ) -> BleakClientWithServiceCache:
        """Mirror the connector's construct-before-connect ownership ordering."""
        assert max_attempts == 1
        assert use_services_cache
        assert ble_device_callback() is device
        client = client_class(
            device, disconnected_callback=disconnected_callback, **kwargs
        )
        await client.connect()
        if self.internal_retry == "connect":
            await client.connect()
        elif self.internal_retry == "new_client":
            client_class(device, disconnected_callback=disconnected_callback, **kwargs)
        return client

    async def read(
        self, device: _ProbeDevice, _received: asyncio.Future[None], _enable_status: Any
    ) -> ProbeOutcome:
        """Use real client establishment, stopping before hardware protocol I/O."""
        if self.devices:
            assert not self.backends[0].is_connected
        self.devices.append(device)
        client = await device._try_establish_connection()
        assert client is not None
        device._client = client
        return ProbeOutcome.MATCHED

    async def wait(self, address: str) -> bool:
        """Signal when the next probe waits for the partially established link."""
        self.waits += 1
        if self.waits == 2:
            self.retry_started.set()
        return await self.original_wait(address)


@pytest.fixture(name="ownership_harness")
def fixture_ownership_harness(monkeypatch: pytest.MonkeyPatch) -> OwnershipHarness:
    """Inject a deterministic backend under the real owned-client wrapper."""
    harness = OwnershipHarness(probe_module._async_wait_for_previous_cleanup)

    def initialize(
        client: BleakClientWithServiceCache, *_args: Any, **_kwargs: Any
    ) -> None:
        """Replace only backend allocation, before the probe captures its client."""
        cast(Any, client)._backend = harness.backends[len(harness.clients)]
        harness.clients.append(client)

    async def connect(client: BleakClientWithServiceCache, **_kwargs: Any) -> None:
        """Dispatch to the fake backend using the real probe connect wrapper."""
        await cast(Any, client)._backend.connect()

    async def disconnect(client: BleakClientWithServiceCache) -> None:
        """Wait for the fake backend's actual physical connection release."""
        await cast(Any, client)._backend.disconnect()

    async def read(
        device: _ProbeDevice, received: asyncio.Future[None], enable_status: Any
    ) -> ProbeOutcome:
        """Bind the establishment harness to the private probe device."""
        return await harness.read(device, received, enable_status)

    monkeypatch.setattr(BleakClientWithServiceCache, "__init__", initialize)
    monkeypatch.setattr(BleakClientWithServiceCache, "connect", connect)
    monkeypatch.setattr(BleakClientWithServiceCache, "disconnect", disconnect)
    monkeypatch.setattr(BleakClientWithServiceCache, "stop_notify", AsyncMock())
    monkeypatch.setattr(probe_module, "establish_connection", harness.establish)
    monkeypatch.setattr(probe_module, "_async_wait_for_previous_cleanup", harness.wait)
    monkeypatch.setattr(probe_module, "_pending_probe_cleanup", {})
    monkeypatch.setattr(probe_module, "PROBE_TIMEOUT", 0.05)
    monkeypatch.setattr(probe_module, "PROBE_CLEANUP_TIMEOUT", 0.05)
    monkeypatch.setattr(_ProbeDevice, "authenticated_read", read)
    return harness


async def test_partial_establishment_is_closed_before_config_flow_retry(
    hass: HomeAssistant, ownership_harness: OwnershipHarness
) -> None:
    """A link stalled in service discovery is owned even without a returned client."""
    flow, discovery = _flow(
        hass, [make_credentials(uuid="cloud-uuid", product_id="prod")]
    )
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
    ):
        progress = await flow.async_step_device({"address": discovery.address})
        assert progress["type"] == FlowResultType.SHOW_PROGRESS
        await ownership_harness.backends[0].opened.wait()
        await ownership_harness.backends[0].disconnect_cancelled.wait()
        await ownership_harness.retry_started.wait()
        assert len(ownership_harness.clients) == 1
        assert ownership_harness.devices[0]._client is None
        assert (
            ownership_harness.devices[0].cleanup_client is ownership_harness.clients[0]
        )
        assert ownership_harness.backends[0].is_connected
        ownership_harness.backends[0].release_disconnect.set()
        assert flow._probe_task is not None
        await flow._probe_task
        result = await flow.async_step_probe_finish()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_UUID] == "1234567890abcdef"
    assert len(ownership_harness.clients) == 2
    assert [backend.connect_calls for backend in ownership_harness.backends] == [1, 1]
    assert all(not backend.is_connected for backend in ownership_harness.backends)
    assert not probe_module._pending_probe_cleanup


@dataclass
class ProxyBackend(PartialDiscoveryBackend):
    """Model ESPHome clearing local state before remote disconnect is acknowledged."""

    physical_connected: bool = False
    _client: Any = None

    async def connect(self) -> None:
        """Open the proxy link without service-discovery delay."""
        self.connect_calls += 1
        self.is_connected = True
        self.physical_connected = True

    async def disconnect(self) -> None:
        """Await the remote response; clear the local flag even on cancellation."""
        try:
            api_client = getattr(self, "_client", None)
            if api_client is not None and api_client._connection is None:
                raise OSError("The owning API session has ended")
            await self.release_disconnect.wait()
            self.physical_connected = False
        finally:
            self.is_connected = False


async def test_acknowledged_proxy_disconnect_allows_next_probe(
    ownership_harness: OwnershipHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Successful remote acknowledgment preserves the match and releases ownership."""
    backend = ProxyBackend(partial=False)
    monkeypatch.setattr(ProxyBackend, "__module__", "bleak_esphome.backend.client")
    backend.release_disconnect.set()
    ownership_harness.backends[0] = backend
    ble, adv = _input()
    for _ in range(2):
        result = await async_probe_authenticated_status(
            ble, adv, make_credentials(), "1234567890abcdef", frozenset({1})
        )
        assert result == ProbeOutcome.MATCHED
    assert not backend.physical_connected
    assert len(ownership_harness.clients) == 2
    assert not probe_module._pending_probe_cleanup


async def test_cancelled_proxy_disconnect_keeps_barrier_until_remote_ack(
    ownership_harness: OwnershipHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """False proxy-local flags never allow two public probes to overlap remotely."""
    backend = ProxyBackend(partial=False)
    monkeypatch.setattr(ProxyBackend, "__module__", "bleak_esphome.backend.client")
    ownership_harness.backends[0] = backend
    ble, adv = _input()
    first = await async_probe_authenticated_status(
        ble, adv, make_credentials(), "1234567890abcdef", frozenset({1})
    )
    await asyncio.gather(
        *(pending[0] for pending in probe_module._pending_probe_cleanup.values()),
        return_exceptions=True,
    )
    assert first == ProbeOutcome.RETRYABLE
    assert not backend.is_connected
    assert backend.physical_connected
    blocked = await async_probe_authenticated_status(
        ble, adv, make_credentials(), "1234567890abcdef", frozenset({1})
    )
    await asyncio.gather(
        *(pending[0] for pending in probe_module._pending_probe_cleanup.values()),
        return_exceptions=True,
    )
    assert blocked == ProbeOutcome.RETRYABLE
    assert len(ownership_harness.clients) == 1
    assert backend.physical_connected
    backend.release_disconnect.set()
    resumed = await async_probe_authenticated_status(
        ble, adv, make_credentials(), "1234567890abcdef", frozenset({1})
    )
    assert resumed == ProbeOutcome.MATCHED
    assert not backend.physical_connected
    assert len(ownership_harness.clients) == 2
    assert not probe_module._pending_probe_cleanup


@pytest.mark.parametrize(
    ("session_state", "current_session", "release_expected"),
    [
        ("CLOSED", "detached", True),
        ("CLOSED", "replaced", True),
        ("CONNECTED", "detached", False),
        ("CLOSED", "original", False),
        (None, "replaced", False),
    ],
)
async def test_proxy_session_replacement_requires_confirmed_old_session_closure(
    ownership_harness: OwnershipHarness,
    monkeypatch: pytest.MonkeyPatch,
    session_state: str | None,
    current_session: str,
    release_expected: bool,
) -> None:
    """Only a terminated owning API session can release an unacknowledged proxy."""
    session = SimpleNamespace(connection_state=SimpleNamespace(name="CONNECTED"))
    api_client = SimpleNamespace(_connection=session)
    backend = ProxyBackend(partial=False)
    cast(Any, backend)._client = api_client
    monkeypatch.setattr(ProxyBackend, "__module__", "bleak_esphome.backend.client")
    ownership_harness.backends[0] = backend
    ble, adv = _input()
    first = await async_probe_authenticated_status(
        ble, adv, make_credentials(), "1234567890abcdef", frozenset({1})
    )
    await asyncio.gather(
        *(pending[0] for pending in probe_module._pending_probe_cleanup.values()),
        return_exceptions=True,
    )
    assert first == ProbeOutcome.RETRYABLE
    assert backend.physical_connected
    assert not backend.is_connected
    session.connection_state = (
        None if session_state is None else SimpleNamespace(name=session_state)
    )
    if current_session == "detached":
        api_client._connection = None
    elif current_session == "replaced":
        api_client._connection = SimpleNamespace(
            connection_state=SimpleNamespace(name="CONNECTED")
        )
    if release_expected:
        # The proxy releases this subscriber's BLE links when its session ends.
        backend.physical_connected = False
    resumed = await async_probe_authenticated_status(
        ble, adv, make_credentials(), "1234567890abcdef", frozenset({1})
    )
    await asyncio.gather(
        *(pending[0] for pending in probe_module._pending_probe_cleanup.values()),
        return_exceptions=True,
    )
    if release_expected:
        assert resumed == ProbeOutcome.MATCHED
        assert len(ownership_harness.clients) == 2
        assert not probe_module._pending_probe_cleanup
    else:
        assert resumed == ProbeOutcome.RETRYABLE
        assert len(ownership_harness.clients) == 1
        assert backend.physical_connected
        assert probe_module._pending_probe_cleanup


@pytest.mark.parametrize("internal_retry", ["connect", "new_client"])
async def test_connector_internal_retry_cannot_reconnect_an_uncleaned_client(
    ownership_harness: OwnershipHarness,
    internal_retry: str,
) -> None:
    """Any connector retry is deferred to the outer loop's fresh, cleaned session."""
    ownership_harness.backends[0].partial = False
    ownership_harness.internal_retry = internal_retry
    ble, adv = _input()
    result = await async_probe_authenticated_status(
        ble, adv, make_credentials(), "1234567890abcdef", frozenset({1})
    )
    assert result == ProbeOutcome.RETRYABLE
    assert ownership_harness.backends[0].connect_calls == 1
    assert ownership_harness.backends[1].connect_calls == 0
    assert not ownership_harness.backends[0].is_connected
    assert not probe_module._pending_probe_cleanup
