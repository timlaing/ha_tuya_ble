"""How a protocol-4 connection learns its protocol version from the advertisement.

Protocol-4 firmware sends the FD50 identity record in the advertisement and the
encrypted identity in the scan response (``tuya_ble_main.c`` in the SDK mirror
pinned in ``docs/BLE_ADVERTISEMENT_DECODING.md``). These tests replay what an
SRB-PM01 did after Home Assistant restarts, over the firmware double from
``test_protocol_v4``. All vectors are synthetic.
"""

# pylint: disable=protected-access
from __future__ import annotations

import asyncio
import logging
from typing import cast
from unittest.mock import AsyncMock, patch

from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from bleak.exc import BleakError
import pytest

from custom_components.tuya_ble.tuya_ble import TuyaBLEDevice
from custom_components.tuya_ble.tuya_ble.const import (
    CHARACTERISTIC_NOTIFY_V4,
    MANUFACTURER_DATA_ID,
    SERVICE_UUID,
    SERVICE_UUID_V4,
    TuyaBLECode,
)
from custom_components.tuya_ble.tuya_ble.exceptions import TuyaBLEDeviceError
from custom_components.tuya_ble.tuya_ble.manager import TuyaBLEDeviceCredentials
from tests.conftest import (
    FakeAdvertisementData,
    FakeBleakClient,
    make_credentials,
    make_device,
)
from tests.protocol_harness import v4_advertisement
from tests.test_protocol_v4 import (
    FakeV4Firmware,
    assert_v4_session,
    drain_responses,
    make_v4_device,
    report,
    run_session,
    soil_sensor_datapoints,
)


async def test_fd50_identity_arriving_mid_connect_is_used() -> None:
    """An advertisement stored after the connect loop started still counts.

    Setup builds the device before any advertisement may have been seen, so
    ``_ensure_connected`` finds nothing to decode and the loop starts with the
    protocol-2 default. The identity that arrives while connecting must switch
    the handshake to protocol 4 before the device-info request goes out.
    """
    credentials = make_credentials()
    data = report(soil_sensor_datapoints())
    firmware = FakeV4Firmware(credentials, data)
    device = make_device()
    device._advertisement_data = None
    device.initialize_with_credentials(credentials)
    assert device._protocol_version == 2

    def advertise() -> None:
        device.set_ble_device_and_advertisement_data(
            device._ble_device, v4_advertisement(uuid=credentials.uuid.encode())
        )

    await run_session(device, firmware, while_connecting=advertise)

    assert_v4_session(firmware, data)
    dp = device.datapoints[5]
    assert dp is not None
    assert dp.value == 215


# ---------------------------------------------------------------------------
# Choosing protocol 4 without the scan response, and waiting when unusable
# ---------------------------------------------------------------------------

ESTABLISH = "custom_components.tuya_ble.tuya_ble.base.establish_connection"


def without_scan_response(credentials: TuyaBLEDeviceCredentials) -> AdvertisementData:
    """Return the FD50 advertisement alone, before its scan response arrived.

    Protocol-4 firmware sends the FD50 service data in the advertisement and
    the manufacturer data with the encrypted identity in the scan response
    (``tuya_ble_main.c``, ``adv_data_const`` / ``scan_rsp_data_const``). A
    passive scanner only ever stores this part.
    """
    complete = v4_advertisement(uuid=credentials.uuid.encode())
    return cast(
        AdvertisementData,
        FakeAdvertisementData(service_data=dict(complete.service_data)),
    )


def beacon() -> AdvertisementData:
    """Return an FD50 beacon frame: identity-record bit 0 is clear."""
    return cast(
        AdvertisementData,
        FakeAdvertisementData(
            service_data={SERVICE_UUID_V4: b"\x48\x00\x00\x08abcdefgh"}
        ),
    )


def make_unadvertised_device(credentials: TuyaBLEDeviceCredentials) -> TuyaBLEDevice:
    """Build a device as setup does after a restart: no advertisement yet."""
    device = make_device()
    device._advertisement_data = None
    device.initialize_with_credentials(credentials)
    return device


