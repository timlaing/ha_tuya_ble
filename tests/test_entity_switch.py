"""Unit tests for the Tuya BLE switch entity."""
# pylint: disable=protected-access
# The tests deliberately set up device/coordinator internals as test setup
# state, which is sanctioned by the project's test conventions.

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.components.switch import SwitchEntityDescription
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tuya_ble import switch
from custom_components.tuya_ble.const import DOMAIN
from custom_components.tuya_ble.device_descriptors.handlers.fingerbot.mode import (
    in_program_mode as is_fingerbot_in_program_mode,
)
from custom_components.tuya_ble.device_descriptors.handlers.fingerbot.mode import (
    in_switch_mode as is_fingerbot_in_switch_mode,
)
from custom_components.tuya_ble.device_descriptors.handlers.fingerbot.program import (
    get_repeat_forever as get_fingerbot_program_repeat_forever,
)
from custom_components.tuya_ble.device_descriptors.handlers.fingerbot.program import (
    set_repeat_forever as set_fingerbot_program_repeat_forever,
)
from custom_components.tuya_ble.device_descriptors.handlers.water_valve import (
    is_water_valve_in_switch_mode,
    set_16wgjvck_water_valve,
)
from custom_components.tuya_ble.devices import (
    TuyaBLECoordinator,
    TuyaBLEData,
    TuyaBLEProductInfo,
)
from custom_components.tuya_ble.switch import TuyaBLESwitch
from custom_components.tuya_ble.tuya_ble import (
    TuyaBLEDataPointType,
    TuyaBLEDevice,
)
from tests.conftest import (
    add_dp,
    build_context,
    connect,
    make_credentials,
    make_product_info,
)


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


@pytest.fixture(name="touch_switches")
async def fixture_touch_switches(
    hass: HomeAssistant,
) -> list[switch.TuyaBLEPulseSwitch]:
    """Build both channel switches through the normal platform setup path."""
    device, coordinator, product = build_context(hass)
    device._device_info = make_credentials(category="kg", product_id="bs3ubslo")
    entry = MockConfigEntry(domain=DOMAIN)
    hass.data[DOMAIN] = {
        entry.entry_id: TuyaBLEData("Touch", device, product, Mock(), coordinator)
    }
    add_entities = Mock()
    await switch.async_setup_entry(hass, entry, add_entities)
    channels = [
        entity
        for entity in add_entities.call_args.args[0]
        if isinstance(entity, switch.TuyaBLEPulseSwitch)
    ]
    assert len(channels) == 2
    for channel in channels:
        channel.hass = hass
    await connect(coordinator)
    return channels


@pytest.mark.parametrize(("channel", "duration_ms"), [(1, 300), (2, 2000)])
async def test_touch_push_sends_once_then_resets_only_ha(
    touch_switches: list[switch.TuyaBLEPulseSwitch], channel: int, duration_ms: int
) -> None:
    """A press changes one datapoint once, including when a request is repeated."""
    entity = touch_switches[channel - 1]
    device = entity.device
    add_dp(device, 100 + channel, TuyaBLEDataPointType.DT_ENUM, 0)
    add_dp(device, 102 + channel, TuyaBLEDataPointType.DT_VALUE, duration_ms)
    add_dp(device, channel, TuyaBLEDataPointType.DT_BOOL, True)
    datapoint = device.datapoints[channel]
    assert datapoint is not None
    assert entity.available
    assert not entity.is_on
    with (
        patch.object(device, "send_datapoints", new_callable=AsyncMock) as send,
        patch.object(entity, "async_write_ha_state") as write_state,
        patch.object(switch, "async_call_later") as schedule,
    ):
        await entity.async_turn_on()
        assert entity.is_on
        assert not touch_switches[2 - channel].is_on
        entity._handle_coordinator_update()
        assert entity.is_on
        assert datapoint.value is False
        send.assert_awaited_once_with([channel])
        schedule.assert_called_once_with(
            entity.hass, duration_ms / 1000, entity._reset_pulse
        )
        await entity.async_turn_on()
        await entity.async_turn_off()
        assert entity.is_on
        schedule.call_args.args[2](None)
        assert not entity.is_on
        assert datapoint.value is False
        send.assert_awaited_once_with([channel])
        assert write_state.call_count == 3

        await entity.async_turn_on()
        assert datapoint.value is True
        assert send.await_count == 2
        await entity.async_will_remove_from_hass()
        schedule.return_value.assert_called_once()


