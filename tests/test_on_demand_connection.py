"""On-demand BLE connections: idle disconnect, reconnect on command (#167)."""

# pylint: disable=protected-access

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager
import logging
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

from bleak.exc import BleakError
from homeassistant.core import HomeAssistant
import pytest

from custom_components.tuya_ble.coordinator import TuyaBLECoordinator
from custom_components.tuya_ble.tuya_ble import (
    AbstractTuyaBLEDeviceManager,
    TuyaBLEDataPointType,
    TuyaBLEDevice,
)
from custom_components.tuya_ble.tuya_ble.const import (
    CHARACTERISTIC_NOTIFY,
    TuyaBLECode,
)
from tests.conftest import (
    FakeAdvertisementData,
    FakeBLEAddress,
    FakeBleakClient,
    FakeBLEManager,
    make_credentials,
)
from tests.protocol_harness import (
    decode_frame,
    device_info_data,
    encrypt_payload,
    fragment,
)

ESTABLISH = "custom_components.tuya_ble.tuya_ble.base.establish_connection"
PRESENT = "custom_components.tuya_ble.coordinator.bluetooth.async_address_present"
TRACK_UNAVAILABLE = (
    "custom_components.tuya_ble.coordinator.bluetooth.async_track_unavailable"
)
ADDRESS = "AA:BB:CC:DD:EE:FF"
IDLE = 0.05


def _device(idle_disconnect_delay: float | None = IDLE) -> TuyaBLEDevice:
    """Build a protocol 3 device with the given connection policy."""
    credentials = make_credentials()
    dev = TuyaBLEDevice(
        cast(AbstractTuyaBLEDeviceManager, FakeBLEManager(credentials)),
        cast(Any, FakeBLEAddress(ADDRESS, "TestDevice")),
        cast(
            Any,
            FakeAdvertisementData(rssi=-50, service_data={}, manufacturer_data={}),
        ),
        idle_disconnect_delay=idle_disconnect_delay,
    )
    dev.initialize_with_credentials(credentials)
    return dev


class _Link:
    """Answer every packet the device writes, on whichever client it uses."""

    def __init__(self, dev: TuyaBLEDevice, clients: list[FakeBleakClient]) -> None:
        self.dev = dev
        self.clients = clients
        self.sent: list[tuple[int, TuyaBLECode]] = []
        self.silent: set[TuyaBLECode] = set()
        self._original = dev._int_send_packet_while_connected

    async def send(self, packets: list[bytes]) -> None:
        """Record the packet, write it, then deliver the device's reply."""
        login_key = self.dev._login_key
        assert login_key is not None
        keys = {4: login_key, 5: self.dev._session_key or bytes(16)}
        seq_num, _, code, _, flag = decode_frame(keys, packets)
        command = TuyaBLECode(code)
        client = cast(Any, self.dev._client)
        self.sent.append((self.clients.index(client), command))
        await self._original(packets)
        if command in self.silent:
            return
        payload = (
            device_info_data()
            if command is TuyaBLECode.FUN_SENDER_DEVICE_INFO
            else b"\x00"
        )
        response = encrypt_payload(keys[flag], flag, 1, seq_num, command, payload)
        assert client.notify_handler is not None
        for part in fragment(response, 3):
            client.notify_handler(CHARACTERISTIC_NOTIFY, bytearray(part))


@contextmanager
def _linked(
    dev: TuyaBLEDevice, count: int = 2
) -> Iterator[tuple[_Link, MagicMock | AsyncMock]]:
    """Patch the connector to hand out fresh fake clients that answer packets."""
    clients = [FakeBleakClient() for _ in range(count)]
    link = _Link(dev, clients)
    with (
        patch(ESTABLISH, side_effect=clients) as establish,
        patch.object(dev, "_int_send_packet_while_connected", side_effect=link.send),
    ):
        yield link, establish


async def _idle_closed(dev: TuyaBLEDevice) -> None:
    """Wait for the pending idle timer to finish."""
    task = dev._idle_task
    assert task is not None
    await asyncio.wait_for(task, 1)


def test_default_policy_keeps_the_connection() -> None:
    """Without a delay the device keeps its link open, as before."""
    dev = _device(None)
    assert dev.keep_connection is True
    assert dev.idle_disconnect_delay is None