async def test_restart_connects_from_the_advertisement_alone() -> None:
    """Replay what an SRB-PM01 did after a Home Assistant restart.

    The connect loop starts before any advertisement has been stored, and the
    scanner then delivers only the FD50 advertisement: Home Assistant's
    passive scanners never received the scan response with the encrypted
    identity. The stored credentials already identify the device, so the
    advertisement's identity record must be enough for the protocol-4
    handshake on that same connection.
    """
    credentials = make_credentials()
    data = report(soil_sensor_datapoints())
    firmware = FakeV4Firmware(credentials, data)
    device = make_unadvertised_device(credentials)

    def advertise() -> None:
        device.set_ble_device_and_advertisement_data(
            device._ble_device, without_scan_response(credentials)
        )

    await run_session(device, firmware, while_connecting=advertise)

    assert_v4_session(firmware, data)
    assert firmware.notify_char == CHARACTERISTIC_NOTIFY_V4
    assert device.uuid == credentials.uuid
    assert not device._awaiting_fd50_identity


async def test_restart_reads_the_advertisement_home_assistant_did_not_pass_on() -> None:
    """Replay a later SRB-PM01 restart: the callback never delivers anything.

    Home Assistant stored the proxy's advertisement only while connecting, and
    did not pass it to the callback because another scanner was the device's
    preferred source. The provider must supply it after the connection is up.
    """
    credentials = make_credentials()
    data = report(soil_sensor_datapoints())
    firmware = FakeV4Firmware(credentials, data)
    device = make_unadvertised_device(credentials)
    stored: list[AdvertisementData] = []
    lookups: list[bool] = []

    def provider() -> tuple[BLEDevice, AdvertisementData] | None:
        lookups.append(bool(stored))
        return (device._ble_device, stored[-1]) if stored else None

    device.set_advertisement_provider(provider)

    await run_session(
        device,
        firmware,
        while_connecting=lambda: stored.append(without_scan_response(credentials)),
    )

    assert_v4_session(firmware, data)
    assert lookups[:2] == [False, True]
    assert device.rssi == stored[-1].rssi


async def test_stored_beacon_from_the_provider_prevents_connecting() -> None:
    """The provider is read before connecting, too."""
    device = make_unadvertised_device(make_credentials())
    device.set_advertisement_provider(lambda: (device._ble_device, beacon()))

    with (
        patch(ESTABLISH, new_callable=AsyncMock) as connect,
        pytest.raises(TuyaBLEDeviceError),
    ):
        await device.update()

    connect.assert_not_awaited()
    assert device._awaiting_fd50_identity


async def test_provider_identity_clears_wait_only_after_connection() -> None:
    """A provider-only identity clears the wait once the handshake succeeds."""
    credentials = make_credentials()
    data = report(soil_sensor_datapoints())
    firmware = FakeV4Firmware(credentials, data)
    device = make_unadvertised_device(credentials)
    stored = [beacon()]
    device.set_advertisement_provider(lambda: (device._ble_device, stored[-1]))

    with pytest.raises(TuyaBLEDeviceError):
        await device.update()
    assert device._awaiting_fd50_identity

    stored.append(without_scan_response(credentials))
    await run_session(device, firmware)

    assert_v4_session(firmware, data)
    assert not device._awaiting_fd50_identity
    device.set_ble_device_and_advertisement_data(device._ble_device, stored[-1])
    assert device._identity_connect_task is None


async def test_failed_identity_connection_retries_without_new_advertisement() -> None:
    """Backoff retries an unchanged identity without resetting sequence numbers."""
    credentials = make_credentials()
    data = report(soil_sensor_datapoints())
    firmware = FakeV4Firmware(credentials, data)
    device = make_unadvertised_device(credentials)
    device.set_ble_device_and_advertisement_data(device._ble_device, beacon())
    with pytest.raises(TuyaBLEDeviceError):
        await device.update()

    identity = without_scan_response(credentials)
    device._current_seq_num = 20
    with (
        patch(
            ESTABLISH, new_callable=AsyncMock, side_effect=[None] * 100 + [firmware]
        ) as connect,
        patch("custom_components.tuya_ble.tuya_ble.base.BLEAK_BACKOFF_TIME", 0),
    ):
        device.set_ble_device_and_advertisement_data(device._ble_device, identity)
        first = device._identity_connect_task
        assert first is not None
        await asyncio.wait_for(first, 1)
        assert connect.await_count == 101
        assert device._current_seq_num > 20
        assert not device._awaiting_fd50_identity
        device.set_ble_device_and_advertisement_data(device._ble_device, identity)
        assert device._identity_connect_task is first
        await device.update()
        await asyncio.wait_for(firmware.acknowledged.wait(), 1)
        await drain_responses(device)
        await device.stop()

    assert_v4_session(firmware, data)


