"""Session recovery after a failed command: no replay into a new session (#162)."""

# pylint: disable=protected-access

from __future__ import annotations

import asyncio
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from bleak.exc import BleakError
import pytest

from custom_components.tuya_ble.tuya_ble import TuyaBLEDataPointType, TuyaBLEDevice
from custom_components.tuya_ble.tuya_ble.const import (
    CHARACTERISTIC_NOTIFY,
    TuyaBLECode,
)
from custom_components.tuya_ble.tuya_ble.exceptions import (
    TuyaBLEDeviceError,
    TuyaBLEResponseTimeoutError,
)
from tests.conftest import (
    FakeBleakClient,
    FakeBLEManager,
    make_credentials,
    make_device,
)
from tests.protocol_harness import (
    ProtocolHarness,
    decode_frame,
    device_info_data,
    encrypt_payload,
    fragment,
    frame_packet0,
)

ESTABLISH = "custom_components.tuya_ble.tuya_ble.base.establish_connection"
RESPONSE_TIMEOUT = (
    "custom_components.tuya_ble.tuya_ble.protocol_send.RESPONSE_WAIT_TIMEOUT"
)

_FAILURES: dict[str, type[Exception]] = {
    "write": BleakError,
    "timeout": TuyaBLEResponseTimeoutError,
    "disconnect": TuyaBLEDeviceError,
}


def _device() -> TuyaBLEDevice:
    """Build a protocol 3 device that does not use the Fingerbot Touch handling."""
    credentials = make_credentials()
    dev = make_device(manager=FakeBLEManager(credentials))
    dev.initialize_with_credentials(credentials)
    return dev


@pytest.mark.parametrize("failure", list(_FAILURES))
async def test_failed_command_is_not_replayed_after_reconnect(failure: str) -> None:
    """A failed command releases its session and is never sent to the next one."""
    dev = _device()
    clients = [FakeBleakClient(), FakeBleakClient()]
    sent: list[tuple[int, TuyaBLECode]] = []
    login_key = dev._login_key
    assert login_key is not None
    original_send = dev._int_send_packet_while_connected

    async def send(packets: list[bytes]) -> None:
        keys = {4: login_key, 5: dev._session_key or bytes(16)}
        seq_num, _, code, _, flag = decode_frame(keys, packets)
        command = TuyaBLECode(code)
        client = cast(Any, dev._client)
        index = clients.index(client)
        sent.append((index, command))
        failing = command is TuyaBLECode.FUN_SENDER_DPS and index == 0
        if failing and failure == "write":
            client.write_gatt_char = AsyncMock(side_effect=OSError("write failed"))
        await original_send(packets)
        if failing and failure == "timeout":
            return
        if failing and failure == "disconnect":
            await client.disconnect()
            establish.call_args.args[3](client)
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

    with (
        patch(ESTABLISH, side_effect=clients) as establish,
        patch.object(dev, "_int_send_packet_while_connected", side_effect=send),
        patch(RESPONSE_TIMEOUT, 0.05),
    ):
        try:
            await dev.update()
            datapoint = dev.get_or_create_datapoint(
                1, TuyaBLEDataPointType.DT_BOOL, False
            )
            with pytest.raises(_FAILURES[failure]):
                await datapoint.set_value(True)
            assert datapoint.value is False
            assert not clients[0].is_connected
            assert not dev._input_expected_responses

            reconnect = dev._reconnect_task
            assert reconnect is not None
            await asyncio.wait_for(reconnect, 1)
            assert cast(object, dev._client) is clients[1]
            assert dev._is_ready()

            # A late callback from the failed link must not drop the new session.
            establish.call_args.args[3](clients[0])
            assert dev._is_ready()

            await datapoint.set_value(True)
            assert datapoint.value is True
        finally:
            await dev.stop()

    handshake = [TuyaBLECode.FUN_SENDER_DEVICE_INFO, TuyaBLECode.FUN_SENDER_PAIR]
    assert sent == [
        *((0, code) for code in handshake),
        (0, TuyaBLECode.FUN_SENDER_DEVICE_STATUS),
        (0, TuyaBLECode.FUN_SENDER_DPS),
        *((1, code) for code in handshake),
        (1, TuyaBLECode.FUN_SENDER_DPS),
    ]
    assert establish.await_count == 2


