"""Regression tests for the Maxmarr tubular motor descriptor (issue #145)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.components.binary_sensor.const import BinarySensorDeviceClass
from homeassistant.components.cover import ATTR_POSITION, CoverEntityFeature
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
import pytest

from custom_components.tuya_ble import binary_sensor, cover, select, sensor
from custom_components.tuya_ble.coordinator import TuyaBLECoordinator
from custom_components.tuya_ble.device_registry import get_mapped_dp_ids, get_registry
from custom_components.tuya_ble.entity import (
    TuyaBLEProductInfo,
    get_device_product_info,
)
from custom_components.tuya_ble.tuya_ble import TuyaBLEDataPointType, TuyaBLEDevice
from tests.conftest import add_dp, make_credentials, make_device


def _context(
    hass: HomeAssistant,
) -> tuple[TuyaBLEDevice, TuyaBLECoordinator, TuyaBLEProductInfo]:
    """Build a real device and coordinator using the shipped descriptor."""
    device = make_device()
    device.initialize_with_credentials(
        make_credentials(category="cl", product_id="mnet9kgf")
    )
    return device, TuyaBLECoordinator(hass, device), get_device_product_info(device)


def test_maxmarr_descriptor_metadata_and_dp_coverage() -> None:
    """Resolve the product and leave calibration/undocumented DPs unmapped."""
    descriptor = get_registry().get("cl", "mnet9kgf")
    assert descriptor is not None
    assert descriptor.device_name == "Tubular Motor"
    assert descriptor.manufacturer == "Maxmarr"
    assert descriptor.model_name == "mnet9kgf"
    assert get_mapped_dp_ids("cl", "mnet9kgf") == {1, 2, 3, 5, 7, 12, 13}


@pytest.mark.parametrize("raw_position", [0, 30, 100])
async def test_maxmarr_cover_reports_inverted_position(
    hass: HomeAssistant, raw_position: int
) -> None:
    """Raw 0 is fully open; use the existing cover key without tilt support."""
    device, coordinator, product = _context(hass)
    mappings = cover.get_mapping_by_device(device)
    assert len(mappings) == 1
    entity = cover.TuyaBLECover(hass, coordinator, device, product, mappings[0])
    assert entity.entity_description.key == "ble_cover"
    assert entity.supported_features == (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.STOP
        | CoverEntityFeature.SET_POSITION
    )
    with patch.object(entity, "async_write_ha_state") as write_state:
        await entity.async_added_to_hass()
        add_dp(device, 3, TuyaBLEDataPointType.DT_VALUE, raw_position)
        coordinator.async_set_updated_data({})
    assert entity.current_cover_position == 100 - raw_position
    assert entity.is_closed is (raw_position == 100)
    write_state.assert_called_once()


async def test_maxmarr_cover_commands_before_first_report(hass: HomeAssistant) -> None:
    """Open/close/stop use ENUM even before DP1 has been reported."""
    device, coordinator, product = _context(hass)
    entity = cover.TuyaBLECover(
        hass, coordinator, device, product, cover.get_mapping_by_device(device)[0]
    )
    with (
        patch.object(device, "send_datapoints", new_callable=AsyncMock) as send,
        patch.object(entity, "async_write_ha_state"),
    ):
        for command, expected in (
            (entity.async_open_cover, 0),
            (entity.async_close_cover, 2),
            (entity.async_stop_cover, 1),
        ):
            await command()
            datapoint = device.datapoints[1]
            assert datapoint is not None
            assert datapoint.dp_type == TuyaBLEDataPointType.DT_ENUM
            assert datapoint.value == expected
            send.assert_awaited_with([1])
    assert send.await_count == 3


@pytest.mark.parametrize("ha_position", [0, 25, 100])
async def test_maxmarr_target_position_is_inverted(
    hass: HomeAssistant, ha_position: int
) -> None:
    """HA's requested position is inverted on DP2, not written to DP3."""
    device, coordinator, product = _context(hass)
    entity = cover.TuyaBLECover(
        hass, coordinator, device, product, cover.get_mapping_by_device(device)[0]
    )
    with patch.object(device, "send_datapoints", new_callable=AsyncMock) as send:
        await entity.async_set_cover_position(**{ATTR_POSITION: ha_position})
    datapoint = device.datapoints[2]
    assert datapoint is not None
    assert datapoint.dp_type == TuyaBLEDataPointType.DT_VALUE
    assert datapoint.value == 100 - ha_position
    assert device.datapoints[3] is None
    send.assert_awaited_once_with([2])