async def test_device_without_credentials_still_needs_the_scan_response(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The identity record proves nothing about which device sent it."""
    device = make_device()
    device.set_ble_device_and_advertisement_data(
        device._ble_device, without_scan_response(make_credentials())
    )

    with (
        patch(ESTABLISH, new_callable=AsyncMock) as connect,
        caplog.at_level(logging.DEBUG),
        pytest.raises(TuyaBLEDeviceError),
    ):
        await device.update()

    connect.assert_not_awaited()
    assert "Waiting for a supported FD50 identity" in caplog.text


async def test_restart_waits_on_a_beacon_then_connects(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A beacon arriving mid-connect closes that link and waits.

    A beacon frame carries no identity record, so the protocol version is
    unknown. The link must be closed without any handshake instead of
    retrying with protocol 2, and the next identity record, even without its
    scan response, must start the protocol-4 session on its own.
    """
    credentials = make_credentials()
    data = report(soil_sensor_datapoints())
    early = FakeV4Firmware(credentials, data)
    late = FakeV4Firmware(credentials, data)
    clients = [early, late]
    device = make_unadvertised_device(credentials)
    connected: list[bool] = []
    device.register_connected_callback(lambda: connected.append(True))

    async def establish(*_args: object, **_kwargs: object) -> FakeV4Firmware:
        if clients[0] is early:
            device.set_ble_device_and_advertisement_data(device._ble_device, beacon())
        return clients.pop(0)

    with (
        patch(ESTABLISH, side_effect=establish) as connect,
        caplog.at_level(logging.DEBUG),
    ):
        with pytest.raises(TuyaBLEDeviceError):
            await device.update()
        assert connect.await_count == 1
        assert not early.is_connected
        assert not early.received
        assert not early.services.lookups
        assert device._client is None
        assert caplog.text.count("Waiting for a supported FD50 identity") == 1

        # Another beacon changes nothing.
        device.set_ble_device_and_advertisement_data(device._ble_device, beacon())
        assert device._identity_connect_task is None

        device.set_ble_device_and_advertisement_data(
            device._ble_device, without_scan_response(credentials)
        )
        task = device._identity_connect_task
        assert task is not None
        await task
        assert connected == [True]
        assert connect.await_count == 2

        # What the coordinator does once connected.
        await device.update()
        await asyncio.wait_for(late.acknowledged.wait(), 1)
        await drain_responses(device)

        # The closed link's disconnect callback may only arrive now.
        caplog.clear()
        device._disconnected(early)
        assert device._client is late
        assert device._is_paired
        assert "before the FD50 identity arrived" in caplog.text
        assert "unexpectedly disconnected" not in caplog.text
        await device.stop()

    assert_v4_session(late, data)
    assert device.protocol_version == "4.2"


async def test_waiting_device_does_not_connect_until_the_identity_arrives(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A stored FD50 beacon blocks before connecting."""
    credentials = make_credentials()
    device = make_unadvertised_device(credentials)
    device.set_ble_device_and_advertisement_data(device._ble_device, beacon())
    attempts_exhausted = asyncio.Event()

    async def unavailable(*_args: object, **_kwargs: object) -> None:
        if connect.await_count == 100:
            attempts_exhausted.set()

    with (
        patch(ESTABLISH, new_callable=AsyncMock, side_effect=unavailable) as connect,
        caplog.at_level(logging.DEBUG),
    ):
        for _ in range(2):
            with pytest.raises(TuyaBLEDeviceError):
                await device.update()
        connect.assert_not_awaited()
        assert caplog.text.count("Waiting for a supported FD50 identity") == 1

        # The identity record arrives, but the device is out of reach: every
        # connect attempt fails and the failure is logged, not raised.
        device.set_ble_device_and_advertisement_data(
            device._ble_device, without_scan_response(credentials)
        )
        task = device._identity_connect_task
        assert task is not None
        await asyncio.wait_for(attempts_exhausted.wait(), 1)
        assert connect.await_count == 100
        assert "Connecting after the FD50 identity arrived failed" in caplog.text
        await device.stop()
        assert task.cancelled()
        assert connect.await_count == 100


async def test_identity_connect_runs_once_and_never_after_stop() -> None:
    """Only one identity connection is pending, and stop cancels it."""
    credentials = make_credentials()
    device = make_v4_device(credentials)
    complete = v4_advertisement(uuid=credentials.uuid.encode())

    with patch.object(device, "_ensure_connected", new_callable=AsyncMock) as ensure:
        device._awaiting_fd50_identity = True
        device.set_ble_device_and_advertisement_data(device._ble_device, complete)
        first = device._identity_connect_task
        assert first is not None

        device._awaiting_fd50_identity = True
        device.set_ble_device_and_advertisement_data(device._ble_device, complete)
        assert device._identity_connect_task is first

        await device.stop()
        assert first.cancelled()
        ensure.assert_not_awaited()

        device._awaiting_fd50_identity = True
        device.set_ble_device_and_advertisement_data(device._ble_device, complete)
        assert device._identity_connect_task is first


async def test_identity_retry_exits_when_stop_is_requested_during_failure() -> None:
    """An intentional stop prevents another connection cycle after backoff."""
    device = make_v4_device(make_credentials())
    device._awaiting_fd50_identity = True

    async def fail_while_stopping() -> None:
        device._expected_disconnect = True
        raise TuyaBLEDeviceError(0)

    with (
        patch.object(
            device, "_ensure_connected", side_effect=fail_while_stopping
        ) as ensure,
        patch("custom_components.tuya_ble.tuya_ble.base.BLEAK_BACKOFF_TIME", 0),
    ):
        device.set_ble_device_and_advertisement_data(
            device._ble_device, v4_advertisement()
        )
        task = device._identity_connect_task
        assert task is not None
        await task
        ensure.assert_awaited_once()
        await device.stop()


async def test_failure_to_close_the_unused_connection_is_only_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failing disconnect of the early link still ends in the wait."""
    credentials = make_credentials()
    device = make_unadvertised_device(credentials)
    client = FakeBleakClient()
    close = client.disconnect
    disconnect = AsyncMock(side_effect=BleakError("gone"))
    client.disconnect = disconnect  # type: ignore[method-assign]

    async def establish(*_args: object, **_kwargs: object) -> FakeBleakClient:
        device.set_ble_device_and_advertisement_data(device._ble_device, beacon())
        return client

    with (
        patch(ESTABLISH, side_effect=establish),
        caplog.at_level(logging.DEBUG),
        pytest.raises(TuyaBLEDeviceError),
    ):
        await device.update()

    disconnect.assert_awaited_once()
    assert "Closing the unused connection failed" in caplog.text
    assert device._client is None
    assert not client.writes
    assert client in device._pending_client_cleanup  # type: ignore[comparison-overlap]

    disconnect.side_effect = close
    await device.stop()
    assert disconnect.await_count == 2
    assert not client.is_connected
    assert not device._pending_client_cleanup


async def test_failed_discard_is_cleaned_before_reconnecting() -> None:
    """An unused active client is closed before a replacement is established."""
    credentials = make_credentials()
    data = report(soil_sensor_datapoints())
    device = make_unadvertised_device(credentials)
    early = FakeBleakClient()
    close = early.disconnect
    disconnect = AsyncMock(side_effect=BleakError("still active"))
    early.disconnect = disconnect  # type: ignore[method-assign]

    async def establish_early(*_args: object, **_kwargs: object) -> FakeBleakClient:
        device.set_ble_device_and_advertisement_data(device._ble_device, beacon())
        return early

    with (
        patch(ESTABLISH, side_effect=establish_early),
        pytest.raises(TuyaBLEDeviceError),
    ):
        await device.update()

    disconnect.side_effect = close
    late = FakeV4Firmware(credentials, data)

    async def establish_late(*_args: object, **_kwargs: object) -> FakeV4Firmware:
        assert not early.is_connected
        return late

    with patch(ESTABLISH, side_effect=establish_late):
        device.set_ble_device_and_advertisement_data(
            device._ble_device, without_scan_response(credentials)
        )
        task = device._identity_connect_task
        assert task is not None
        await task
        assert disconnect.await_count == 2
        device._disconnected(early)  # type: ignore[arg-type]
        assert device._client is late  # type: ignore[comparison-overlap]
        assert device._is_paired
        await device.stop()


async def test_cleanup_preserves_current_client_and_drops_already_closed_links() -> (
    None
):
    """Cleanup skips the owned client and never disconnects a closed link twice."""
    device = make_v4_device(make_credentials())
    current = FakeBleakClient()
    closed = FakeBleakClient(is_connected=False)
    device._client = current  # type: ignore[assignment]
    device._pending_client_cleanup.update((current, closed))  # type: ignore[arg-type]
    current_disconnect = AsyncMock(wraps=current.disconnect)
    current.disconnect = current_disconnect  # type: ignore[method-assign]
    closed.disconnect = AsyncMock()  # type: ignore[method-assign]

    await device._cleanup_discarded_clients()
    current_disconnect.assert_not_awaited()
    closed.disconnect.assert_not_awaited()
    assert closed not in device._pending_client_cleanup  # type: ignore[comparison-overlap]
    await device.stop()
    current_disconnect.assert_awaited_once()
    assert not device._pending_client_cleanup


async def test_multiple_discarded_callbacks_do_not_clear_replacement_client() -> None:
    """All discarded callbacks remain isolated even if they arrive out of order."""
    device = make_v4_device(make_credentials())
    first, second, current = FakeBleakClient(), FakeBleakClient(), FakeBleakClient()
    await device._discard_client(first)  # type: ignore[arg-type]
    await device._discard_client(second)  # type: ignore[arg-type]
    device._client = current  # type: ignore[assignment]
    device._is_paired = True
    device._disconnected(second)  # type: ignore[arg-type]
    device._disconnected(first)  # type: ignore[arg-type]
    assert device._client is current  # type: ignore[comparison-overlap]
    assert device._is_paired
    assert not device._discarded_clients
    await device.stop()


async def test_classic_advertisement_never_waits_for_an_fd50_identity() -> None:
    """A classic advertisement arriving mid-connect keeps the v2/v3 handshake."""
    device = make_unadvertised_device(make_credentials())
    classic = cast(
        AdvertisementData,
        FakeAdvertisementData(
            service_data={SERVICE_UUID: b"\x00abcdefgh"},
            manufacturer_data={MANUFACTURER_DATA_ID: b"\x80\x03" + bytes(20)},
        ),
    )
    assert SERVICE_UUID_V4 not in classic.service_data
    codes: list[TuyaBLECode] = []

    async def establish(*_args: object, **_kwargs: object) -> FakeBleakClient:
        device.set_ble_device_and_advertisement_data(device._ble_device, classic)
        return FakeBleakClient()

    async def send(
        code: TuyaBLECode, _data: bytes, _response_to: int, _wait: bool
    ) -> bool:
        codes.append(code)
        device._session_key = bytes(16)
        if code == TuyaBLECode.FUN_SENDER_PAIR:
            device._is_paired = True
        return True

    with (
        patch(ESTABLISH, side_effect=establish),
        patch.object(device, "_send_packet_while_connected", side_effect=send),
    ):
        await device.update()

    assert codes == [
        TuyaBLECode.FUN_SENDER_DEVICE_INFO,
        TuyaBLECode.FUN_SENDER_PAIR,
        TuyaBLECode.FUN_SENDER_DEVICE_STATUS,
    ]
    assert not device._awaiting_fd50_identity
    assert device._identity_connect_task is None
