"""Unit tests for the Tuya BLE switch entity."""
# pylint: disable=protected-access
# The tests deliberately set up device/coordinator internals as test setup
# state, which is sanctioned by the project's test conventions.

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntityDescription
from homeassistant.core import HomeAssistant

from custom_components.tuya_ble import switch
from custom_components.tuya_ble.devices import (
    TuyaBLECoordinator,
    TuyaBLEFingerbotInfo,
    TuyaBLEProductInfo,
    TuyaBLEWaterValveInfo,
)
from custom_components.tuya_ble.switch import TuyaBLESwitch
from custom_components.tuya_ble.tuya_ble import (
    TuyaBLEDataPointType,
    TuyaBLEDevice,
)
from tests.conftest import add_dp, build_context, connect, make_credentials


def _make_entity(
    hass: HomeAssistant,
    device: TuyaBLEDevice,
    coordinator: TuyaBLECoordinator,
    product: TuyaBLEProductInfo,
    **kwargs: Any,
) -> TuyaBLESwitch:
    """Build a switch entity from a mapping built from kwargs."""
    fields: dict[str, Any] = {
        "dp_id": 1,
        "description": SwitchEntityDescription(key="switch"),
    }
    fields.update(kwargs)
    mapping = switch.TuyaBLESwitchMapping(**fields)
    entity = switch.TuyaBLESwitch(hass, coordinator, device, product, mapping)
    entity.hass = hass
    return entity


async def test_is_on_bool_true(hass: HomeAssistant) -> None:
    """Verify is_on reports True for a True bool datapoint."""
    device, coordinator, product = build_context(hass)
    add_dp(device, 1, TuyaBLEDataPointType.DT_BOOL, True)
    entity = _make_entity(hass, device, coordinator, product)
    assert entity.is_on is True


async def test_is_on_bool_false(hass: HomeAssistant) -> None:
    """Verify is_on reports False for a False bool datapoint."""
    device, coordinator, product = build_context(hass)
    add_dp(device, 1, TuyaBLEDataPointType.DT_BOOL, False)
    entity = _make_entity(hass, device, coordinator, product)
    assert entity.is_on is False


async def test_is_on_no_datapoint(hass: HomeAssistant) -> None:
    """Verify is_on is False when the datapoint is missing."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    assert entity.is_on is False


async def test_is_on_bitmap(hass: HomeAssistant) -> None:
    """Verify is_on reads the bitmap mask bits."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        dp_id=7,
        bitmap_mask=b"\x01",
        description=SwitchEntityDescription(key="s"),
    )
    add_dp(device, 7, TuyaBLEDataPointType.DT_BITMAP, b"\x01")
    assert entity.is_on is True
    add_dp(device, 7, TuyaBLEDataPointType.DT_BITMAP, b"\x00")
    assert entity.is_on is False


async def test_is_on_getter(hass: HomeAssistant) -> None:
    """Verify a custom getter controls is_on."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        getter=lambda self_, product_: True,
    )
    assert entity.is_on is True


async def test_turn_on_bool(hass: HomeAssistant) -> None:
    """Verify turn_on sends True for a bool datapoint."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    entity.turn_on()
    await hass.async_block_till_done()
    dp = device.datapoints[1]
    assert dp is not None
    assert dp.value is True


async def test_turn_off_bool(hass: HomeAssistant) -> None:
    """Verify turn_off sends False for a bool datapoint."""
    device, coordinator, product = build_context(hass)
    add_dp(device, 1, TuyaBLEDataPointType.DT_BOOL, True)
    entity = _make_entity(hass, device, coordinator, product)
    entity.turn_off()
    await hass.async_block_till_done()
    dp = device.datapoints[1]
    assert dp is not None
    assert dp.value is False


async def test_turn_on_setter(hass: HomeAssistant) -> None:
    """Verify a custom setter receives the on/off values."""
    device, coordinator, product = build_context(hass)
    calls: list[bool] = []

    def setter(self_: TuyaBLESwitch, product_: TuyaBLEProductInfo, value: bool) -> None:
        calls.append(value)

    entity = _make_entity(hass, device, coordinator, product, setter=setter)
    entity.turn_on()
    entity.turn_off()
    assert calls == [True, False]


