"""Unit tests for the Tuya BLE data coordinator."""
# pylint: disable=protected-access
# The tests deliberately set up device/coordinator internals as test setup
# state, which is sanctioned by the project's test conventions.

from __future__ import annotations

import logging
from unittest.mock import patch

from homeassistant.core import Event, HomeAssistant
import pytest

from custom_components.tuya_ble.const import (
    FINGERBOT_BUTTON_EVENT,
    SET_DISCONNECTED_DELAY,
)
from custom_components.tuya_ble.coordinator import TuyaBLECoordinator
from custom_components.tuya_ble.devices import TuyaBLEFingerbotInfo, TuyaBLEProductInfo
from custom_components.tuya_ble.tuya_ble import TuyaBLEDataPointType, TuyaBLEDevice
from tests.conftest import make_credentials, make_device

ADDRESS = "AA:BB:CC:DD:EE:FF"


def _make_coord(hass: HomeAssistant) -> tuple[TuyaBLECoordinator, TuyaBLEDevice]:
    """Build a coordinator and device wired together for tests."""
    device = make_device()
    device._device_info = make_credentials()
    return TuyaBLECoordinator(hass, device), device


def _fingerbot_product(manual_control: int) -> TuyaBLEProductInfo:
    """Return a product info carrying fingerbot specs with manual control."""
    return TuyaBLEProductInfo(
        name="Fingerbot",
        fingerbot=TuyaBLEFingerbotInfo(
            switch=1,
            mode=2,
            up_position=5,
            down_position=6,
            hold_time=3,
            reverse_positions=4,
            manual_control=manual_control,
        ),
    )


def test_initial_state_disconnected(hass: HomeAssistant) -> None:
    """Verify the coordinator starts disconnected."""
    coordinator, _ = _make_coord(hass)
    assert coordinator.connected is False


def test_connect_transition_is_logged(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Log the disconnected -> connected transition, not every subsequent update."""
    coordinator, _ = _make_coord(hass)

    caplog.set_level(logging.DEBUG)
    coordinator._async_handle_connect()

    assert coordinator.connected is True
    assert f"{ADDRESS}: Connected" in caplog.text

    caplog.clear()
    coordinator._async_handle_connect()
    assert "Connected" not in caplog.text


def test_connect_cancels_pending_disconnect(
    hass: HomeAssistant,
) -> None:
    """Connecting again cancels the pending delayed disconnect."""
    coordinator, _ = _make_coord(hass)
    cancelled: list[bool] = []

    coordinator._unsub_disconnect = lambda: cancelled.append(True)
    coordinator._async_handle_connect()

    assert cancelled == [True]


def test_update_batch_logs_data_point_ids(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """The update log reports how many data points arrived and which ones."""
    coordinator, device = _make_coord(hass)
    datapoints = device.datapoints
    updates = [
        datapoints.get_or_create(1, TuyaBLEDataPointType.DT_BOOL),
        datapoints.get_or_create(2, TuyaBLEDataPointType.DT_BOOL),
    ]

    caplog.set_level(logging.DEBUG)
    coordinator._async_handle_update(updates)

    assert f"{ADDRESS}: Received update with 2 data point(s): [1, 2]" in caplog.text


def test_update_batch_logs_empty_batch(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """An empty update batch is still traced."""
    coordinator, _ = _make_coord(hass)

    caplog.set_level(logging.DEBUG)
    coordinator._async_handle_update([])

    assert f"{ADDRESS}: Received update with 0 data point(s): []" in caplog.text


async def test_fingerbot_event_is_logged_and_fired(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Fire and log the fingerbot button event for a changed switch data point."""
    coordinator, device = _make_coord(hass)
    device.datapoints.update_from_device(
        1, 1000.0, 0, TuyaBLEDataPointType.DT_BOOL, False
    )
    device.datapoints.update_from_device(
        1, 1001.0, 0, TuyaBLEDataPointType.DT_BOOL, True
    )
    update = device.datapoints[1]
    assert update is not None
    captured: list[Event] = []
    hass.bus.async_listen(FINGERBOT_BUTTON_EVENT, captured.append)

    caplog.set_level(logging.DEBUG)
    with patch(
        "custom_components.tuya_ble.coordinator.get_device_product_info",
        return_value=_fingerbot_product(manual_control=7),
    ):
        coordinator._async_handle_update([update])
    await hass.async_block_till_done()

    assert f"{ADDRESS}: Fingerbot button event for data point 1" in caplog.text
    assert len(captured) == 1


def test_idle_timeout_marks_disconnected(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """The idle timeout callback flips the coordinator back to disconnected."""
    coordinator, _ = _make_coord(hass)
    coordinator._async_handle_connect()
    assert coordinator.connected is True

    caplog.set_level(logging.DEBUG)
    coordinator._set_disconnected(None)

    assert coordinator.connected is False
    assert f"{ADDRESS}: Idle timeout, marked as disconnected" in caplog.text


async def test_disconnect_schedules_delayed_transition(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A disconnect schedules, but does not immediately apply, the transition."""
    coordinator, _ = _make_coord(hass)
    coordinator._async_handle_connect()

    caplog.set_level(logging.DEBUG)
    coordinator._async_handle_disconnect()

    assert (
        f"{ADDRESS}: Disconnected, marking as disconnected in "
        f"{SET_DISCONNECTED_DELAY}s" in caplog.text
    )
    assert coordinator.connected is True
    assert coordinator._unsub_disconnect is not None
    await coordinator.async_shutdown()


async def test_second_disconnect_is_skipped(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A second disconnect while one is pending does not reschedule the timer."""
    coordinator, _ = _make_coord(hass)
    coordinator._async_handle_connect()
    coordinator._async_handle_disconnect()
    scheduled = coordinator._unsub_disconnect

    caplog.set_level(logging.DEBUG)
    coordinator._async_handle_disconnect()

    assert f"{ADDRESS}: Already scheduled to disconnect, skipping" in caplog.text
    assert coordinator._unsub_disconnect is scheduled
    await coordinator.async_shutdown()


async def test_scheduled_disconnect_fires_after_delay(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Firing the scheduled handler disconnects and clears the pending timer."""
    coordinator, _ = _make_coord(hass)
    coordinator._async_handle_connect()
    coordinator._async_handle_disconnect()
    assert coordinator._unsub_disconnect is not None
    cancel_timer = coordinator._unsub_disconnect

    caplog.set_level(logging.DEBUG)
    coordinator._set_disconnected(None)

    assert coordinator.connected is False
    assert coordinator._unsub_disconnect is None
    cancel_timer()
    await coordinator.async_shutdown()


async def test_shutdown_cancels_pending_disconnect(
    hass: HomeAssistant,
) -> None:
    """Shutting the coordinator down cancels a pending delayed disconnect."""
    coordinator, _ = _make_coord(hass)
    coordinator._async_handle_connect()
    coordinator._async_handle_disconnect()
    assert coordinator._unsub_disconnect is not None

    await coordinator.async_shutdown()

    assert coordinator._unsub_disconnect is None


async def test_shutdown_without_pending_disconnect(
    hass: HomeAssistant,
) -> None:
    """Shutting down with nothing pending is a no-op."""
    coordinator, _ = _make_coord(hass)

    await coordinator.async_shutdown()

    assert coordinator._unsub_disconnect is None
