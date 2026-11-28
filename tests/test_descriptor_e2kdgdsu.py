"""Hardware-tested controls of the AQUA CREATIV TITAN PRO 300 (``dj/e2kdgdsu``)."""

from __future__ import annotations

from homeassistant.components.number import NumberMode
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
import pytest

from custom_components.tuya_ble import light, number, select, switch
from custom_components.tuya_ble.coordinator import TuyaBLECoordinator
from custom_components.tuya_ble.device_registry import get_mapped_dp_ids, get_registry
from custom_components.tuya_ble.entity import TuyaBLEProductInfo
from custom_components.tuya_ble.tuya_ble import TuyaBLEDataPointType, TuyaBLEDevice
from tests.conftest import add_dp, build_context, make_credentials

CHANNEL_DP_IDS = [105, 106, 107, 108]
LIGHT_MODES = ["Manual", "Auto", "Simple setup"]
EFFECT_DEMOS = ["None", "Auto", "Simple setup", "Moon", "Thunderstorm", "Cloudy"]


def _context(
    hass: HomeAssistant,
) -> tuple[TuyaBLEDevice, TuyaBLECoordinator, TuyaBLEProductInfo]:
    """Build a device that the registry resolves to the aquarium light."""
    device, coordinator, product = build_context(hass)
    device._device_info = make_credentials(category="dj", product_id="e2kdgdsu")
    return device, coordinator, product


def test_registry_maps_controls_and_keeps_the_diagnostics() -> None:
    """Every controlled data point is mapped next to the #210 diagnostic reports."""
    assert get_mapped_dp_ids("dj", "e2kdgdsu") == {
        3,
        101,
        102,
        103,
        104,
        105,
        106,
        107,
        108,
    }
    descriptor = get_registry().get("dj", "e2kdgdsu")
    assert descriptor is not None
    assert descriptor.manufacturer == "AQUA CREATIV"
    assert [item.dp_id for item in descriptor.get("binary_sensor")] == [101]
    assert [item.dp_id for item in descriptor.get("sensor")] == [102]


async def test_light_maps_power_and_total_brightness(hass: HomeAssistant) -> None:
    """The light uses DP101 for power and DP3 (0-1000) for total brightness only."""
    device, _coordinator, _product = _context(hass)
    mappings = light.get_mapping_by_device(device)
    assert len(mappings) == 1
    mapping = mappings[0]
    assert mapping.switch_dp_id == 101
    assert mapping.brightness_dp_id == 3
    assert (mapping.brightness_min, mapping.brightness_max) == (0, 1000)
    assert mapping.color_data_dp_id == 0
    assert mapping.color_temp_dp_id == 0
    assert mapping.color_mode_dp_id == 0


async def test_channels_are_sliders_that_write_value_data_points(
    hass: HomeAssistant,
) -> None:
    """Channels A-D are 0-100 % sliders written as VALUE data points."""
    device, coordinator, product = _context(hass)
    mappings = number.get_mapping_by_device(device)
    assert [item.dp_id for item in mappings] == CHANNEL_DP_IDS
    for item in mappings:
        description = item.description
        assert description.native_min_value == 0
        assert description.native_max_value == 100
        assert description.native_step == 1
        assert description.native_unit_of_measurement == "%"
        assert item.mode is NumberMode.SLIDER
        entity = number.TuyaBLENumber(hass, coordinator, device, product, item)
        entity.hass = hass
        await entity.async_set_native_value(42)
        await hass.async_block_till_done()
        datapoint = device.datapoints[item.dp_id]
        assert datapoint is not None
        assert datapoint.dp_type is TuyaBLEDataPointType.DT_VALUE
        assert datapoint.value == 42


@pytest.mark.parametrize(
    ("dp_id", "options", "option", "index"),
    [
        (102, LIGHT_MODES, "Simple setup", 2),
        (103, EFFECT_DEMOS, "Thunderstorm", 4),
    ],
)
async def test_selects_write_the_enum_index(
    hass: HomeAssistant, dp_id: int, options: list[str], option: str, index: int
) -> None:
    """Light mode and effect demo are ENUM data points indexed by option order."""
    device, coordinator, product = _context(hass)
    mapping = next(
        item for item in select.get_mapping_by_device(device) if item.dp_id == dp_id
    )
    assert mapping.dp_type is TuyaBLEDataPointType.DT_ENUM
    assert mapping.description.options == options
    entity = select.TuyaBLESelect(hass, coordinator, device, product, mapping)
    entity.hass = hass

    add_dp(device, dp_id, TuyaBLEDataPointType.DT_ENUM, 0)
    assert entity.current_option == options[0]

    entity.select_option(option)
    await hass.async_block_till_done()
    datapoint = device.datapoints[dp_id]
    assert datapoint is not None
    assert datapoint.dp_type is TuyaBLEDataPointType.DT_ENUM
    assert datapoint.value == index


async def test_hour_system_switch_is_a_config_boolean(hass: HomeAssistant) -> None:
    """The 24-hour clock is a configuration switch on DP104."""
    device, coordinator, product = _context(hass)
    mappings = switch.get_mapping_by_device(device)
    assert [item.dp_id for item in mappings] == [104]
    assert mappings[0].description.entity_category is EntityCategory.CONFIG
    entity = switch.TuyaBLESwitch(hass, coordinator, device, product, mappings[0])
    entity.hass = hass
    assert entity.is_on is False

    await entity.async_turn_on()
    await hass.async_block_till_done()
    datapoint = device.datapoints[104]
    assert datapoint is not None
    assert datapoint.dp_type is TuyaBLEDataPointType.DT_BOOL
    assert datapoint.value is True
    assert entity.is_on is True