async def test_turn_on_bitmap(hass: HomeAssistant) -> None:
    """Verify turn_on applies the bitmap mask."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        dp_id=7,
        bitmap_mask=b"\x03",
        description=SwitchEntityDescription(key="s"),
    )
    add_dp(device, 7, TuyaBLEDataPointType.DT_BITMAP, b"\x01")
    entity.turn_on()
    await hass.async_block_till_done()
    dp = device.datapoints[7]
    assert dp is not None
    assert dp.value == b"\x03"


async def test_turn_off_bitmap(hass: HomeAssistant) -> None:
    """Verify turn_off clears the bitmask bits."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        dp_id=7,
        bitmap_mask=b"\x03",
        description=SwitchEntityDescription(key="s"),
    )
    add_dp(device, 7, TuyaBLEDataPointType.DT_BITMAP, b"\x03")
    entity.turn_off()
    await hass.async_block_till_done()
    dp = device.datapoints[7]
    assert dp is not None
    assert dp.value == b"\x00"


async def test_available(hass: HomeAssistant) -> None:
    """Verify availability follows the coordinator connection state."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    assert entity.available is False
    await connect(coordinator)
    assert entity.available is True


async def test_available_with_is_available(hass: HomeAssistant) -> None:
    """Verify the is_available callback is invoked when set."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        is_available=lambda self_, product_: True,
    )
    await connect(coordinator)
    assert entity.available is True


async def test_fingerbot_in_program_mode_no_fingerbot(
    hass: HomeAssistant,
) -> None:
    """Verify is_fingerbot_in_program_mode returns True when fingerbot is None."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        is_available=switch.is_fingerbot_in_program_mode,
    )
    await connect(coordinator)
    assert entity.available is True


async def test_fingerbot_in_switch_mode_no_fingerbot(
    hass: HomeAssistant,
) -> None:
    """Verify is_fingerbot_in_switch_mode returns True when fingerbot is None."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        is_available=switch.is_fingerbot_in_switch_mode,
    )
    await connect(coordinator)
    assert entity.available is True


async def test_fingerbot_in_switch_mode_no_datapoint(
    hass: HomeAssistant,
) -> None:
    """Verify is_fingerbot_in_switch_mode with fingerbot set but no mode dp."""

    device, coordinator, product = build_context(hass)
    product.fingerbot = TuyaBLEFingerbotInfo(
        switch=2,
        mode=8,
        up_position=5,
        down_position=6,
        hold_time=3,
        reverse_positions=0,
    )
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        is_available=switch.is_fingerbot_in_switch_mode,
    )
    await connect(coordinator)
    assert entity.available is True


