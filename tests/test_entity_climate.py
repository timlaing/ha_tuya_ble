"""Unit tests for the Tuya BLE climate entity."""

# pylint: disable=protected-access
from __future__ import annotations

from homeassistant.components.climate import ClimateEntityDescription
from homeassistant.components.climate.const import (
    PRESET_AWAY,
    PRESET_NONE,
    HVACAction,
    HVACMode,
)
from homeassistant.core import HomeAssistant

from custom_components.tuya_ble import climate
from custom_components.tuya_ble.climate import TuyaBLEClimate
from custom_components.tuya_ble.devices import (
    TuyaBLECoordinator,
    TuyaBLEProductInfo,
)
from custom_components.tuya_ble.tuya_ble import (
    TuyaBLEDataPointType,
    TuyaBLEDevice,
)
from tests.conftest import add_dp, build_context


def _make_switch_entity(
    hass: HomeAssistant,
    device: TuyaBLEDevice,
    coordinator: TuyaBLECoordinator,
    product: TuyaBLEProductInfo,
) -> TuyaBLEClimate:
    """Build a switch-styled climate entity."""
    mapping = climate.TuyaBLEClimateMapping(
        description=ClimateEntityDescription(key="trv"),
        hvac_switch_dp_id=101,
        hvac_switch_mode=HVACMode.HEAT,
        target_temperature_dp_id=103,
        target_temperature_coefficient=10.0,
        current_temperature_dp_id=102,
        current_temperature_coefficient=10.0,
        target_humidity_dp_id=201,
        preset_mode_dp_ids={PRESET_AWAY: 106, PRESET_NONE: 106},
    )
    entity = climate.TuyaBLEClimate(hass, coordinator, device, product, mapping)
    entity.hass = hass
    return entity


def _make_mode_entity(
    hass: HomeAssistant,
    device: TuyaBLEDevice,
    coordinator: TuyaBLECoordinator,
    product: TuyaBLEProductInfo,
) -> TuyaBLEClimate:
    """Build a mode-based climate entity."""
    mapping = climate.TuyaBLEClimateMapping(
        description=ClimateEntityDescription(key="ac"),
        hvac_mode_dp_id=1,
        hvac_modes=[HVACMode.OFF, HVACMode.COOL],
        target_temperature_dp_id=2,
        target_humidity_dp_id=2,
    )
    entity = climate.TuyaBLEClimate(hass, coordinator, device, product, mapping)
    entity.hass = hass
    return entity


async def test_init_supported(hass: HomeAssistant) -> None:
    """Verify the switch-styled climate exposes off plus its mode."""
    device, coordinator, product = build_context(hass)
    entity = _make_switch_entity(hass, device, coordinator, product)
    assert entity.hvac_modes == [HVACMode.OFF, HVACMode.HEAT]


async def test_handle_update_switch(hass: HomeAssistant) -> None:
    """Verify coordinator updates drive the switch-styled climate."""
    device, coordinator, product = build_context(hass)
    entity = _make_switch_entity(hass, device, coordinator, product)
    await entity.async_added_to_hass()
    add_dp(device, 101, TuyaBLEDataPointType.DT_BOOL, True)
    add_dp(device, 102, TuyaBLEDataPointType.DT_VALUE, 210)
    add_dp(device, 103, TuyaBLEDataPointType.DT_VALUE, 200)
    coordinator.async_set_updated_data({})
    await hass.async_block_till_done()
    assert entity.hvac_mode == HVACMode.HEAT
    assert entity.current_temperature == 21.0
    assert entity.target_temperature == 20.0
    assert entity.hvac_action == HVACAction.IDLE


async def test_handle_update_preset_away(hass: HomeAssistant) -> None:
    """Verify the away preset is reported from its datapoint."""
    device, coordinator, product = build_context(hass)
    entity = _make_switch_entity(hass, device, coordinator, product)
    await entity.async_added_to_hass()
    add_dp(device, 106, TuyaBLEDataPointType.DT_BOOL, True)
    coordinator.async_set_updated_data({})
    await hass.async_block_till_done()
    assert entity.preset_mode == PRESET_AWAY


async def test_handle_update_mode_based(hass: HomeAssistant) -> None:
    """Verify coordinator updates drive the mode-based climate."""
    device, coordinator, product = build_context(hass)
    entity = _make_mode_entity(hass, device, coordinator, product)
    await entity.async_added_to_hass()
    add_dp(device, 1, TuyaBLEDataPointType.DT_VALUE, 1)
    coordinator.async_set_updated_data({})
    await hass.async_block_till_done()
    assert entity.hvac_mode == HVACMode.COOL


