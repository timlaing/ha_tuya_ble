"""Regression tests for the CBF02V2 Finger Robot descriptor (issue #168)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.components.number import NumberMode
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
import pytest

from custom_components.tuya_ble import button, number, select, sensor, switch, text
from custom_components.tuya_ble.coordinator import TuyaBLECoordinator
from custom_components.tuya_ble.device_registry import get_mapped_dp_ids, get_registry
from custom_components.tuya_ble.entity import (
    TuyaBLEProductInfo,
    get_device_product_info,
)
from custom_components.tuya_ble.tuya_ble import TuyaBLEDataPointType, TuyaBLEDevice
from tests.conftest import add_dp, connect, make_credentials, make_device

_CATEGORY = "szjqr"
_PRODUCT = "bo25vaxy"


def _context(
    hass: HomeAssistant,
) -> tuple[TuyaBLEDevice, TuyaBLECoordinator, TuyaBLEProductInfo]:
    """Use the shipped descriptor with a real device and coordinator."""
    device = make_device()
    device.initialize_with_credentials(
        make_credentials(category=_CATEGORY, product_id=_PRODUCT)
    )
    return device, TuyaBLECoordinator(hass, device), get_device_product_info(device)


def _number(
    hass: HomeAssistant,
    device: TuyaBLEDevice,
    coordinator: TuyaBLECoordinator,
    product: TuyaBLEProductInfo,
    dp_id: int,
) -> number.TuyaBLENumber:
    mapping = next(m for m in number.get_mapping_by_device(device) if m.dp_id == dp_id)
    entity = number.TuyaBLENumber(hass, coordinator, device, product, mapping)
    entity.hass = hass
    return entity


def _switch(
    hass: HomeAssistant,
    device: TuyaBLEDevice,
    coordinator: TuyaBLECoordinator,
    product: TuyaBLEProductInfo,
) -> switch.TuyaBLESwitch:
    (mapping,) = switch.get_mapping_by_device(device)
    entity = switch.TuyaBLESwitch(hass, coordinator, device, product, mapping)
    entity.hass = hass
    return entity


def test_descriptor_is_a_two_mode_profile_without_plus_controls() -> None:
    """No program, reverse-position, charge-status or auto-adjust entities."""
    descriptor = get_registry().get(_CATEGORY, _PRODUCT)
    assert descriptor is not None
    assert descriptor.device_name == "Finger Robot CBF02V2"
    assert get_mapped_dp_ids(_CATEGORY, _PRODUCT) == {1, 2, 3, 4, 5, 8, 102}
    assert not descriptor.get("text")
    assert [d.translation_key for d in descriptor.get("switch")] == ["switch"]
    assert descriptor.get("select")[0].options == ["push", "switch"]
    info = TuyaBLEProductInfo.from_descriptor(descriptor)
    assert info.fingerbot_mode_dp_id == 2
    assert info.fingerbot_switch_dp_id == 1
    assert info.fingerbot_program_dp_id is None
    assert info.fingerbot_manual_control_dp_id is None


async def test_platform_mappings_and_mode_gating(hass: HomeAssistant) -> None:
    """Push and hold time follow Click mode; the switch works in both modes."""
    device, coordinator, product = _context(hass)
    assert not text.get_mapping_by_device(device)
    buttons = [
        button.TuyaBLEButton(hass, coordinator, device, product, mapping)
        for mapping in button.get_mapping_by_device(device)
    ]
    numbers = [
        number.TuyaBLENumber(hass, coordinator, device, product, mapping)
        for mapping in number.get_mapping_by_device(device)
    ]
    actuator = _switch(hass, device, coordinator, product)
    assert [b.entity_description.key for b in buttons] == [
        "push",
        "invert_switch_status",
    ]
    assert buttons[1].entity_description.entity_category is EntityCategory.CONFIG
    assert [n.mode for n in numbers] == [NumberMode.BOX] * 3
    with patch.object(device, "update", new_callable=AsyncMock):
        await connect(coordinator)
        for mode in (0, 1):
            add_dp(device, 2, TuyaBLEDataPointType.DT_ENUM, mode)
            assert [b.available for b in buttons] == [mode == 0, True]
            assert [n.available for n in numbers] == [mode == 0, True, True]
            assert actuator.available is True
        await coordinator.async_shutdown()


@pytest.mark.parametrize("mode", [0, 1])
async def test_mode_select_writes_enum_index(hass: HomeAssistant, mode: int) -> None:
    """Click is enum 0 and Switch (cloud long_press) is enum 1; no Program."""
    device, coordinator, product = _context(hass)
    (mapping,) = select.get_mapping_by_device(device)
    entity = select.TuyaBLESelect(hass, coordinator, device, product, mapping)
    entity.hass = hass
    with patch.object(device, "send_datapoints", new_callable=AsyncMock) as send:
        entity.select_option(entity.options[mode])
        await hass.async_block_till_done()
        entity.select_option("program")
        await hass.async_block_till_done()
    send.assert_awaited_once_with([2])
    datapoint = device.datapoints[2]
    assert datapoint is not None
    assert datapoint.dp_type == TuyaBLEDataPointType.DT_ENUM
    assert datapoint.value == mode
    assert entity.current_option == entity.options[mode]


@pytest.mark.parametrize(("raw", "expected"), [(False, True), (True, False)])
async def test_switch_reads_inverted(
    hass: HomeAssistant, raw: bool, expected: bool
) -> None:
    """The reported DP1 polarity is inverted, and no report reads as off."""
    device, coordinator, product = _context(hass)
    entity = _switch(hass, device, coordinator, product)
    assert entity.is_on is False
    add_dp(device, 1, TuyaBLEDataPointType.DT_BOOL, raw)
    assert entity.is_on is expected


@pytest.mark.parametrize(("turn_on", "raw"), [(True, False), (False, True)])
async def test_switch_writes_inverted(
    hass: HomeAssistant, turn_on: bool, raw: bool
) -> None:
    """Turning on writes false and turning off writes true on DP1."""
    device, coordinator, product = _context(hass)
    entity = _switch(hass, device, coordinator, product)
    with patch.object(device, "send_datapoints", new_callable=AsyncMock) as send:
        if turn_on:
            entity.turn_on()
        else:
            entity.turn_off()
        await hass.async_block_till_done()
    send.assert_awaited_once_with([1])
    datapoint = device.datapoints[1]
    assert datapoint is not None
    assert datapoint.dp_type == TuyaBLEDataPointType.DT_BOOL
    assert datapoint.value is raw
    assert entity.is_on is turn_on


async def test_invert_status_button_always_writes_true(hass: HomeAssistant) -> None:
    """DP102 is a write-only trigger, so every press sends true, not a toggle."""
    device, coordinator, product = _context(hass)
    mapping = next(m for m in button.get_mapping_by_device(device) if m.dp_id == 102)
    entity = button.TuyaBLEButton(hass, coordinator, device, product, mapping)
    entity.hass = hass
    with patch.object(device, "send_datapoints", new_callable=AsyncMock) as send:
        await entity.async_press()
        await entity.async_press()
    assert [call.args[0] for call in send.await_args_list] == [[102], [102]]
    datapoint = device.datapoints[102]
    assert datapoint is not None
    assert datapoint.dp_type == TuyaBLEDataPointType.DT_BOOL
    assert datapoint.value is True
    assert device.datapoints[1] is None


async def test_push_button_toggles_dp1(hass: HomeAssistant) -> None:
    """The Click-mode push still toggles DP1 rather than writing DP102."""
    device, coordinator, product = _context(hass)
    mapping = next(m for m in button.get_mapping_by_device(device) if m.dp_id == 1)
    entity = button.TuyaBLEButton(hass, coordinator, device, product, mapping)
    entity.hass = hass
    add_dp(device, 1, TuyaBLEDataPointType.DT_BOOL, True)
    with patch.object(device, "send_datapoints", new_callable=AsyncMock) as send:
        await entity.async_press()
    send.assert_awaited_once_with([1])
    datapoint = device.datapoints[1]
    assert datapoint is not None
    assert datapoint.value is False


@pytest.mark.parametrize(("seconds", "raw"), [(0.3, 3), (0.5, 5), (10.0, 100)])
async def test_hold_time_scales_tenths_of_a_second(
    hass: HomeAssistant, seconds: float, raw: int
) -> None:
    """DP3 is 3-100 in 0.1 s units, shown as 0.3-10 s."""
    device, coordinator, product = _context(hass)
    entity = _number(hass, device, coordinator, product, 3)
    assert (entity.native_min_value, entity.native_max_value) == pytest.approx((
        0.3,
        10,
    ))
    assert entity.native_step == pytest.approx(0.1)
    assert entity.native_unit_of_measurement == "s"
    with patch.object(device, "send_datapoints", new_callable=AsyncMock) as send:
        entity.set_native_value(seconds)
        await hass.async_block_till_done()
    send.assert_awaited_once_with([3])
    datapoint = device.datapoints[3]
    assert datapoint is not None
    assert datapoint.dp_type == TuyaBLEDataPointType.DT_VALUE
    assert datapoint.value == raw
    assert entity.native_value == pytest.approx(seconds)


@pytest.mark.parametrize("dp_id", [4, 5])
async def test_positions_use_the_0_to_30_schema_range(
    hass: HomeAssistant, dp_id: int
) -> None:
    """Up/down positions are unitless 0-30 values written unscaled."""
    device, coordinator, product = _context(hass)
    entity = _number(hass, device, coordinator, product, dp_id)
    # Home Assistant rejects service calls outside the advertised bounds.
    assert (entity.min_value, entity.max_value, entity.step) == (0, 30, 1)
    assert entity.native_unit_of_measurement is None
    assert entity.native_value == 0
    add_dp(device, dp_id, TuyaBLEDataPointType.DT_VALUE, 23)
    assert entity.native_value == 23
    with patch.object(device, "send_datapoints", new_callable=AsyncMock) as send:
        entity.set_native_value(30)
        await hass.async_block_till_done()
    send.assert_awaited_once_with([dp_id])
    datapoint = device.datapoints[dp_id]
    assert datapoint is not None
    assert datapoint.value == 30


async def test_battery_reads_dp8(hass: HomeAssistant) -> None:
    """Battery percentage is DP8; DP7 charge status is not mapped."""
    device, coordinator, product = _context(hass)
    (mapping,) = sensor.get_mapping_by_device(device)
    assert mapping.dp_id == 8
    entity = sensor.TuyaBLESensor(hass, coordinator, device, product, mapping)
    assert entity.device_class == SensorDeviceClass.BATTERY
    with patch.object(entity, "async_write_ha_state"):
        await entity.async_added_to_hass()
        add_dp(device, 8, TuyaBLEDataPointType.DT_VALUE, 98)
        coordinator.async_set_updated_data({})
    assert entity.native_value == 98