async def test_set_fingerbot_program_repeat_forever_no_fingerbot(
    hass: HomeAssistant,
) -> None:
    """Verify set_fingerbot_program_repeat_forever exits early when no fingerbot."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        setter=switch.set_fingerbot_program_repeat_forever,
    )
    entity.turn_on()
    entity.turn_off()


async def test_is_fingerbot_in_program_mode_with_fingerbot(
    hass: HomeAssistant,
) -> None:
    """Verify is_fingerbot_in_program_mode checks the mode datapoint."""
    device, coordinator, product = build_context(hass)
    product.fingerbot = TuyaBLEFingerbotInfo(
        switch=2,
        mode=8,
        up_position=15,
        down_position=9,
        hold_time=10,
        reverse_positions=11,
        manual_control=17,
        program=121,
    )
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        is_available=switch.is_fingerbot_in_program_mode,
    )
    # No mode datapoint -> returns True (default)
    await connect(coordinator)
    assert entity.available is True
    # Add mode datapoint with value 2 (program mode)
    add_dp(device, 8, TuyaBLEDataPointType.DT_ENUM, 2)
    assert entity.available is True
    # Add mode datapoint with value 1 (switch mode)
    add_dp(device, 8, TuyaBLEDataPointType.DT_ENUM, 1)
    assert entity.available is False


async def test_is_fingerbot_in_switch_mode_with_mode_datapoint(
    hass: HomeAssistant,
) -> None:
    """Verify is_fingerbot_in_switch_mode checks the mode datapoint."""
    device, coordinator, product = build_context(hass)
    product.fingerbot = TuyaBLEFingerbotInfo(
        switch=2,
        mode=8,
        up_position=15,
        down_position=9,
        hold_time=10,
        reverse_positions=11,
    )
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        is_available=switch.is_fingerbot_in_switch_mode,
    )
    await connect(coordinator)
    # Add mode datapoint with value 1 (switch mode) -> returns True
    add_dp(device, 8, TuyaBLEDataPointType.DT_ENUM, 1)
    assert entity.available is True
    # Add mode datapoint with value 2 (program mode) -> returns False
    add_dp(device, 8, TuyaBLEDataPointType.DT_ENUM, 2)
    assert entity.available is False


async def test_get_fingerbot_program_repeat_forever(
    hass: HomeAssistant,
) -> None:
    """Verify get_fingerbot_program_repeat_forever reads the program datapoint."""
    device, coordinator, product = build_context(hass)
    product.fingerbot = TuyaBLEFingerbotInfo(
        switch=2,
        mode=8,
        up_position=15,
        down_position=9,
        hold_time=10,
        reverse_positions=11,
        program=121,
    )
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        getter=switch.get_fingerbot_program_repeat_forever,
    )
    # No program datapoint -> returns None
    assert entity.is_on is False
    # Add program datapoint with repeat count 0xFFFF (repeat forever)
    program_data = (0xFFFF).to_bytes(2, "big") + b"\x00\x00"
    add_dp(device, 121, TuyaBLEDataPointType.DT_RAW, program_data)
    assert entity.is_on is True
    # Add program datapoint with repeat count 1 (not repeat forever)
    program_data = (1).to_bytes(2, "big") + b"\x00\x00"
    add_dp(device, 121, TuyaBLEDataPointType.DT_RAW, program_data)
    assert entity.is_on is False


async def test_get_fingerbot_program_repeat_forever_program_zero(
    hass: HomeAssistant,
) -> None:
    """Verify get_fingerbot_program_repeat_forever returns None when program is 0."""
    device, coordinator, product = build_context(hass)
    product.fingerbot = TuyaBLEFingerbotInfo(
        switch=2,
        mode=8,
        up_position=15,
        down_position=9,
        hold_time=10,
        reverse_positions=11,
        program=0,
    )
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        getter=switch.get_fingerbot_program_repeat_forever,
    )
    # program=0 means the if condition is False, returns None
    assert entity.is_on is False


async def test_set_fingerbot_program_repeat_forever(
    hass: HomeAssistant,
) -> None:
    """Verify set_fingerbot_program_repeat_forever modifies the program datapoint."""
    device, coordinator, product = build_context(hass)
    product.fingerbot = TuyaBLEFingerbotInfo(
        switch=2,
        mode=8,
        up_position=15,
        down_position=9,
        hold_time=10,
        reverse_positions=11,
        program=121,
    )
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        setter=switch.set_fingerbot_program_repeat_forever,
    )
    # Add program datapoint
    program_data = (1).to_bytes(2, "big") + b"\x00\x01"
    add_dp(device, 121, TuyaBLEDataPointType.DT_RAW, program_data)
    # Set repeat forever -> should modify the first 2 bytes to 0xFFFF
    entity.turn_on()
    await hass.async_block_till_done()
    dp = device.datapoints[121]
    assert dp is not None
    assert dp.value == (0xFFFF).to_bytes(2, "big") + b"\x00\x01"
    # Set not repeat forever -> should modify the first 2 bytes to 1
    entity.turn_off()
    await hass.async_block_till_done()
    assert dp.value == (1).to_bytes(2, "big") + b"\x00\x01"


async def test_set_fingerbot_program_no_program_dp(
    hass: HomeAssistant,
) -> None:
    """Verify set_fingerbot_program_repeat_forever exits early when no program dp."""
    device, coordinator, product = build_context(hass)
    product.fingerbot = TuyaBLEFingerbotInfo(
        switch=2,
        mode=8,
        up_position=15,
        down_position=9,
        hold_time=10,
        reverse_positions=11,
        program=121,
    )
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        setter=switch.set_fingerbot_program_repeat_forever,
    )
    # No program datapoint -> should not raise
    entity.turn_on()
    entity.turn_off()


async def test_read_bitmap_on_non_bytes_value(
    hass: HomeAssistant,
) -> None:
    """Verify _read_bitmap_on returns False for non-bytes value."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        dp_id=7,
        bitmap_mask=b"\x01",
        description=SwitchEntityDescription(key="s"),
    )
    # Create a datapoint with a non-bytes value (e.g., int)
    dp = device.datapoints.get_or_create(7, TuyaBLEDataPointType.DT_BITMAP, b"\x01")
    dp._value = 42  # int, not bytes
    result = entity._read_bitmap_on(dp)
    assert result is False