async def test_handle_update_mode_invalid(hass: HomeAssistant) -> None:
    """Verify an invalid mode value leaves hvac_mode as None."""
    device, coordinator, product = build_context(hass)
    entity = _make_mode_entity(hass, device, coordinator, product)
    await entity.async_added_to_hass()
    add_dp(device, 1, TuyaBLEDataPointType.DT_VALUE, 99)
    coordinator.async_set_updated_data({})
    await hass.async_block_till_done()
    assert entity.hvac_mode is None


async def test_set_temperature(hass: HomeAssistant) -> None:
    """Verify set_temperature writes the coefficient-adjusted value."""
    device, coordinator, product = build_context(hass)
    entity = _make_switch_entity(hass, device, coordinator, product)
    await entity.async_set_temperature(temperature=22.0)
    await hass.async_block_till_done()
    dp = device.datapoints[103]
    assert dp is not None
    assert dp.value == 220


async def test_set_humidity(hass: HomeAssistant) -> None:
    """Verify set_humidity writes the target humidity datapoint."""
    device, coordinator, product = build_context(hass)
    entity = _make_switch_entity(hass, device, coordinator, product)
    await entity.async_set_humidity(50)
    await hass.async_block_till_done()
    dp = device.datapoints[201]
    assert dp is not None
    assert dp.value == 50


async def test_async_set_hvac_mode_switch(hass: HomeAssistant) -> None:
    """Verify async_set_hvac_mode toggles the switch datapoint."""
    device, coordinator, product = build_context(hass)
    entity = _make_switch_entity(hass, device, coordinator, product)
    await entity.async_set_hvac_mode(HVACMode.HEAT)
    await hass.async_block_till_done()
    dp = device.datapoints[101]
    assert dp is not None
    assert dp.value is True
    await entity.async_set_hvac_mode(HVACMode.OFF)
    await hass.async_block_till_done()
    dp = device.datapoints[101]
    assert dp is not None
    assert dp.value is False


async def test_async_set_hvac_mode_modebased(hass: HomeAssistant) -> None:
    """Verify async_set_hvac_mode writes the mode index."""
    device, coordinator, product = build_context(hass)
    entity = _make_mode_entity(hass, device, coordinator, product)
    await entity.async_set_hvac_mode(HVACMode.COOL)
    await hass.async_block_till_done()
    dp = device.datapoints[2]
    assert dp is not None
    assert dp.value == 1


async def test_async_set_preset_mode_away(hass: HomeAssistant) -> None:
    """Verify async_set_preset_mode enables the away preset."""
    device, coordinator, product = build_context(hass)
    entity = _make_switch_entity(hass, device, coordinator, product)
    await entity.async_set_preset_mode(PRESET_AWAY)
    await hass.async_block_till_done()
    dp = device.datapoints[106]
    assert dp is not None
    assert dp.value is True


async def test_async_set_preset_mode_none(hass: HomeAssistant) -> None:
    """Verify async_set_preset_mode with none disables the preset."""
    device, coordinator, product = build_context(hass)
    entity = _make_switch_entity(hass, device, coordinator, product)
    await entity.async_set_preset_mode(PRESET_NONE)
    await hass.async_block_till_done()
    dp = device.datapoints[106]
    assert dp is not None
    assert dp.value is False


async def test_noop_setters(hass: HomeAssistant) -> None:
    """Verify the sync no-op setters do not raise."""
    device, coordinator, product = build_context(hass)
    entity = _make_switch_entity(hass, device, coordinator, product)
    entity.set_temperature(temperature=1)
    entity.set_humidity(1)
    entity.set_hvac_mode(HVACMode.HEAT)
    entity.set_preset_mode(PRESET_NONE)
    entity.set_fan_mode("auto")
    entity.set_swing_mode("both")
    entity.set_swing_horizontal_mode("both")
    entity.turn_on()
    entity.turn_off()
    entity.toggle()


async def test_init_no_hvac_mode_or_switch(hass: HomeAssistant) -> None:
    """Verify climate init with both hvac_mode and hvac_switch dp_id=0."""
    device, coordinator, product = build_context(hass)
    mapping = climate.TuyaBLEClimateMapping(
        description=ClimateEntityDescription(key="bare"),
    )
    entity = climate.TuyaBLEClimate(hass, coordinator, device, product, mapping)
    entity.hass = hass
    assert not hasattr(entity, "_attr_hvac_modes") or entity._attr_hvac_modes is None


