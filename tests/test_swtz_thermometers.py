"""Regression tests for the swtz cooking thermometer descriptors (issue #168)."""

# pylint: disable=protected-access

from __future__ import annotations

import asyncio
from struct import pack
from unittest.mock import patch

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
import pytest

from custom_components.tuya_ble import (
    binary_sensor,
    button,
    number,
    select,
    sensor,
    switch,
)
from custom_components.tuya_ble.coordinator import TuyaBLECoordinator
from custom_components.tuya_ble.device_registry import get_mapped_dp_ids, get_registry
from custom_components.tuya_ble.entity import get_device_product_info
from custom_components.tuya_ble.tuya_ble import TuyaBLEDataPointType, TuyaBLEDevice
from custom_components.tuya_ble.tuya_ble.const import TuyaBLECode
from tests.conftest import add_dp, make_credentials, make_device
from tests.protocol_harness import ProtocolHarness, encrypt_payload, frame_packet0

_PROBES = {
    "iv13iqhf": {2: "probe_1_temperature", 3: "probe_2_temperature"},
    "o3nnpkat": {
        2: "probe_1_temperature",
        3: "probe_2_temperature",
        4: "probe_3_temperature",
        5: "probe_4_temperature",
        28: "probe_5_temperature",
        29: "probe_6_temperature",
    },
}


def _device(product_id: str) -> TuyaBLEDevice:
    device = make_device()
    device.initialize_with_credentials(
        make_credentials(category="swtz", product_id=product_id)
    )
    return device


@pytest.mark.parametrize(
    ("product_id", "name"),
    [
        ("iv13iqhf", "GRILLMEISTER Bluetooth Cooking Thermometer"),
        ("o3nnpkat", "BTH08 Smart Meat Thermometer"),
    ],
)
def test_descriptor_maps_only_battery_and_probes(product_id: str, name: str) -> None:
    """Unconfirmed probe-presence, unit, target and alarm DPs stay unmapped."""
    descriptor = get_registry().get("swtz", product_id)
    assert descriptor is not None
    assert descriptor.device_name == name
    assert get_mapped_dp_ids("swtz", product_id) == {1, *_PROBES[product_id]}
    assert "swtz" not in get_registry().category_defaults
    device = _device(product_id)
    for platform in (binary_sensor, button, number, select, switch):
        assert not platform.get_mapping_by_device(device)


@pytest.mark.parametrize("product_id", sorted(_PROBES))
def test_probe_sensor_mappings(product_id: str) -> None:
    """Each probe is a 0.1 degC temperature measurement on its own DP."""
    mappings = sensor.get_mapping_by_device(_device(product_id))
    battery, *probes = mappings
    assert battery.dp_id == 1
    assert battery.description.device_class == SensorDeviceClass.BATTERY
    assert battery.description.entity_category is EntityCategory.DIAGNOSTIC
    assert {m.dp_id: m.description.key for m in probes} == _PROBES[product_id]
    for mapping in probes:
        assert mapping.coefficient == pytest.approx(10.0)
        assert mapping.description.device_class == SensorDeviceClass.TEMPERATURE
        assert mapping.description.state_class == SensorStateClass.MEASUREMENT
        assert (
            mapping.description.native_unit_of_measurement == UnitOfTemperature.CELSIUS
        )


@pytest.mark.parametrize("product_id", sorted(_PROBES))
@pytest.mark.parametrize(("raw", "expected"), [(50, 5.0), (-250, -25.0), (0, 0.0)])
async def test_probe_readings_are_signed_tenths(
    hass: HomeAssistant, product_id: str, raw: int, expected: float
) -> None:
    """Reported values divide by ten, including freezer (negative) readings."""
    device = _device(product_id)
    coordinator = TuyaBLECoordinator(hass, device)
    product = get_device_product_info(device)
    entities = [
        sensor.TuyaBLESensor(hass, coordinator, device, product, mapping)
        for mapping in sensor.get_mapping_by_device(device)
    ]
    with patch.object(sensor.TuyaBLESensor, "async_write_ha_state"):
        for entity in entities:
            await entity.async_added_to_hass()
        add_dp(device, 1, TuyaBLEDataPointType.DT_VALUE, 90)
        for dp_id in _PROBES[product_id]:
            add_dp(device, dp_id, TuyaBLEDataPointType.DT_VALUE, raw)
        coordinator.async_set_updated_data({})
    battery, *probes = entities
    assert battery.native_value == 90
    assert [probe.native_value for probe in probes] == pytest.approx(
        [expected] * len(probes)
    )


async def test_grillmeister_decodes_negative_probe_from_the_wire(
    hass: HomeAssistant,
) -> None:
    """A 4-byte two's-complement report (-250) becomes -25.0 degC on probe 2."""
    harness = ProtocolHarness()
    await harness.register_notify()
    device = harness.device
    # Test setup state: identify the harness device as the GRILLMEISTER.
    device._device_info = make_credentials(category="swtz", product_id="iv13iqhf")
    coordinator = TuyaBLECoordinator(hass, device)
    product = get_device_product_info(device)
    probe_2 = sensor.TuyaBLESensor(
        hass, coordinator, device, product, sensor.get_mapping_by_device(device)[2]
    )
    value = TuyaBLEDataPointType.DT_VALUE.value
    data = pack(">BBBi", 2, value, 4, 50) + pack(">BBBi", 3, value, 4, -250)
    assert device._session_key is not None
    with patch.object(sensor.TuyaBLESensor, "async_write_ha_state"):
        await probe_2.async_added_to_hass()
        harness.notify(
            frame_packet0(
                encrypt_payload(
                    device._session_key, 5, 1, 0, TuyaBLECode.FUN_RECEIVE_DP, data
                )
            )
        )
        await asyncio.gather(*tuple(device._send_response_tasks))
        coordinator.async_set_updated_data({})
    assert probe_2.entity_description.key == "probe_2_temperature"
    assert probe_2.native_value == pytest.approx(-25.0)