async def test_command_connects_and_idle_disconnect_skips_reconnect(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A command opens the link; the idle delay closes it without a reconnect."""
    dev = _device()
    assert dev.keep_connection is False
    assert dev.idle_disconnect_delay == IDLE
    disconnected: list[bool] = []
    connected: list[bool] = []
    dev.register_disconnected_callback(lambda: disconnected.append(True))
    dev.register_connected_callback(lambda: connected.append(True))
    caplog.set_level(logging.DEBUG)
    with _linked(dev) as (link, establish):
        try:
            await dev.update()
            datapoint = dev.get_or_create_datapoint(
                1, TuyaBLEDataPointType.DT_BOOL, False
            )
            await datapoint.set_value(True)
            assert dev._is_ready()
            first = link.clients[0]

            await _idle_closed(dev)
            assert dev._client is None
            assert not first.is_connected
            assert disconnected == [True]
            assert dev._reconnect_task is None
            assert dev._current_seq_num == 1
            assert f"{ADDRESS}: Idle for {IDLE}s, disconnecting" in caplog.text
            assert f"{ADDRESS}: Disconnected; reconnecting on demand" in caplog.text
            assert "unexpectedly disconnected" not in caplog.text
            # The late backend callback of the closed link is ignored.
            establish.call_args.args[3](first)
            assert disconnected == [True]

            await datapoint.set_value(False)
            assert cast(object, dev._client) is link.clients[1]
            assert connected == [True, True]
        finally:
            await dev.stop()

    handshake = [TuyaBLECode.FUN_SENDER_DEVICE_INFO, TuyaBLECode.FUN_SENDER_PAIR]
    assert link.sent == [
        *((0, code) for code in handshake),
        (0, TuyaBLECode.FUN_SENDER_DEVICE_STATUS),
        (0, TuyaBLECode.FUN_SENDER_DPS),
        *((1, code) for code in handshake),
        (1, TuyaBLECode.FUN_SENDER_DPS),
    ]
    assert establish.call_count == 2


async def test_activity_restarts_the_idle_timer() -> None:
    """A burst of commands shares one link; the timer runs from the last one."""
    dev = _device(0.4)
    with _linked(dev) as (link, establish):
        try:
            await dev.update()
            datapoint = dev.get_or_create_datapoint(
                1, TuyaBLEDataPointType.DT_BOOL, False
            )
            await datapoint.set_value(True)
            first_timer = dev._idle_task
            await asyncio.sleep(0.2)
            await datapoint.set_value(False)
            await asyncio.sleep(0.25)
            # 0.45 s after the first command, still within the second's delay.
            assert first_timer is not None
            assert first_timer.cancelled()
            assert link.clients[0].is_connected
            await _idle_closed(dev)
            assert not link.clients[0].is_connected
            assert establish.call_count == 1
        finally:
            await dev.stop()


async def test_idle_timer_waits_for_an_awaited_response() -> None:
    """The link is not closed while a response is still awaited."""
    dev = _device()
    with _linked(dev) as (link, _establish):
        try:
            await dev.update()
            link.silent.add(TuyaBLECode.FUN_SENDER_DPS)
            datapoint = dev.get_or_create_datapoint(
                1, TuyaBLEDataPointType.DT_BOOL, False
            )
            write = asyncio.create_task(datapoint.set_value(True))
            for _ in range(20):
                await asyncio.sleep(0)
                if dev._input_expected_responses:
                    break
            assert dev._input_expected_responses
            # Setup: the timer restarted by the command fires while it waits.
            dev._note_activity()
            await asyncio.sleep(IDLE * 3)
            assert link.clients[0].is_connected
            write.cancel()
            with pytest.raises(asyncio.CancelledError):
                await write
            await _idle_closed(dev)
            assert not link.clients[0].is_connected
        finally:
            await dev.stop()


async def test_unexpected_drop_on_demand_does_not_reconnect(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A dropped on-demand link waits for the next command to reconnect."""
    dev = _device(60)
    with _linked(dev) as (link, establish):
        try:
            await dev.update()
            first = link.clients[0]
            caplog.set_level(logging.DEBUG)
            await first.disconnect()
            establish.call_args.args[3](first)
            assert dev._client is None
            assert dev._reconnect_task is None
            assert "unexpectedly disconnected" not in caplog.text
            await dev.update()
            assert cast(object, dev._client) is link.clients[1]
        finally:
            await dev.stop()


async def test_failed_command_on_demand_is_not_replayed() -> None:
    """Session recovery still releases the link, and nothing reconnects early."""
    dev = _device(60)
    with _linked(dev) as (link, establish):
        try:
            await dev.update()
            datapoint = dev.get_or_create_datapoint(
                1, TuyaBLEDataPointType.DT_BOOL, False
            )
            first = link.clients[0]
            with (
                patch.object(
                    first, "write_gatt_char", side_effect=OSError("write failed")
                ),
                pytest.raises(BleakError),
            ):
                await datapoint.set_value(True)
            assert datapoint.value is False
            assert not first.is_connected
            assert dev._reconnect_task is None
            await datapoint.set_value(True)
            assert cast(object, dev._client) is link.clients[1]
        finally:
            await dev.stop()

    assert link.sent[-1] == (1, TuyaBLECode.FUN_SENDER_DPS)
    assert sum(code is TuyaBLECode.FUN_SENDER_DPS for _, code in link.sent) == 2
    assert establish.call_count == 2


async def test_command_waits_for_the_idle_close_to_finish() -> None:
    """A command arriving mid-close connects only once the old link is gone."""
    dev = _device()
    release = asyncio.Event()
    with _linked(dev) as (link, establish):
        try:
            await dev.update()
            first = link.clients[0]
            original_disconnect = first.disconnect

            async def slow_disconnect() -> None:
                await release.wait()
                await original_disconnect()

            with patch.object(first, "disconnect", side_effect=slow_disconnect):
                closing = dev._idle_task
                for _ in range(50):
                    await asyncio.sleep(IDLE / 5)
                    if dev._client is None:
                        break
                assert dev._client is None
                command = asyncio.create_task(dev.update())
                await asyncio.sleep(0.01)
                assert establish.call_count == 1
                release.set()
                await asyncio.wait_for(command, 1)
            assert closing is not None
            assert closing.cancelled()
            assert not first.is_connected
            assert cast(object, dev._client) is link.clients[1]
            assert establish.call_count == 2
        finally:
            await dev.stop()


async def test_idle_close_keeps_the_sequence_of_a_new_connection() -> None:
    """A connect that starts during the close keeps its sequence numbers."""
    dev = _device()
    release = asyncio.Event()
    with _linked(dev) as (link, _establish):
        try:
            await dev.update()
            first = link.clients[0]
            original_disconnect = first.disconnect

            async def slow_disconnect() -> None:
                await release.wait()
                await original_disconnect()

            with patch.object(first, "disconnect", side_effect=slow_disconnect):
                closing = dev._idle_task
                for _ in range(50):
                    await asyncio.sleep(IDLE / 5)
                    if dev._client is None:
                        break
                # Setup: another connection attempt holds the connect lock.
                await dev._connect_lock.acquire()
                sequence = dev._current_seq_num
                release.set()
                assert closing is not None
                await asyncio.wait_for(closing, 1)
                assert dev._current_seq_num == sequence
                dev._connect_lock.release()
        finally:
            await dev.stop()


async def test_idle_timer_ignores_a_link_that_is_not_ready() -> None:
    """The timer leaves a link alone that is not paired."""
    dev = _device()
    client = FakeBleakClient()
    dev._client = cast(Any, client)
    dev._is_paired = False
    dev._note_activity()
    await _idle_closed(dev)
    assert dev._client is cast(Any, client)
    assert client.is_connected
    await dev.stop()


async def test_stop_cancels_the_idle_timer_and_ignores_later_activity() -> None:
    """Stopping cancels the timer, and no new timer starts afterwards."""
    dev = _device(60)
    dev._note_activity()
    timer = dev._idle_task
    await dev.stop()
    assert timer is not None
    assert timer.cancelled()
    dev._note_activity()
    assert dev._idle_task is timer
    await dev.update()
    assert dev._idle_task is timer


async def test_kept_connection_starts_no_idle_timer() -> None:
    """The default policy never closes an idle link."""
    dev = _device(None)
    with _linked(dev, 1) as (_link, _establish):
        try:
            await dev.update()
            assert dev._idle_task is None
            assert dev._is_ready()
        finally:
            await dev.stop()


async def test_coordinator_keeps_an_idle_device_available(hass: HomeAssistant) -> None:
    """After the idle disconnect the device stays available, then not when gone."""
    dev = _device()
    coordinator = TuyaBLECoordinator(hass, dev)
    handlers: list[Callable[[object], None]] = []

    def _track(
        _hass: HomeAssistant,
        handler: Callable[[object], None],
        *_args: object,
        **_kwargs: object,
    ) -> Callable[[], None]:
        handlers.append(handler)
        return lambda: None

    with (
        patch(PRESENT, return_value=True),
        patch(TRACK_UNAVAILABLE, side_effect=_track),
    ):
        stop_tracking = coordinator.async_start_on_demand(0)
    with _linked(dev) as (link, _establish):
        try:
            assert coordinator.connected is False
            await dev.update()
            assert coordinator.connected is True
            await _idle_closed(dev)
            assert not link.clients[0].is_connected
            assert coordinator.connected is True
            assert dev._reconnect_task is None
            handlers[0](object())
            assert coordinator.connected is False
        finally:
            stop_tracking()
            await coordinator.async_shutdown()
            await dev.stop()


async def test_reload_during_an_idle_close_waits_for_the_old_link() -> None:
    """A replacement device for the address connects only after the idle close."""
    dev = _device()
    replacement = _device()
    release = asyncio.Event()
    with _linked(dev) as (link, _establish):
        await dev.update()
        first = link.clients[0]
        original_disconnect = first.disconnect

        async def slow_disconnect() -> None:
            await release.wait()
            await original_disconnect()

        with patch.object(first, "disconnect", side_effect=slow_disconnect):
            for _ in range(50):
                await asyncio.sleep(IDLE / 5)
                if dev._client is None:
                    break
            assert dev._client is None
            # A reload stops the old device while its idle close is pending.
            stopping = asyncio.create_task(dev.stop())
            with _linked(replacement, 1) as (new_link, new_establish):
                connecting = asyncio.create_task(replacement.update())
                await asyncio.sleep(0.05)
                new_establish.assert_not_called()
                release.set()
                await asyncio.wait_for(stopping, 1)
                await asyncio.wait_for(connecting, 1)
                assert not first.is_connected
                assert cast(object, replacement._client) is new_link.clients[0]
                await replacement.stop()