def _harness() -> ProtocolHarness:
    """Return a paired protocol 3 harness whose writes go to a fake client."""
    return ProtocolHarness(protocol_version=3)


async def test_cancelled_write_releases_its_response_slot() -> None:
    """Cancelling a datapoint write that awaits its response leaves no waiter."""
    h = _harness()
    dp = h.device.get_or_create_datapoint(1, TuyaBLEDataPointType.DT_BOOL, False)
    write = asyncio.create_task(dp.set_value(True))
    for _ in range(20):
        await asyncio.sleep(0)
        if h.writes():
            break
    assert h.writes()
    waiters = list(h.device._input_expected_responses.values())
    assert len(waiters) == 1
    write.cancel()
    with pytest.raises(asyncio.CancelledError):
        await write
    assert waiters[0].cancelled()
    assert not h.device._input_expected_responses
    assert dp.value is False
    # Cancelling is not a device failure: the session stays usable.
    assert cast(object, h.device._client) is h.client
    assert h.device._is_ready()


@pytest.mark.parametrize("link", ["live", "dropped", "replaced"])
async def test_failed_write_releases_only_the_failed_client(link: str) -> None:
    """A failed write releases its own session, never a replacement link."""
    h = _harness()
    replacement = FakeBleakClient()
    dp = h.device.get_or_create_datapoint(1, TuyaBLEDataPointType.DT_BOOL, False)

    async def fail(*_args: object) -> None:
        if link == "dropped":
            await h.client.disconnect()
        elif link == "replaced":
            h.device._client = replacement  # type: ignore[assignment]
        raise OSError("write failed")

    with (
        patch.object(h.client, "write_gatt_char", side_effect=fail),
        patch.object(h.device, "_schedule_reconnect") as reconnect,
        pytest.raises(BleakError),
    ):
        await dp.set_value(True)

    assert dp.value is False
    assert not h.device._input_expected_responses
    assert not h.client.is_connected or link == "replaced"
    if link == "replaced":
        assert cast(object, h.device._client) is replacement
        assert replacement.is_connected
        assert not replacement.writes
        reconnect.assert_not_called()
    else:
        assert h.device._client is None
        reconnect.assert_called_once_with()


async def test_late_response_to_finished_waiter_is_ignored() -> None:
    """A response arriving after its waiter was cancelled must not raise."""
    h = _harness()
    await h.register_notify()
    key = h.device._session_key
    assert key is not None
    future: asyncio.Future[int] = asyncio.get_running_loop().create_future()
    future.cancel()
    # Setup: a waiter cancelled just before its response arrived.
    h.device._input_expected_responses[7] = future
    h.notify(
        frame_packet0(
            encrypt_payload(key, 5, 1, 7, TuyaBLECode.FUN_SENDER_DPS, b"\x00"), 3
        )
    )
    await asyncio.sleep(0)
    assert not h.device._input_expected_responses
    assert h.device._input_buffer is None


async def test_fragments_never_reach_a_replacement_client() -> None:
    """A fragmented write stops when its link is replaced mid-frame."""
    h = _harness()
    replacement = FakeBleakClient()
    dp = h.device.get_or_create_datapoint(1, TuyaBLEDataPointType.DT_STRING, "x")
    original_write = h.client.write_gatt_char

    async def swap(char: str, data: bytes, response: bool) -> None:
        await original_write(char, data, response)
        h.device._client = replacement  # type: ignore[assignment]

    with (
        patch.object(h.client, "write_gatt_char", side_effect=swap),
        patch.object(h.device, "_schedule_reconnect"),
        pytest.raises(BleakError),
    ):
        await dp.set_value("y" * 100)
    assert len(h.client.writes) == 1
    assert not replacement.writes
    assert cast(object, h.device._client) is replacement
    assert dp.value == "x"


async def test_stop_notifies_listeners_once_and_ignores_the_late_callback() -> None:
    """Stopping fires the disconnect listeners even when the backend stays silent."""
    dev = _device()
    client = FakeBleakClient()
    dev._client = client  # type: ignore[assignment]
    dev._is_paired = True
    disconnected: list[bool] = []
    dev.register_disconnected_callback(lambda: disconnected.append(True))
    await dev.stop()
    dev._disconnected(client)  # type: ignore[arg-type]
    assert disconnected == [True]
    assert dev._client is None
    assert not client.is_connected
    assert client.stopped == [CHARACTERISTIC_NOTIFY]