async def test_maxmarr_battery_and_optional_work_state(hass: HomeAssistant) -> None:
    """Expose DP13 as a battery and DP7 as an optional enum diagnostic."""
    device, coordinator, product = _context(hass)
    mappings = sensor.get_mapping_by_device(device)
    assert [mapping.dp_id for mapping in mappings] == [13, 7]
    battery, work_state = [
        sensor.TuyaBLESensor(hass, coordinator, device, product, mapping)
        for mapping in mappings
    ]
    assert battery.device_class == SensorDeviceClass.BATTERY
    assert battery.native_unit_of_measurement == "%"
    assert battery.state_class == SensorStateClass.MEASUREMENT
    assert battery.entity_category == EntityCategory.DIAGNOSTIC
    assert work_state.device_class == SensorDeviceClass.ENUM
    assert not work_state.entity_registry_enabled_default
    with (
        patch.object(battery, "async_write_ha_state"),
        patch.object(work_state, "async_write_ha_state"),
    ):
        await battery.async_added_to_hass()
        await work_state.async_added_to_hass()
        add_dp(device, 13, TuyaBLEDataPointType.DT_VALUE, 30)
        for value, expected in ((0, "opening"), (1, "closing"), (2, None)):
            add_dp(device, 7, TuyaBLEDataPointType.DT_ENUM, value)
            coordinator.async_set_updated_data({})
            assert work_state.native_value == expected
    assert battery.native_value == 30


@pytest.mark.parametrize("fault", [b"\x00", b"\x01", b"\x00\x00"])
async def test_maxmarr_motor_fault_bitmap(hass: HomeAssistant, fault: bytes) -> None:
    """Zero bitmap payloads are healthy rather than truthy byte strings."""
    device, coordinator, product = _context(hass)
    mappings = binary_sensor.get_mapping_by_device(device)
    assert len(mappings) == 1
    assert mappings[0].dp_id == 12
    assert mappings[0].dp_type == TuyaBLEDataPointType.DT_BITMAP
    entity = binary_sensor.TuyaBLEBinarySensor(
        hass, coordinator, device, product, mappings[0]
    )
    assert entity.device_class == BinarySensorDeviceClass.PROBLEM
    with patch.object(entity, "async_write_ha_state"):
        await entity.async_added_to_hass()
        add_dp(device, 12, TuyaBLEDataPointType.DT_BITMAP, fault)
        coordinator.async_set_updated_data({})
    assert entity.is_on is any(fault)


@pytest.mark.parametrize("value,option", [(0, "Forward"), (1, "Back")])
async def test_maxmarr_motor_direction_read_and_write(
    hass: HomeAssistant, value: int, option: str
) -> None:
    """The reported Back index and both direction writes use DP5 ENUM."""
    device, coordinator, product = _context(hass)
    mappings = select.get_mapping_by_device(device)
    assert len(mappings) == 1
    assert mappings[0].dp_id == 5
    entity = select.TuyaBLESelect(hass, coordinator, device, product, mappings[0])
    entity.hass = hass
    assert entity.options == ["Forward", "Back"]
    assert entity.entity_category == EntityCategory.CONFIG
    assert entity.current_option is None
    with patch.object(device, "send_datapoints", new_callable=AsyncMock) as send:
        entity.select_option(option)
        await hass.async_block_till_done()
    datapoint = device.datapoints[5]
    assert datapoint is not None
    assert datapoint.dp_type == TuyaBLEDataPointType.DT_ENUM
    assert datapoint.value == value
    send.assert_awaited_once_with([5])
    add_dp(device, 5, TuyaBLEDataPointType.DT_ENUM, 1 - value)
    assert entity.current_option == entity.options[1 - value]
