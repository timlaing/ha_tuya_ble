"""Real Bleak BlueZ teardown over synthetic D-Bus transport after session loss."""

# pylint: disable=protected-access

from __future__ import annotations

import asyncio
from typing import cast
from unittest.mock import AsyncMock, patch

from bleak.backends.bluezdbus import defs
from bleak.backends.bluezdbus.client import BleakClientBlueZDBus
from bleak.backends.bluezdbus.manager import BlueZManager
from bleak.backends.bluezdbus.version import BlueZFeatures
from bleak.backends.service import BleakGATTServiceCollection
from bleak_retry_connector import BleakClientWithServiceCache
from dbus_fast import Message, MessageType, Variant
from dbus_fast.aio import MessageBus
import pytest

from custom_components.tuya_ble.tuya_ble.bluez_cleanup import (
    _BlueZSource,
    _ConnectionState,
)
from custom_components.tuya_ble.tuya_ble.connection_cleanup import _ConnectionCleanup

ADDRESS = "AA:BB:CC:DD:EE:FF"
PATH = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"


class NativeBus:
    """Implement only D-Bus transport; Bleak's client and watcher logic stay real."""

    def __init__(self, error: str | None = None) -> None:
        self.connected: bool | None = True
        self.error = error
        self.closed = 0
        self.calls: list[Message] = []
        self.waiting = asyncio.Event()

    async def connect(self) -> None:
        """Allow a recovered bus, or model the host service still being down."""
        if self.error == "connect":
            raise OSError("Synthetic D-Bus service unavailable")

    async def call(self, message: Message) -> Message:
        """Return actual D-Bus reply objects without any BLE state shortcuts."""
        self.calls.append(message)
        if self.connected is False:
            raise OSError("Synthetic dead private D-Bus session")
        if self.error == "wait":
            self.waiting.set()
            await asyncio.Event().wait()
        if self.error is not None:
            return Message(
                message_type=MessageType.ERROR,
                reply_serial=1,
                error_name=self.error,
                signature="s",
                body=["Synthetic D-Bus error"],
            )
        return Message(message_type=MessageType.METHOD_RETURN, reply_serial=1)

    def disconnect(self) -> None:
        """Close handles, including when connect failed."""
        self.closed += 1
        self.connected = False

    async def wait_for_disconnect(self) -> None:
        """A dead bus retains its fatal exception even after BlueZ recovers."""
        raise OSError("Synthetic persistent old D-Bus failure")


async def make_native_client() -> tuple[
    BleakClientWithServiceCache, BleakClientBlueZDBus, NativeBus, BlueZManager
]:
    """Run actual native connect and register its real manager watcher."""
    client = BleakClientWithServiceCache(ADDRESS, backend=BleakClientBlueZDBus)
    backend = cast(BleakClientBlueZDBus, client._backend)
    backend._device_path = PATH
    bus = NativeBus()
    manager = BlueZManager()
    manager._properties[PATH] = {
        defs.DEVICE_INTERFACE: {"Connected": False, "Adapter": "/org/bluez/hci0"}
    }
    with (
        patch.object(BlueZFeatures, "checked_bluez_version", True),
        patch.object(BlueZFeatures, "supported_version", True),
        patch(
            "bleak.backends.bluezdbus.client.get_global_bluez_manager",
            new=AsyncMock(return_value=manager),
        ),
        patch("bleak.backends.bluezdbus.client.MessageBus", return_value=bus),
        patch.object(backend, "_get_services", new=AsyncMock()),
    ):
        await backend.connect(pair=False)
    backend.services = BleakGATTServiceCollection()
    assert backend.is_connected
    bus.connected = False
    return client, backend, bus, manager


@pytest.mark.parametrize("watcher_ran", [False, True])
async def test_real_native_dead_bus_requires_fresh_acknowledged_teardown(
    watcher_ran: bool,
) -> None:
    """Neither a missed watcher nor a delivered link-down signal repairs the bus."""
    client, backend, dead, manager = await make_native_client()
    monitor = backend._disconnect_monitor_event
    assert monitor is not None
    if watcher_ran:
        manager._parse_msg(
            Message(
                message_type=MessageType.SIGNAL,
                path=PATH,
                interface=defs.PROPERTIES_INTERFACE,
                member="PropertiesChanged",
                signature="sa{sv}as",
                body=[defs.DEVICE_INTERFACE, {"Connected": Variant("b", False)}, []],
            )
        )
        assert not backend.is_connected
        assert backend.services is None
    # Exercise the real backend's disconnect, not a fake disconnect coroutine.
    with pytest.raises(OSError):
        await client.disconnect()
    assert backend._bus is cast(MessageBus, dead)
    recovery = NativeBus()
    cleanup = _ConnectionCleanup(ADDRESS)
    with patch("dbus_fast.aio.MessageBus", return_value=recovery):
        await cleanup.async_discard(client, 0.1)
    assert recovery.calls[0].member == "Disconnect"
    assert recovery.calls[0].path == PATH
    assert recovery.calls[0].destination == defs.BLUEZ_SERVICE
    assert backend._bus is None
    assert not client.is_connected
    assert backend.services is None
    assert monitor.is_set()
    assert not manager._device_watchers.get(PATH)
    assert not cleanup.pending
    assert not cleanup.tasks
    assert not cleanup._native_sources
    assert dead.closed == (2 if watcher_ran else 1)
    assert recovery.closed == 1
    await asyncio.sleep(0)


