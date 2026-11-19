"""Regression tests for the MaXpeedingRods diesel heater descriptor (issue #169)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.components.climate.const import (
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
import pytest

from custom_components.tuya_ble import climate, select, sensor
from custom_components.tuya_ble.coordinator import TuyaBLECoordinator
from custom_components.tuya_ble.device_descriptors.handlers import measurement
from custom_components.tuya_ble.device_registry import get_registry
from custom_components.tuya_ble.entity import (
    TuyaBLEProductInfo,
    get_device_product_info,
)
from custom_components.tuya_ble.tuya_ble import TuyaBLEDataPointType, TuyaBLEDevice
from tests.conftest import add_dp, make_credentials, make_device

BOOL = TuyaBLEDataPointType.DT_BOOL
VALUE = TuyaBLEDataPointType.DT_VALUE
ENUM = TuyaBLEDataPointType.DT_ENUM


def _context(
    hass: HomeAssistant,
) -> tuple[TuyaBLEDevice, TuyaBLECoordinator, TuyaBLEProductInfo]:
    """Build a real device and coordinator using the shipped descriptor."""
    device = make_device()
    device.initialize_with_credentials(
        make_credentials(category="qn", product_id="vmdizama")
    )
    return device, TuyaBLECoordinator(hass, device), get_device_product_info(device)


def _climate(hass: HomeAssistant) -> tuple[TuyaBLEDevice, climate.TuyaBLEClimate]:
    """Build the heater climate entity from the shipped descriptor."""
    device, coordinator, product = _context(hass)
    [mapping] = climate.get_mapping_by_device(device)
    entity = climate.TuyaBLEClimate(hass, coordinator, device, product, mapping)
    entity.hass = hass
    return device, entity


def test_heater_descriptor_metadata() -> None:
    """The product resolves with its manufacturer and device name."""
    descriptor = get_registry().get("qn", "vmdizama")
    assert descriptor is not None
    assert descriptor.manufacturer == "MaXpeedingRods"
    assert descriptor.device_name == "Diesel Heater"
    assert descriptor.model_name == "vmdizama"


async def test_heater_climate_features(hass: HomeAssistant) -> None:
    """On/off and a 0-40 C whole-degree set point, with no presets."""
    _device, entity = _climate(hass)
    assert entity.unique_id is not None
    assert entity.unique_id.endswith("-parking_heater")
    assert entity.hvac_modes == [HVACMode.OFF, HVACMode.HEAT]
    assert entity.supported_features & ClimateEntityFeature.TARGET_TEMPERATURE
    assert not entity.supported_features & ClimateEntityFeature.PRESET_MODE
    assert entity.temperature_unit == UnitOfTemperature.CELSIUS
    assert (entity.min_temp, entity.max_temp) == (0.0, 40.0)
    assert entity.target_temperature_step == 1.0


async def test_heater_climate_reads_switch_and_set_point(hass: HomeAssistant) -> None:
    """DP 3 never becomes the room temperature; the action follows DP 1."""
    device, entity = _climate(hass)
    with patch.object(entity, "async_write_ha_state"):
        await entity.async_added_to_hass()
        add_dp(device, 1, BOOL, True)
        add_dp(device, 2, VALUE, 19)
        add_dp(device, 3, VALUE, 225)
        entity.coordinator.async_set_updated_data({})
        running = (entity.hvac_mode, entity.hvac_action)
        assert entity.target_temperature == 19.0
        assert entity.current_temperature is None
        add_dp(device, 1, BOOL, False)
        entity.coordinator.async_set_updated_data({})
        stopped = (entity.hvac_mode, entity.hvac_action)
    assert running == (HVACMode.HEAT, HVACAction.HEATING)
    assert stopped == (HVACMode.OFF, HVACAction.OFF)


@pytest.mark.parametrize(
    ("hvac_mode", "expected"), [(HVACMode.HEAT, True), (HVACMode.OFF, False)]
)
async def test_heater_climate_switch_write(
    hass: HomeAssistant, hvac_mode: HVACMode, expected: bool
) -> None:
    """HVAC mode writes the DP 1 boolean."""
    device, entity = _climate(hass)
    with patch.object(device, "send_datapoints", new_callable=AsyncMock) as send:
        await entity.async_set_hvac_mode(hvac_mode)
        await hass.async_block_till_done()
    datapoint = device.datapoints[1]
    assert datapoint is not None
    assert datapoint.dp_type == BOOL
    assert datapoint.value is expected
    send.assert_awaited_once_with([1])


async def test_heater_climate_set_point_write_is_unscaled(hass: HomeAssistant) -> None:
    """A 10 C set point is sent as raw DP 2 VALUE 10 (scale 0)."""
    device, entity = _climate(hass)
    with patch.object(device, "send_datapoints", new_callable=AsyncMock) as send:
        await entity.async_set_temperature(temperature=10.0)
        await hass.async_block_till_done()
    datapoint = device.datapoints[2]
    assert datapoint is not None
    assert datapoint.dp_type == VALUE
    assert datapoint.value == 10
    send.assert_awaited_once_with([2])


async def test_heater_exchanger_and_work_state_sensors(hass: HomeAssistant) -> None:
    """DP 3 is a gated, throttled temperature; DP 11 an enum diagnostic."""
    device, coordinator, product = _context(hass)
    exchanger, work_state = [
        sensor.TuyaBLESensor(hass, coordinator, device, product, mapping)
        for mapping in sensor.get_mapping_by_device(device)
    ]
    assert exchanger.device_class == SensorDeviceClass.TEMPERATURE
    assert exchanger.native_unit_of_measurement == UnitOfTemperature.CELSIUS
    assert exchanger.state_class == SensorStateClass.MEASUREMENT
    assert exchanger.unique_id is not None
    assert exchanger.unique_id.endswith("-heat_exchanger_temperature")
    assert work_state.device_class == SensorDeviceClass.ENUM
    assert work_state.entity_category == EntityCategory.DIAGNOSTIC
    assert work_state.options == ["heating"]
    with (
        patch.object(exchanger, "async_write_ha_state"),
        patch.object(work_state, "async_write_ha_state"),
        patch.object(measurement, "monotonic", return_value=500.0),
    ):
        await exchanger.async_added_to_hass()
        await work_state.async_added_to_hass()
        add_dp(device, 1, BOOL, True)
        add_dp(device, 3, VALUE, 9)
        add_dp(device, 11, ENUM, 0)
        coordinator.async_set_updated_data({})
        assert exchanger.native_value == 9.0
        assert work_state.native_value == "heating"
        add_dp(device, 3, VALUE, 12)  # +3 C within 30 s: held back
        coordinator.async_set_updated_data({})
        assert exchanger.native_value == 9.0
        add_dp(device, 3, VALUE, 160)
        coordinator.async_set_updated_data({})
        assert exchanger.native_value == 160.0
        add_dp(device, 1, BOOL, False)  # switched off: no stale 160 C
        coordinator.async_set_updated_data({})
        assert exchanger.native_value is None
        add_dp(device, 11, ENUM, 3)  # undeclared work state code
        coordinator.async_set_updated_data({})
        assert work_state.native_value is None


async def test_heater_mode_select_writes_enum(hass: HomeAssistant) -> None:
    """DP 4 reports and writes its ENUM index."""
    device, coordinator, product = _context(hass)
    mode_mapping = select.get_mapping_by_device(device)[0]
    entity = select.TuyaBLESelect(hass, coordinator, device, product, mode_mapping)
    entity.hass = hass
    assert entity.options == ["smart", "auto"]
    add_dp(device, 4, ENUM, 1)
    assert entity.current_option == "auto"
    with patch.object(device, "send_datapoints", new_callable=AsyncMock) as send:
        await entity.async_select_option("smart")
        await hass.async_block_till_done()
    datapoint = device.datapoints[4]
    assert datapoint is not None
    assert datapoint.dp_type == ENUM
    assert datapoint.value == 0
    send.assert_awaited_once_with([4])


@pytest.mark.parametrize(("option", "index"), [("1", 0), ("2", 1), ("3", 2)])
async def test_heater_level_settable_values(
    hass: HomeAssistant, option: str, index: int
) -> None:
    """Levels 1-3 are written as their DP 5 ENUM index."""
    device, coordinator, product = _context(hass)
    level_mapping = select.get_mapping_by_device(device)[1]
    entity = select.TuyaBLESelect(hass, coordinator, device, product, level_mapping)
    entity.hass = hass
    with patch.object(device, "send_datapoints", new_callable=AsyncMock) as send:
        await entity.async_select_option(option)
        await hass.async_block_till_done()
    datapoint = device.datapoints[5]
    assert datapoint is not None
    assert datapoint.dp_type == ENUM
    assert datapoint.value == index
    send.assert_awaited_once_with([5])


@pytest.mark.parametrize("option", ["4", "5"])
async def test_heater_level_read_only_values(hass: HomeAssistant, option: str) -> None:
    """Levels 4 and 5 are shown when reported but never sent."""
    device, coordinator, product = _context(hass)
    level_mapping = select.get_mapping_by_device(device)[1]
    entity = select.TuyaBLESelect(hass, coordinator, device, product, level_mapping)
    entity.hass = hass
    add_dp(device, 5, ENUM, int(option) - 1)
    assert entity.current_option == option
    with (
        patch.object(device, "send_datapoints", new_callable=AsyncMock) as send,
        pytest.raises(ServiceValidationError),
    ):
        await entity.async_select_option(option)
    await hass.async_block_till_done()
    send.assert_not_awaited()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build a JSON object, failing on a key that appears twice."""
    keys = [key for key, _value in pairs]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    assert not duplicates, duplicates
    return dict(pairs)


