"""Regression tests for the Simart Akilli Parmak descriptor (issue #148)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.components.sensor import SensorDeviceClass
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


def _context(
    hass: HomeAssistant,
) -> tuple[TuyaBLEDevice, TuyaBLECoordinator, TuyaBLEProductInfo]:
    """Use the real shipped descriptor with a real device and coordinator."""
    device = make_device()
    device.initialize_with_credentials(
        make_credentials(category="kg", product_id="gnpbj0bq")
    )
    return device, TuyaBLECoordinator(hass, device), get_device_product_info(device)


def test_simart_descriptor_omits_reverse_positions_and_program_controls() -> None:
    """The ENUM DP104 and absent DP109 must not inherit Plus controls."""
    descriptor = get_registry().get("kg", "gnpbj0bq")
    assert descriptor is not None
    assert descriptor.device_name == "Akilli Parmak"
    assert descriptor.manufacturer == "Simart"
    assert descriptor.model_name == "gnpbj0bq"
    assert get_mapped_dp_ids("kg", "gnpbj0bq") == {1, 101, 102, 103, 105, 106, 107, 108}
    assert not descriptor.get("text")
    assert [desc.translation_key for desc in descriptor.get("switch")] == [
        "switch",
        "manual_control",
    ]
    assert [desc.dp_id for desc in descriptor.get("number")] == [102, 103, 106]
    assert descriptor.get("select")[0].options == ["push", "switch"]


async def test_simart_battery_reports_dp105(hass: HomeAssistant) -> None:
    """Battery percentage comes from DP105, unlike the Touch's DP115."""
    device, coordinator, product = _context(hass)
    mappings = sensor.get_mapping_by_device(device)
    assert len(mappings) == 1
    assert mappings[0].dp_id == 105
    entity = sensor.TuyaBLESensor(hass, coordinator, device, product, mappings[0])
    assert entity.device_class == SensorDeviceClass.BATTERY
    assert entity.native_unit_of_measurement == "%"
    with patch.object(entity, "async_write_ha_state"):
        await entity.async_added_to_hass()
        add_dp(device, 105, TuyaBLEDataPointType.DT_VALUE, 85)
        coordinator.async_set_updated_data({})
    assert entity.native_value == 85


@pytest.mark.parametrize("mode", [0, 1])
async def test_simart_controls_follow_push_and_switch_modes(
    hass: HomeAssistant, mode: int
) -> None:
    """Shared Fingerbot handlers gate the mapped controls using DP101."""
    device, coordinator, product = _context(hass)
    controls = [
        button.TuyaBLEButton(hass, coordinator, device, product, mapping)
        for mapping in button.get_mapping_by_device(device)
    ]
    numbers = [
        number.TuyaBLENumber(hass, coordinator, device, product, mapping)
        for mapping in number.get_mapping_by_device(device)
    ]
    switches = [
        switch.TuyaBLESwitch(hass, coordinator, device, product, mapping)
        for mapping in switch.get_mapping_by_device(device)
    ]
    assert not text.get_mapping_by_device(device)
    add_dp(device, 101, TuyaBLEDataPointType.DT_ENUM, mode)
    with patch.object(device, "update", new_callable=AsyncMock):
        await connect(coordinator)
        assert [entity.available for entity in controls] == [mode == 0]
        assert [entity.available for entity in numbers] == [True, mode == 0, True]
        assert [entity.available for entity in switches] == [mode == 1, True]
        await coordinator.async_shutdown()


@pytest.mark.parametrize("mode", [0, 1])
async def test_simart_mode_select_writes_dp101_enum(
    hass: HomeAssistant, mode: int
) -> None:
    """Offer only Push and Switch, without an unsupported Program action."""
    device, coordinator, product = _context(hass)
    mappings = select.get_mapping_by_device(device)
    assert len(mappings) == 1
    entity = select.TuyaBLESelect(hass, coordinator, device, product, mappings[0])
    entity.hass = hass
    assert entity.options == ["push", "switch"]
    with patch.object(device, "send_datapoints", new_callable=AsyncMock) as send:
        entity.select_option(entity.options[mode])
        await hass.async_block_till_done()
        send.assert_awaited_once_with([101])
        entity.select_option("program")
        await hass.async_block_till_done()
        assert send.await_count == 1
    datapoint = device.datapoints[101]
    assert datapoint is not None
    assert datapoint.dp_type == TuyaBLEDataPointType.DT_ENUM
    assert datapoint.value == mode
    assert entity.current_option == entity.options[mode]


@pytest.mark.parametrize("dp_id,value", [(102, 85), (103, 3), (106, 15)])
async def test_simart_position_and_hold_time_writes(
    hass: HomeAssistant, dp_id: int, value: int
) -> None:
    """Position and hold-time settings use the reported numeric datapoints."""
    device, coordinator, product = _context(hass)
    mapping = next(
        mapping
        for mapping in number.get_mapping_by_device(device)
        if mapping.dp_id == dp_id
    )
    entity = number.TuyaBLENumber(hass, coordinator, device, product, mapping)
    entity.hass = hass
    with patch.object(device, "send_datapoints", new_callable=AsyncMock) as send:
        entity.set_native_value(value)
        await hass.async_block_till_done()
    datapoint = device.datapoints[dp_id]
    assert datapoint is not None
    assert datapoint.dp_type == TuyaBLEDataPointType.DT_VALUE
    assert datapoint.value == value
    assert entity.native_value == value
    send.assert_awaited_once_with([dp_id])


async def test_simart_push_and_manual_control_use_bool_datapoints(
    hass: HomeAssistant,
) -> None:
    """Push and manual control write DP108/107; neither touches ENUM DP104."""
    device, coordinator, product = _context(hass)
    push = button.TuyaBLEButton(
        hass, coordinator, device, product, button.get_mapping_by_device(device)[0]
    )
    manual = switch.TuyaBLESwitch(
        hass, coordinator, device, product, switch.get_mapping_by_device(device)[1]
    )
    push.hass = manual.hass = hass
    with patch.object(device, "send_datapoints", new_callable=AsyncMock) as send:
        await push.async_press()
        manual.turn_on()
        await hass.async_block_till_done()
    for dp_id in (108, 107):
        datapoint = device.datapoints[dp_id]
        assert datapoint is not None
        assert datapoint.dp_type == TuyaBLEDataPointType.DT_BOOL
        assert datapoint.value is True
    assert [call.args[0] for call in send.await_args_list] == [[108], [107]]
    assert device.datapoints[104] is None
    assert device.datapoints[109] is None