async def test_touch_switch_uses_reported_state_and_blocks_program(
    touch_switches: list[switch.TuyaBLEPulseSwitch],
) -> None:
    """Switch mode writes physical ON and OFF; Program gates both actions."""
    entity = touch_switches[0]
    device = entity.device
    add_dp(device, 101, TuyaBLEDataPointType.DT_ENUM, 1)
    with patch.object(device, "send_datapoints", new_callable=AsyncMock) as send:
        await entity.async_turn_on()
        assert entity.is_on
        await entity.async_turn_off()
        assert not entity.is_on
        assert send.await_count == 2

        add_dp(device, 101, TuyaBLEDataPointType.DT_ENUM, 2)
        assert not entity.available
        await entity.async_turn_on()
        await entity.async_turn_off()
        assert send.await_count == 2


async def test_touch_push_retry_after_failed_send(
    hass: HomeAssistant, touch_switches: list[switch.TuyaBLEPulseSwitch]
) -> None:
    """A failed or pending write cannot start another press or leave HA on."""
    entity = touch_switches[0]
    device = entity.device
    add_dp(device, 101, TuyaBLEDataPointType.DT_ENUM, 0)
    started = asyncio.Event()
    finish = asyncio.Event()

    async def fail_send(_datapoints: list[int]) -> None:
        started.set()
        await finish.wait()
        raise ValueError("write failed")

    with (
        patch.object(device, "send_datapoints", side_effect=fail_send) as send,
        patch.object(entity, "async_write_ha_state"),
        patch.object(switch, "async_call_later") as schedule,
    ):
        first = hass.async_create_task(entity.async_turn_on())
        await started.wait()
        await entity.async_turn_on()
        finish.set()
        with pytest.raises(ValueError, match="write failed"):
            await first
        send.assert_awaited_once()
        assert not entity.is_on
        schedule.assert_not_called()

        send.side_effect = None
        await entity.async_turn_on()
        assert entity.is_on
        schedule.assert_called_once_with(hass, 0.1, entity._reset_pulse)

        add_dp(device, 101, TuyaBLEDataPointType.DT_ENUM, 1)
        entity._handle_coordinator_update()
        schedule.return_value.assert_called_once()
        assert entity.is_on  # Switch mode reads the last reported DP value.


@pytest.mark.parametrize("change_mode", [False, True])
async def test_touch_push_does_not_start_timer_after_unload_or_mode_change(
    hass: HomeAssistant,
    touch_switches: list[switch.TuyaBLEPulseSwitch],
    change_mode: bool,
) -> None:
    """A press finishing after a mode change or unload cannot start a timer."""
    entity = touch_switches[0]
    device = entity.device
    add_dp(device, 101, TuyaBLEDataPointType.DT_ENUM, 0)
    started = asyncio.Event()
    finish = asyncio.Event()

    async def send_press(_datapoints: list[int]) -> None:
        started.set()
        await finish.wait()

    with (
        patch.object(device, "send_datapoints", side_effect=send_press),
        patch.object(entity, "async_write_ha_state"),
        patch.object(switch, "async_call_later") as schedule,
    ):
        task = hass.async_create_task(entity.async_turn_on())
        await started.wait()
        if change_mode:
            add_dp(device, 101, TuyaBLEDataPointType.DT_ENUM, 1)
            entity._handle_coordinator_update()
        else:
            await entity.async_will_remove_from_hass()
        finish.set()
        await task
        schedule.assert_not_called()


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
        is_available=is_fingerbot_in_program_mode,
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
        is_available=is_fingerbot_in_switch_mode,
    )
    await connect(coordinator)
    assert entity.available is True


async def test_fingerbot_in_switch_mode_no_datapoint(
    hass: HomeAssistant,
) -> None:
    """Verify is_fingerbot_in_switch_mode with fingerbot set but no mode dp."""

    device, coordinator, product = build_context(hass)
    product = make_product_info(mode=8, switch=2)
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        is_available=is_fingerbot_in_switch_mode,
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
        setter=set_fingerbot_program_repeat_forever,
    )
    entity.turn_on()
    entity.turn_off()