async def test_read_bitmap_on_none_mask(
    hass: HomeAssistant,
) -> None:
    """Verify _read_bitmap_on returns False when bitmap_mask is None."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        dp_id=7,
        description=SwitchEntityDescription(key="s"),
    )
    # bitmap_mask is None by default
    dp = device.datapoints.get_or_create(7, TuyaBLEDataPointType.DT_BITMAP, b"\x01")
    result = entity._read_bitmap_on(dp)
    assert result is False


async def test_get_mapping_by_device_known_category(
    hass: HomeAssistant,
) -> None:
    """Verify get_mapping_by_device returns mappings for a known device."""
    device, _coordinator, _product = build_context(hass)
    device._device_info = make_credentials(category="wk", product_id="drlajpqc")
    mappings = switch.get_mapping_by_device(device)
    assert len(mappings) > 0
    assert mappings[0].dp_id in {8, 10, 40, 107, 108, 130}


async def test_get_mapping_by_device_unknown_category(
    hass: HomeAssistant,
) -> None:
    """Verify get_mapping_by_device returns empty list for unknown category."""
    device, _coordinator, _product = build_context(hass)
    device._device_info = make_credentials(category="unknown", product_id="unknown")
    mappings = switch.get_mapping_by_device(device)
    assert mappings == []


async def test_get_mapping_by_device_known_category_unknown_product(
    hass: HomeAssistant,
) -> None:
    """Verify get_mapping_by_device returns empty for unknown product."""
    device, _coordinator, _product = build_context(hass)
    device._device_info = make_credentials(category="wk", product_id="unknown")
    mappings = switch.get_mapping_by_device(device)
    assert mappings == []


async def test_get_mapping_by_device_fallback_to_category_mapping(
    hass: HomeAssistant,
) -> None:
    """Verify get_mapping_by_device returns category.mapping when product not found."""
    device, _coordinator, _product = build_context(hass)
    device._device_info = make_credentials(category="test_cat", product_id="unknown")
    # Temporarily patch the mapping dict to add a category with a mapping fallback
    fallback_mapping = [
        switch.TuyaBLESwitchMapping(
            dp_id=99,
            description=SwitchEntityDescription(key="fallback"),
        )
    ]
    switch.mapping["test_cat"] = switch.TuyaBLECategorySwitchMapping(
        products={"known_product": []},
        mapping=fallback_mapping,
    )
    try:
        mappings = switch.get_mapping_by_device(device)
        assert mappings == fallback_mapping
    finally:
        del switch.mapping["test_cat"]


async def test_write_bitmap_none_mask(
    hass: HomeAssistant,
) -> None:
    """Verify _write_bitmap returns early when bitmap_mask is None."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        dp_id=7,
        description=SwitchEntityDescription(key="s"),
    )
    # bitmap_mask is None -> should return without doing anything
    entity._write_bitmap(lambda v, m: v | m)
    # No datapoint should have been created
    assert device.datapoints[7] is None


# --- is_water_valve_in_switch_mode ---


async def test_is_water_valve_in_switch_mode_true(hass: HomeAssistant) -> None:
    """Verify is_water_valve_in_switch_mode returns True when watervalve is set."""
    device, coordinator, product = build_context(hass)
    product.watervalve = TuyaBLEWaterValveInfo(
        switch=1,
        countdown=2,
        weather_delay=3,
        smart_weather=4,
        use_time=5,
    )
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        is_available=switch.is_water_valve_in_switch_mode,
    )
    await connect(coordinator)
    assert entity.available is True


async def test_is_water_valve_in_switch_mode_false(hass: HomeAssistant) -> None:
    """Verify is_water_valve_in_switch_mode returns False when watervalve is None."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        is_available=switch.is_water_valve_in_switch_mode,
    )
    await connect(coordinator)
    assert entity.available is False


# --- set_16wgjvck_water_valve ---


async def test_set_16wgjvck_water_valve_on_default(hass: HomeAssistant) -> None:
    """Verify set_16wgjvck_water_valve on with no datapoints uses defaults."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(
        hass, device, coordinator, product, setter=switch.set_16wgjvck_water_valve
    )
    entity.turn_on()
    await hass.async_block_till_done()
    assert device.datapoints[1] is not None
    assert device.datapoints[1].value is True
    assert device.datapoints[2] is not None
    assert device.datapoints[2].value == 100
    assert device.datapoints[11] is not None
    assert device.datapoints[11].value == 60


async def test_set_16wgjvck_water_valve_on_with_dp15(
    hass: HomeAssistant,
) -> None:
    """Verify set_16wgjvck_water_valve on reads duration from dp15."""
    device, coordinator, product = build_context(hass)
    add_dp(device, 15, TuyaBLEDataPointType.DT_VALUE, 120)
    entity = _make_entity(
        hass, device, coordinator, product, setter=switch.set_16wgjvck_water_valve
    )
    entity.turn_on()
    await hass.async_block_till_done()
    assert device.datapoints[11] is not None
    assert device.datapoints[11].value == 120


