"""Recover teardown of an abandoned native client with a dead private bus."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from bleak.backends.bluezdbus.client import BleakClientBlueZDBus


class _ConnectionState(Protocol):
    """Type the state field missing from Bleak's native backend annotations."""

    _is_connected: bool


@dataclass(repr=False)
class _BlueZSource:
    """Retain the native backend until BlueZ acknowledges its link is gone."""

    backend: BleakClientBlueZDBus

    async def recover(self) -> bool:
        """Use a new bus only when the original private bus is explicitly dead."""
        # pylint: disable=import-outside-toplevel
        # Imports stay lazy: native Linux dependencies are not needed by proxies
        # or by the CoreBluetooth and Windows backends.
        from bleak.backends.bluezdbus import defs  # noqa: PLC0415
        from bleak.backends.bluezdbus.utils import (  # noqa: PLC0415
            assert_reply,
            get_dbus_authenticator,
        )
        from bleak.exc import BleakDBusError  # noqa: PLC0415
        from dbus_fast import BusType, Message  # noqa: PLC0415
        from dbus_fast.aio import MessageBus  # noqa: PLC0415

        backend = self.backend
        old_bus = backend._bus  # pylint: disable=protected-access
        path = backend._device_path  # pylint: disable=protected-access
        if old_bus is None or old_bus.connected is not False or path is None:
            return False

        bus = MessageBus(bus_type=BusType.SYSTEM, auth=get_dbus_authenticator())
        try:
            await bus.connect()
            reply = await bus.call(
                Message(
                    destination=defs.BLUEZ_SERVICE,
                    path=path,
                    interface=defs.DEVICE_INTERFACE,
                    member="Disconnect",
                )
            )
            try:
                assert_reply(reply)
            except BleakDBusError as error:
                # Both replies affirmatively confirm there is no remaining link.
                # A missing service, unavailable adapter or unknown error does not.
                if error.dbus_error not in (
                    "org.freedesktop.DBus.Error.UnknownObject",
                    "org.bluez.Error.NotConnected",
                ):
                    raise
        finally:
            # Also close constructor-owned handles if connect failed or was cancelled.
            bus.disconnect()

        # Bleak 3.0.2's watcher can clear services without replacing its dead bus,
        # or can miss the link-down signal during a manager bus reset altogether.
        # Only the affirmative response above permits retiring this abandoned client.
        backend._cleanup_all()  # pylint: disable=protected-access
        cast(_ConnectionState, backend)._is_connected = False  # pylint: disable=protected-access
        if event := backend._disconnect_monitor_event:  # pylint: disable=protected-access
            event.set()
            backend._disconnect_monitor_event = None  # pylint: disable=protected-access
        old_bus.disconnect()
        backend._bus = None  # pylint: disable=protected-access
        return True
