"""Regression tests for bounded BLE retries and outage logging."""

# pylint: disable=protected-access
from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, call, patch

from bleak.exc import BleakError
from bleak_retry_connector import BleakAbortedError, BleakNotFoundError
import pytest

from custom_components.tuya_ble.tuya_ble.exceptions import TuyaBLEDeviceError
from tests.conftest import FakeBleakClient, make_credentials, make_device
from tests.protocol_harness import v4_advertisement

BASE = "custom_components.tuya_ble.tuya_ble.base"
pytestmark = pytest.mark.filterwarnings("ignore::RuntimeWarning")


@pytest.fixture(autouse=True)
def warning_logs(caplog: pytest.LogCaptureFixture) -> None:
    """Exclude debug setup logs unless the test explicitly enables them."""
    caplog.set_level(logging.WARNING)


@pytest.mark.parametrize(
    "error",
    [
        BleakAbortedError("Error ESP_GATT_ERROR while connecting"),
        BleakNotFoundError("Device out of range"),
        BleakError("Connection failed"),
        OSError("Adapter unavailable"),
        TuyaBLEDeviceError(0),
    ],
)
async def test_connection_outage_warns_once_without_traceback(
    error: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    """Repeated public status requests log one warning per failed device."""
    device = make_device()
    device.initialize_with_credentials(make_credentials())
    with (
        patch(f"{BASE}.establish_connection", side_effect=error) as connect,
        caplog.at_level(logging.WARNING),
    ):
        for _ in range(3):
            with pytest.raises(BleakNotFoundError):
                await device.update()

    assert connect.await_count == 9
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.WARNING
    assert caplog.records[0].exc_info is None
    assert "Unable to connect after 3 connection cycles" in caplog.text
    assert "Traceback" not in caplog.text


async def test_retry_tracebacks_remain_available_at_debug(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Debug logging retains the original GATT failure and traceback."""
    device = make_device()
    device.initialize_with_credentials(make_credentials())
    with (
        patch(
            f"{BASE}.establish_connection",
            side_effect=BleakAbortedError("ESP_GATT_ERROR"),
        ),
        caplog.at_level(logging.DEBUG),
        pytest.raises(BleakNotFoundError),
    ):
        await device.update()

    failures = [record for record in caplog.records if record.exc_info]
    assert len(failures) == 3
    assert all(record.levelno == logging.DEBUG for record in failures)
    assert "ESP_GATT_ERROR" in caplog.text


async def test_successful_handshake_resets_outage_warning_and_backoff(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Recovery re-arms the warning for a later outage, not merely a BLE link."""
    device = make_device()
    device.initialize_with_credentials(make_credentials())
    client = FakeBleakClient(is_connected=True)

    async def configure() -> bool:
        device._is_paired = True
        return True

    with (
        patch(f"{BASE}.establish_connection", side_effect=BleakAbortedError),
        caplog.at_level(logging.WARNING),
        pytest.raises(BleakNotFoundError),
    ):
        await device.update()
    device._reconnect_delay = 60
    with (
        patch(f"{BASE}.establish_connection", return_value=client),
        patch.object(device, "_configure_client", side_effect=configure),
        patch.object(device, "_send_packet_while_connected", return_value=True),
    ):
        await device.update()
    assert device._is_paired
    assert device._reconnect_delay == 5
    assert not device._connection_failure_reported

    # A later outage is distinct from the recovered session.
    device._client = None
    device._is_paired = False
    with (
        patch(f"{BASE}.establish_connection", side_effect=BleakAbortedError),
        caplog.at_level(logging.WARNING),
        pytest.raises(BleakNotFoundError),
    ):
        await device.update()
    assert len(caplog.records) == 2
    assert all(record.exc_info is None for record in caplog.records)


async def test_unpaired_handshake_does_not_reset_outage_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A live BLE link without a successful handshake is still the same outage."""
    device = make_device()
    device.initialize_with_credentials(make_credentials())
    clients = [FakeBleakClient(is_connected=True) for _ in range(6)]
    with (
        patch(f"{BASE}.establish_connection", side_effect=clients),
        patch.object(device, "_configure_client", return_value=False),
        caplog.at_level(logging.WARNING),
    ):
        for _ in range(2):
            with pytest.raises(BleakNotFoundError):
                await device.update()
    assert len(caplog.records) == 1
    assert device._connection_failure_reported


async def test_cancelled_connection_does_not_warn_or_retry(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Cancellation remains cancellation rather than a recoverable BLE failure."""
    device = make_device()
    device.initialize_with_credentials(make_credentials())
    with (
        patch(
            f"{BASE}.establish_connection", side_effect=asyncio.CancelledError
        ) as connect,
        caplog.at_level(logging.WARNING),
        pytest.raises(asyncio.CancelledError),
    ):
        await device.update()
    connect.assert_awaited_once()
    assert not caplog.records


async def test_legacy_reconnect_backoff_is_exponential_and_capped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Legacy devices also back off without emitting reconnect tracebacks."""
    device = make_device()
    with (
        patch.object(device, "_ensure_connected", side_effect=BleakAbortedError),
        patch.object(device, "_schedule_reconnect") as schedule,
        patch(f"{BASE}.asyncio.sleep", new_callable=AsyncMock) as sleep,
        caplog.at_level(logging.WARNING),
    ):
        for _ in range(6):
            await device._reconnect()
    assert sleep.await_args_list == [call(delay) for delay in (5, 10, 20, 40, 60, 60)]
    assert device._reconnect_delay == 60
    assert schedule.call_count == 6
    assert not caplog.records


async def test_identity_connection_backoff_is_exponential_and_capped() -> None:
    """Advertisement-triggered retries use the same capped outage backoff."""
    device = make_device()
    credentials = make_credentials()
    device.initialize_with_credentials(credentials)
    device._awaiting_fd50_identity = True
    delays: list[float] = []

    async def backoff(delay: float) -> None:
        delays.append(delay)
        if len(delays) == 6:
            device._expected_disconnect = True

    with (
        patch.object(device, "_ensure_connected", side_effect=BleakAbortedError),
        patch(f"{BASE}.asyncio.sleep", side_effect=backoff),
    ):
        device.set_ble_device_and_advertisement_data(
            device._ble_device, v4_advertisement(uuid=credentials.uuid.encode())
        )
        task = device._identity_connect_task
        assert task is not None
        await task
    assert delays == [5, 10, 20, 40, 60, 60]
    await device.stop()
