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


async def test_cancelled_command_releases_its_response_slot() -> None:
    """Cancelling a command while it awaits its response leaves no waiter behind."""
    harness = ProtocolHarness()
    dev, client = harness.device, harness.client
    waiters: list[asyncio.Future[int]] = []

    async def send(_packets: list[bytes]) -> None:
        waiters.extend(dev._input_expected_responses.values())

    with patch.object(dev, "_int_send_packet_while_connected", side_effect=send):
        command = asyncio.create_task(
            dev._send_packet(TuyaBLECode.FUN_SENDER_DPS, b"\x01")
        )
        for _ in range(10):
            await asyncio.sleep(0)
        assert waiters
        command.cancel()
        with pytest.raises(asyncio.CancelledError):
            await command
    assert waiters[0].cancelled()
    assert not dev._input_expected_responses
    # The session itself stays usable: cancelling is not a device failure.
    assert cast(object, dev._client) is client
    assert dev._is_ready()


async def test_write_failure_before_send_releases_pending_waiter() -> None:
    """A write that fails before reaching the device leaves no pending waiter."""
    dev = ProtocolHarness().device
    waiters: list[asyncio.Future[int]] = []

    async def send(_packets: list[bytes]) -> None:
        waiters.extend(dev._input_expected_responses.values())
        raise BleakError("link lost")

    with (
        patch.object(dev, "_int_send_packet_while_connected", side_effect=send),
        pytest.raises(BleakError),
    ):
        await dev._send_packet_while_connected(TuyaBLECode.FUN_SENDER_DPS, b"", 0, True)
    assert waiters[0].cancelled()
    assert not dev._input_expected_responses


async def test_late_response_to_finished_waiter_is_ignored() -> None:
    """A response arriving after its waiter was cancelled must not raise."""
    dev = _device()
    future: asyncio.Future[int] = asyncio.get_running_loop().create_future()
    future.cancel()
    dev._input_expected_responses[7] = future
    dev._resolve_expected_response(7, 0)
    assert not dev._input_expected_responses


async def test_queued_fragments_never_reach_a_replacement_client() -> None:
    """Fragments of one frame are written only to the link the first one used."""
    dev = _device()
    first = FakeBleakClient()
    replacement = FakeBleakClient()
    dev._client = first  # type: ignore[assignment]
    original_write = first.write_gatt_char

    async def swap(char: str, data: bytes, response: bool) -> None:
        await original_write(char, data, response)
        dev._client = replacement  # type: ignore[assignment]

    with (
        patch.object(first, "write_gatt_char", side_effect=swap),
        pytest.raises(BleakError),
    ):
        await dev._int_send_packets_locked([b"first", b"second"])
    assert first.writes == [b"first"]
    assert not replacement.writes
    assert cast(object, dev._client) is replacement


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