async def test_is_fingerbot_in_program_mode_with_fingerbot(
    hass: HomeAssistant,
) -> None:
    """Verify is_fingerbot_in_program_mode checks the mode datapoint."""
    device, coordinator, product = build_context(hass)
    product = make_product_info(mode=8, switch=2, manual_control=17, program=121)
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        is_available=is_fingerbot_in_program_mode,
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
    product = make_product_info(mode=8, switch=2)
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        is_available=is_fingerbot_in_switch_mode,
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
    product = make_product_info(mode=8, switch=2, program=121)
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        getter=get_fingerbot_program_repeat_forever,
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
    product = make_product_info(mode=8, switch=2)
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        getter=get_fingerbot_program_repeat_forever,
    )
    # program=0 means the if condition is False, returns None
    assert entity.is_on is False


async def test_set_fingerbot_program_repeat_forever(
    hass: HomeAssistant,
) -> None:
    """Verify set_fingerbot_program_repeat_forever modifies the program datapoint."""
    device, coordinator, product = build_context(hass)
    product = make_product_info(mode=8, switch=2, program=121)
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        setter=set_fingerbot_program_repeat_forever,
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
    product = make_product_info(mode=8, switch=2, program=121)
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        setter=set_fingerbot_program_repeat_forever,
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
    product = make_product_info(water_valve=1)
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        is_available=is_water_valve_in_switch_mode,
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
        is_available=is_water_valve_in_switch_mode,
    )
    await connect(coordinator)
    assert entity.available is False


# --- set_16wgjvck_water_valve ---


async def test_set_16wgjvck_water_valve_on_default(hass: HomeAssistant) -> None:
    """Verify set_16wgjvck_water_valve on with no datapoints uses defaults."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(
        hass, device, coordinator, product, setter=set_16wgjvck_water_valve
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
        hass, device, coordinator, product, setter=set_16wgjvck_water_valve
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
        hass, device, coordinator, product, setter=set_16wgjvck_water_valve
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
        hass, device, coordinator, product, setter=set_16wgjvck_water_valve
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
        hass, device, coordinator, product, setter=set_16wgjvck_water_valve
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
        hass, device, coordinator, product, setter=set_16wgjvck_water_valve
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
        hass, device, coordinator, product, setter=set_16wgjvck_water_valve
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
        hass, device, coordinator, product, setter=set_16wgjvck_water_valve
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
        hass, device, coordinator, product, setter=set_16wgjvck_water_valve
    )
    entity.turn_on()
    await hass.async_block_till_done()
    assert device.datapoints[2] is not None
    assert device.datapoints[2].value == 100


async def test_set_16wgjvck_water_valve_off(hass: HomeAssistant) -> None:
    """Verify set_16wgjvck_water_valve off sends False."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(
        hass, device, coordinator, product, setter=set_16wgjvck_water_valve
    )
    entity.turn_off()
    await hass.async_block_till_done()
    assert device.datapoints[1] is not None
    assert device.datapoints[1].value is False


async def test_bitmap_length_mismatch_warns(hass: HomeAssistant, caplog: Any) -> None:
    """A payload wider than the mask pairs the shared prefix and warns."""
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
    add_dp(device, 7, TuyaBLEDataPointType.DT_BITMAP, b"\x01\x01")

    with caplog.at_level("WARNING"):
        assert entity.is_on is True

    assert "Bitmap length mismatch" in caplog.text


async def test_turn_on_bitmap_preserves_untouched_bytes(hass: HomeAssistant) -> None:
    """A payload wider than the mask keeps the bytes outside the mask intact."""
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
    # Second byte belongs to a sibling switch sharing this datapoint.
    add_dp(device, 7, TuyaBLEDataPointType.DT_BITMAP, b"\x00\x01")

    entity.turn_on()
    await hass.async_block_till_done()

    dp = device.datapoints[7]
    assert dp is not None
    assert dp.value == b"\x01\x01"


async def test_turn_off_bitmap_preserves_untouched_bytes(hass: HomeAssistant) -> None:
    """Turning off clears only the masked bits, leaving sibling bytes alone."""
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
    add_dp(device, 7, TuyaBLEDataPointType.DT_BITMAP, b"\x01\x01")

    entity.turn_off()
    await hass.async_block_till_done()

    dp = device.datapoints[7]
    assert dp is not None
    assert dp.value == b"\x00\x01"
