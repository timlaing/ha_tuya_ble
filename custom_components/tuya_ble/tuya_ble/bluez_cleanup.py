"""Recover teardown of an abandoned native client with a dead private bus."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from bleak.backends.bluezdbus.client import BleakClientBlueZDBus
    from dbus_fast.aio import MessageBus


class _ConnectionState(Protocol):
    """Type the state field missing from Bleak's native backend annotations."""

    _is_connected: bool


@dataclass(repr=False)
class _BlueZSource:
    """Retain the native backend until BlueZ acknowledges its link is gone."""

    backend: BleakClientBlueZDBus
    bus: MessageBus | None = field(init=False)
    path: str | None = field(init=False)
    confirmed: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        """Preserve session provenance before native teardown clears its references."""
        self.bus = self.backend._bus  # pylint: disable=protected-access
        self.path = self.backend._device_path  # pylint: disable=protected-access

    async def _request_disconnect(self, bus: MessageBus) -> None:
        """Record an affirmative BlueZ teardown reply, independently of bus closure."""
        # pylint: disable=import-outside-toplevel
        # Imports stay lazy: native Linux dependencies are not needed by proxies
        # or by the CoreBluetooth and Windows backends.
        from bleak.backends.bluezdbus import defs  # noqa: PLC0415
        from bleak.backends.bluezdbus.utils import assert_reply  # noqa: PLC0415
        from bleak.exc import BleakDBusError  # noqa: PLC0415
        from dbus_fast import Message  # noqa: PLC0415

        reply = await bus.call(
            Message(
                destination=defs.BLUEZ_SERVICE,
                path=self.path,
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
        self.confirmed = True

    def _retire_backend(self) -> None:
        """Retire only this abandoned client's local resources after confirmation."""
        backend = self.backend
        backend._cleanup_all()  # pylint: disable=protected-access
        backend.services = None
        cast(_ConnectionState, backend)._is_connected = False  # pylint: disable=protected-access
        if event := backend._disconnect_monitor_event:  # pylint: disable=protected-access
            event.set()
            backend._disconnect_monitor_event = None  # pylint: disable=protected-access
        assert self.bus is not None
        self.bus.disconnect()
        backend._bus = None  # pylint: disable=protected-access

    async def async_disconnect(self) -> bool:
        """Use the captured live session once; recover only an unconfirmed dead one."""
        if self.confirmed:
            self._retire_backend()
            return True
        if self.bus is None:
            return not self.backend.is_connected
        if self.bus.connected is False:
            return await self.recover()
        if self.bus.connected is not True or self.path is None:
            return False
        # This abandoned-client owner observes the actual reply itself, rather
        # than inferring physical teardown from version-dependent native flags.
        await self._request_disconnect(self.bus)
        self._retire_backend()
        return True

    async def recover(self) -> bool:
        """Use a new bus only when the original private bus is explicitly dead."""
        # pylint: disable=import-outside-toplevel
        from bleak.backends.bluezdbus.utils import (
            get_dbus_authenticator,  # noqa: PLC0415
        )
        from dbus_fast import BusType  # noqa: PLC0415
        from dbus_fast.aio import MessageBus  # noqa: PLC0415

        if self.confirmed:
            self._retire_backend()
            return True
        old_bus = self.bus
        path = self.path
        if old_bus is None or old_bus.connected is not False or path is None:
            return False

        bus = MessageBus(bus_type=BusType.SYSTEM, auth=get_dbus_authenticator())
        try:
            await bus.connect()
            await self._request_disconnect(bus)
        finally:
            # Also close constructor-owned handles if connect failed or was cancelled.
            bus.disconnect()

        self._retire_backend()
        return True
