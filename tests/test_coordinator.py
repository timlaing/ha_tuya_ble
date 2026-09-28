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
from custom_components.tuya_ble.tuya_ble import (
    TuyaBLEDataPoint,
    TuyaBLEDataPointType,
    TuyaBLEDevice,
)
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


def _coord_with_product(
    hass: HomeAssistant, category: str, product_id: str
) -> tuple[TuyaBLECoordinator, TuyaBLEDevice]:
    """Build a coordinator for a device with the given product identity."""
    device = make_device()
    device._device_info = make_credentials(category=category, product_id=product_id)
    return TuyaBLECoordinator(hass, device), device


def _receive(
    device: TuyaBLEDevice,
    dp_id: int,
    dp_type: TuyaBLEDataPointType,
    value: bytes | bool | int | str,
    raw: bytes,
) -> list[TuyaBLEDataPoint]:
    """Feed a data point in as if the device had reported it, returning the batch."""
    device.datapoints.update_from_device(dp_id, 1.0, 0, dp_type, value, raw)
    dp = device.datapoints[dp_id]
    assert dp is not None
    return [dp]


def test_unmapped_dp_is_reported_with_raw_bytes(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """An unsupported data point is reported with context and the wire bytes."""
    coordinator, device = _coord_with_product(hass, "wk", "drlajpqc")

    caplog.set_level(logging.DEBUG)
    coordinator._async_handle_update(
        _receive(device, 200, TuyaBLEDataPointType.DT_VALUE, 100, b"\x00\x00\x00\x64")
    )

    assert (
        f"{ADDRESS}: Unmapped DP id=200 type=DT_VALUE raw=00000064 decoded=100 "
        "not used by any entity of wk/drlajpqc" in caplog.text
    )


def test_mapped_dp_is_not_reported_as_unmapped(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A data point the product's entities use is not reported."""
    coordinator, device = _coord_with_product(hass, "wk", "drlajpqc")

    caplog.set_level(logging.DEBUG)
    coordinator._async_handle_update(
        _receive(device, 102, TuyaBLEDataPointType.DT_VALUE, 21, b"\x00\x15")
    )

    assert "Unmapped DP" not in caplog.text


def test_auxiliary_and_disabled_entity_ids_count_as_mapped(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Ids used only by a composite entity or a disabled entity count as mapped."""
    coordinator, device = _coord_with_product(hass, "cl", "4pbr8eig")

    caplog.set_level(logging.DEBUG)
    coordinator._async_handle_update(
        _receive(device, 105, TuyaBLEDataPointType.DT_VALUE, 20, b"\x14")
    )
    coordinator._async_handle_update(
        _receive(device, 7, TuyaBLEDataPointType.DT_ENUM, 0, b"\x00")
    )

    assert "Unmapped DP" not in caplog.text


def test_repeated_unchanged_unmapped_dp_is_suppressed(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Re-reporting the same data point must not flood the log."""
    coordinator, device = _coord_with_product(hass, "wk", "drlajpqc")
    updates = _receive(device, 200, TuyaBLEDataPointType.DT_VALUE, 100, b"\x64")

    caplog.set_level(logging.DEBUG)
    coordinator._async_handle_update(updates)
    coordinator._async_handle_update(updates)
    coordinator._async_handle_update(updates)

    assert caplog.text.count("Unmapped DP") == 1


def test_changed_unmapped_dp_is_reported_again(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A data point that changes value is reported once more."""
    coordinator, device = _coord_with_product(hass, "wk", "drlajpqc")

    caplog.set_level(logging.DEBUG)
    coordinator._async_handle_update(
        _receive(device, 200, TuyaBLEDataPointType.DT_VALUE, 100, b"\x64")
    )
    coordinator._async_handle_update(
        _receive(device, 200, TuyaBLEDataPointType.DT_VALUE, 101, b"\x65")
    )

    assert caplog.text.count("Unmapped DP") == 2
    assert "raw=65 decoded=101" in caplog.text


def test_unmapped_dp_type_change_is_reported_again(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A data point that changes type is reported once more."""
    coordinator, device = _coord_with_product(hass, "wk", "drlajpqc")

    caplog.set_level(logging.DEBUG)
    coordinator._async_handle_update(
        _receive(device, 200, TuyaBLEDataPointType.DT_VALUE, 1, b"\x01")
    )
    coordinator._async_handle_update(
        _receive(device, 200, TuyaBLEDataPointType.DT_STRING, "x", b"x")
    )

    assert caplog.text.count("Unmapped DP") == 2


def test_unmapped_dp_for_unknown_product(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A device missing from the registry is called out separately."""
    coordinator, device = _coord_with_product(hass, "zz", "unknownpid")

    caplog.set_level(logging.DEBUG)
    coordinator._async_handle_update(
        _receive(device, 1, TuyaBLEDataPointType.DT_BOOL, True, b"\x01")
    )

    assert "unknown product zz/unknownpid" in caplog.text
    assert "Unmapped DP id=1 type=DT_BOOL raw=01 decoded=True" in caplog.text


def test_unmapped_report_is_skipped_when_debug_disabled(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """No per-data-point output, and no registry lookup, at INFO level."""
    coordinator, device = _coord_with_product(hass, "wk", "drlajpqc")

    caplog.set_level(logging.INFO)
    coordinator._async_handle_update(
        _receive(device, 200, TuyaBLEDataPointType.DT_VALUE, 100, b"\x64")
    )

    assert "Unmapped DP" not in caplog.text
    assert coordinator._dp_classification is None


def test_dp_classification_is_resolved_once(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """The registry is consulted once per coordinator, not once per update."""
    coordinator, device = _coord_with_product(hass, "wk", "drlajpqc")

    caplog.set_level(logging.DEBUG)
    for value in (1, 2, 3):
        coordinator._async_handle_update(
            _receive(device, 200, TuyaBLEDataPointType.DT_VALUE, value, bytes([value]))
        )

    known_product, mapped = coordinator._resolve_dp_classification()
    assert known_product is True
    assert 102 in mapped
    assert coordinator._resolve_dp_classification()[1] is mapped