async def test_init_no_temperature(hass: HomeAssistant) -> None:
    """Verify climate init with target_temperature_dp_id=0."""
    device, coordinator, product = build_context(hass)
    mapping = climate.TuyaBLEClimateMapping(
        description=ClimateEntityDescription(key="no_temp"),
        hvac_mode_dp_id=1,
        hvac_modes=[HVACMode.OFF, HVACMode.COOL],
    )
    entity = climate.TuyaBLEClimate(hass, coordinator, device, product, mapping)
    entity.hass = hass
    assert entity.target_temperature is None


async def test_init_no_humidity(hass: HomeAssistant) -> None:
    """Verify climate init with target_humidity_dp_id=0."""
    device, coordinator, product = build_context(hass)
    mapping = climate.TuyaBLEClimateMapping(
        description=ClimateEntityDescription(key="no_hum"),
        hvac_mode_dp_id=1,
        hvac_modes=[HVACMode.OFF, HVACMode.COOL],
        target_temperature_dp_id=2,
    )
    entity = climate.TuyaBLEClimate(hass, coordinator, device, product, mapping)
    entity.hass = hass
    assert entity.target_humidity is None


async def test_async_set_temperature_no_dp(hass: HomeAssistant) -> None:
    """Verify set_temperature is a no-op when target_temperature_dp_id=0."""
    device, coordinator, product = build_context(hass)
    mapping = climate.TuyaBLEClimateMapping(
        description=ClimateEntityDescription(key="no_temp"),
        hvac_mode_dp_id=1,
        hvac_modes=[HVACMode.OFF, HVACMode.COOL],
    )
    entity = climate.TuyaBLEClimate(hass, coordinator, device, product, mapping)
    entity.hass = hass
    await entity.async_set_temperature(temperature=22.0)


async def test_async_set_humidity_no_dp(hass: HomeAssistant) -> None:
    """Verify set_humidity is a no-op when target_humidity_dp_id=0."""
    device, coordinator, product = build_context(hass)
    mapping = climate.TuyaBLEClimateMapping(
        description=ClimateEntityDescription(key="no_hum"),
        hvac_mode_dp_id=1,
        hvac_modes=[HVACMode.OFF, HVACMode.COOL],
    )
    entity = climate.TuyaBLEClimate(hass, coordinator, device, product, mapping)
    entity.hass = hass
    await entity.async_set_humidity(50)


async def test_async_set_hvac_mode_no_dp(hass: HomeAssistant) -> None:
    """Verify set_hvac_mode is a no-op when both dp_ids are 0."""
    device, coordinator, product = build_context(hass)
    mapping = climate.TuyaBLEClimateMapping(
        description=ClimateEntityDescription(key="bare"),
    )
    entity = climate.TuyaBLEClimate(hass, coordinator, device, product, mapping)
    entity.hass = hass
    await entity.async_set_hvac_mode(HVACMode.HEAT)


async def test_async_set_preset_mode_no_dp_ids(hass: HomeAssistant) -> None:
    """Verify set_preset_mode is a no-op when preset_mode_dp_ids is None."""
    device, coordinator, product = build_context(hass)
    mapping = climate.TuyaBLEClimateMapping(
        description=ClimateEntityDescription(key="no_preset"),
        hvac_mode_dp_id=1,
        hvac_modes=[HVACMode.OFF, HVACMode.COOL],
    )
    entity = climate.TuyaBLEClimate(hass, coordinator, device, product, mapping)
    entity.hass = hass
    await entity.async_set_preset_mode(PRESET_AWAY)


async def test_async_set_preset_mode_multi_dp(hass: HomeAssistant) -> None:
    """Verify set_preset_mode with different DP IDs per preset."""
    device, coordinator, product = build_context(hass)
    mapping = climate.TuyaBLEClimateMapping(
        description=ClimateEntityDescription(key="multi"),
        hvac_mode_dp_id=1,
        hvac_modes=[HVACMode.OFF, HVACMode.COOL],
        preset_mode_dp_ids={PRESET_AWAY: 106, PRESET_NONE: 107},
    )
    entity = climate.TuyaBLEClimate(hass, coordinator, device, product, mapping)
    entity.hass = hass
    await entity.async_set_preset_mode(PRESET_AWAY)
    await hass.async_block_till_done()
    dp = device.datapoints[106]
    assert dp is not None
    assert dp.value is True


async def test_mode_from_value_no_datapoint(hass: HomeAssistant) -> None:
    """Verify mode-based climate handles absent mode datapoint."""
    device, coordinator, product = build_context(hass)
    entity = _make_mode_entity(hass, device, coordinator, product)
    await entity.async_added_to_hass()
    coordinator.async_set_updated_data({})
    await hass.async_block_till_done()
    assert entity.hvac_mode is None