@pytest.mark.parametrize(
    "error",
    [None, "org.bluez.Error.NotConnected", "org.freedesktop.DBus.Error.UnknownObject"],
)
async def test_native_recovery_accepts_only_confirmed_teardown(
    error: str | None,
) -> None:
    """No-link replies confirm closure even when the old manager missed the signal."""
    client, backend, dead, _manager = await make_native_client()
    assert backend._disconnect_monitor_event is not None
    backend._disconnect_monitor_event.set()
    backend._disconnect_monitor_event = None
    recovery = NativeBus(error)
    with patch("dbus_fast.aio.MessageBus", return_value=recovery):
        assert await _BlueZSource(backend).recover()
    assert not client.is_connected
    assert dead.closed == 1
    assert recovery.closed == 1
    await asyncio.sleep(0)


@pytest.mark.parametrize("error", ["connect", "org.bluez.Error.NotReady", "wait"])
async def test_native_recovery_failure_retains_ownership_then_retries(
    error: str,
) -> None:
    """An unavailable service or timeout never authorizes a replacement link."""
    client, backend, dead, _manager = await make_native_client()
    cleanup = _ConnectionCleanup(ADDRESS)
    recovery = NativeBus(error)
    with patch("dbus_fast.aio.MessageBus", return_value=recovery):
        await cleanup.async_discard(client, 0.01)
    assert client in cleanup.pending
    assert backend._bus is cast(MessageBus, dead)
    assert backend.is_connected
    assert dead.closed == 0
    assert recovery.closed == 1
    healthy = NativeBus()
    # The wrapper dropping its backend must not bypass native ownership either.
    client._backend = None  # type: ignore[assignment]
    with patch("dbus_fast.aio.MessageBus", return_value=healthy):
        assert await cleanup.async_cleanup(None, 0.1)
    assert not cleanup._native_sources
    assert not backend.is_connected
    await asyncio.sleep(0)


@pytest.mark.parametrize("state", ["no_bus", "live", "unknown", "no_path"])
async def test_native_recovery_does_not_replace_live_or_unknown_bus(state: str) -> None:
    """Only an explicitly dead private bus activates the alternate teardown path."""
    _client, backend, bus, _manager = await make_native_client()
    assert backend._disconnect_monitor_event is not None
    backend._disconnect_monitor_event.set()
    if state == "no_bus":
        backend._bus = None
    elif state == "no_path":
        backend._device_path = None
    else:
        bus.connected = True if state == "live" else None
    with patch("dbus_fast.aio.MessageBus") as create:
        assert not await _BlueZSource(backend).recover()
    create.assert_not_called()
    await asyncio.sleep(0)


async def test_live_native_bus_uses_existing_disconnect_before_dead_bus_recovery() -> (
    None
):
    """A live native bus still runs Bleak's own teardown, not an alternate request."""
    client, backend, bus, _manager = await make_native_client()
    bus.connected = True
    cleanup = _ConnectionCleanup(ADDRESS)
    # Model a delivered link-down signal, leaving only the old bus's fatal result.
    cast(_ConnectionState, backend)._is_connected = False
    backend._cleanup_all()
    assert backend._disconnect_monitor_event is not None
    backend._disconnect_monitor_event.set()
    with patch("dbus_fast.aio.MessageBus") as create:
        await cleanup.async_discard(client, 0.1)
    create.assert_not_called()
    assert client in cleanup.pending
    assert bus.closed == 1
    healthy = NativeBus()
    with patch("dbus_fast.aio.MessageBus", return_value=healthy):
        assert await cleanup.async_cleanup(None, 0.1)
    assert not backend.is_connected
    await asyncio.sleep(0)


async def test_cancelling_native_recovery_keeps_ownership_and_closes_fresh_bus() -> (
    None
):
    """Cancellation cannot release a link whose fresh disconnect was unacknowledged."""
    _client, backend, dead, _manager = await make_native_client()
    recovery = NativeBus("wait")
    with patch("dbus_fast.aio.MessageBus", return_value=recovery):
        closing = asyncio.create_task(_BlueZSource(backend).recover())
        await recovery.waiting.wait()
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing
    assert backend._bus is cast(MessageBus, dead)
    assert backend.is_connected
    assert dead.closed == 0
    assert recovery.closed == 1
    assert backend._disconnect_monitor_event is not None
    backend._disconnect_monitor_event.set()
    await asyncio.sleep(0)