@pytest.mark.parametrize("name", ["strings.json", "translations/en.json"])
def test_translations_keep_every_exception_message(name: str) -> None:
    """No duplicate key hides a message; both exception messages survive."""
    path = Path(select.__file__).parent / name
    strings = json.loads(
        path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys
    )
    assert {"invalid_fan_speed", "option_not_settable"} <= set(strings["exceptions"])
    assert "{settable}" in strings["exceptions"]["option_not_settable"]["message"]


async def test_heater_held_reading_shown_on_other_dp_update(
    hass: HomeAssistant,
) -> None:
    """A throttled DP 3 reading is shown by a later report of any data point."""
    device, coordinator, product = _context(hass)
    exchanger = sensor.TuyaBLESensor(
        hass, coordinator, device, product, sensor.get_mapping_by_device(device)[0]
    )
    clock = {"now": 100.0}
    with (
        patch.object(exchanger, "async_write_ha_state"),
        patch.object(measurement, "monotonic", lambda: clock["now"]),
    ):
        await exchanger.async_added_to_hass()
        add_dp(device, 1, BOOL, True)
        add_dp(device, 3, VALUE, 200)
        coordinator.async_set_updated_data({})
        clock["now"] += 1
        add_dp(device, 3, VALUE, 202)  # held back: +2 C after 1 s
        coordinator.async_set_updated_data({})
        held = exchanger.native_value
        clock["now"] += 60  # time alone changes nothing
        idle = exchanger.native_value
        add_dp(device, 11, ENUM, 0)  # an unrelated report re-checks DP 3
        coordinator.async_set_updated_data({})
    assert (held, idle, exchanger.native_value) == (200.0, 200.0, 202.0)