async def test_set_16wgjvck_water_valve_on_with_dp11(
    hass: HomeAssistant,
) -> None:
    """Verify set_16wgjvck_water_valve reads duration from dp11 when dp15 is empty."""
    device, coordinator, product = build_context(hass)
    add_dp(device, 11, TuyaBLEDataPointType.DT_VALUE, 90)
    entity = _make_entity(
        hass, device, coordinator, product, setter=switch.set_16wgjvck_water_valve
    )
    entity.turn_on()
    await hass.async_block_till_done()
    assert device.datapoints[11] is not None
    assert device.datapoints[11].value == 90


async def test_set_16wgjvck_water_valve_on_dp11_zero(
    hass: HomeAssistant,
) -> None:
    """Verify set_16wgjvck_water_valve on uses default 60 when dp11 value is 0."""
    device, coordinator, product = build_context(hass)
    add_dp(device, 11, TuyaBLEDataPointType.DT_VALUE, 0)
    entity = _make_entity(
        hass, device, coordinator, product, setter=switch.set_16wgjvck_water_valve
    )
    entity.turn_on()
    await hass.async_block_till_done()
    assert device.datapoints[11] is not None
    assert device.datapoints[11].value == 60


async def test_set_16wgjvck_water_valve_on_dp15_zero(
    hass: HomeAssistant,
) -> None:
    """Verify set_16wgjvck_water_valve on uses default 60 when dp15 value is 0."""
    device, coordinator, product = build_context(hass)
    add_dp(device, 15, TuyaBLEDataPointType.DT_VALUE, 0)
    entity = _make_entity(
        hass, device, coordinator, product, setter=switch.set_16wgjvck_water_valve
    )
    entity.turn_on()
    await hass.async_block_till_done()
    assert device.datapoints[11] is not None
    assert device.datapoints[11].value == 60


async def test_set_16wgjvck_water_valve_on_dp15_negative(
    hass: HomeAssistant,
) -> None:
    """Verify set_16wgjvck_water_valve on uses default 60 when dp15 is negative."""
    device, coordinator, product = build_context(hass)
    add_dp(device, 15, TuyaBLEDataPointType.DT_VALUE, -5)
    entity = _make_entity(
        hass, device, coordinator, product, setter=switch.set_16wgjvck_water_valve
    )
    entity.turn_on()
    await hass.async_block_till_done()
    assert device.datapoints[11] is not None
    assert device.datapoints[11].value == 60


async def test_set_16wgjvck_water_valve_on_with_dp2(
    hass: HomeAssistant,
) -> None:
    """Verify set_16wgjvck_water_valve on reads percentage from dp2."""
    device, coordinator, product = build_context(hass)
    add_dp(device, 2, TuyaBLEDataPointType.DT_VALUE, 75)
    entity = _make_entity(
        hass, device, coordinator, product, setter=switch.set_16wgjvck_water_valve
    )
    entity.turn_on()
    await hass.async_block_till_done()
    assert device.datapoints[2] is not None
    assert device.datapoints[2].value == 75


async def test_set_16wgjvck_water_valve_on_dp2_zero(
    hass: HomeAssistant,
) -> None:
    """Verify set_16wgjvck_water_valve on uses default 100 when dp2 value is 0."""
    device, coordinator, product = build_context(hass)
    add_dp(device, 2, TuyaBLEDataPointType.DT_VALUE, 0)
    entity = _make_entity(
        hass, device, coordinator, product, setter=switch.set_16wgjvck_water_valve
    )
    entity.turn_on()
    await hass.async_block_till_done()
    assert device.datapoints[2] is not None
    assert device.datapoints[2].value == 100


async def test_set_16wgjvck_water_valve_on_dp2_negative(
    hass: HomeAssistant,
) -> None:
    """Verify set_16wgjvck_water_valve on uses default 100 when dp2 is negative."""
    device, coordinator, product = build_context(hass)
    add_dp(device, 2, TuyaBLEDataPointType.DT_VALUE, -10)
    entity = _make_entity(
        hass, device, coordinator, product, setter=switch.set_16wgjvck_water_valve
    )
    entity.turn_on()
    await hass.async_block_till_done()
    assert device.datapoints[2] is not None
    assert device.datapoints[2].value == 100


async def test_set_16wgjvck_water_valve_off(hass: HomeAssistant) -> None:
    """Verify set_16wgjvck_water_valve off sends False."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(
        hass, device, coordinator, product, setter=switch.set_16wgjvck_water_valve
    )
    entity.turn_off()
    await hass.async_block_till_done()
    assert device.datapoints[1] is not None
    assert device.datapoints[1].value is False
