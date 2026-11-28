"""Descriptors for the RainPoint TCS024B soil sensor and Goldair blanket.

Covers the RainPoint TCS024B soil moisture sensor (``wsdcg`` / ``j5mynqs3``)
and the Goldair SmartSleep multi-zone electric blanket (``dr`` /
``mqcffjumdpuahxmi``). BLE I/O is stubbed; datapoints arrive through the
public ``update_from_device`` path.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
import pytest

from custom_components.tuya_ble import climate, number, select, sensor, switch
from custom_components.tuya_ble.coordinator import TuyaBLECoordinator
from custom_components.tuya_ble.device_registry import get_mapped_dp_ids, get_registry
from custom_components.tuya_ble.entity import (
    TuyaBLEProductInfo,
    get_device_product_info,
)
from custom_components.tuya_ble.tuya_ble import TuyaBLEDataPointType, TuyaBLEDevice
from tests.conftest import add_dp, make_credentials, make_device

SOIL = ("wsdcg", "j5mynqs3")
BLANKET = ("dr", "mqcffjumdpuahxmi")
LEVELS = [str(level) for level in range(7)]
TIMERS = [f"{hours}h" for hours in range(1, 13)]


def _context(
    hass: HomeAssistant, category: str, product_id: str
) -> tuple[TuyaBLEDevice, TuyaBLECoordinator, TuyaBLEProductInfo]:
    """Build a real device and coordinator using the shipped descriptor."""
    device = make_device()
    device.initialize_with_credentials(
        make_credentials(category=category, product_id=product_id)
    )
    return device, TuyaBLECoordinator(hass, device), get_device_product_info(device)


def _sensors(
    hass: HomeAssistant, category: str, product_id: str
) -> tuple[TuyaBLEDevice, TuyaBLECoordinator, list[sensor.TuyaBLESensor]]:
    """Build every descriptor sensor for a product (RSSI excluded)."""
    device, coordinator, product = _context(hass, category, product_id)
    entities = [
        sensor.TuyaBLESensor(hass, coordinator, device, product, mapping)
        for mapping in sensor.get_mapping_by_device(device)
    ]
    return device, coordinator, entities


async def _push(
    coordinator: TuyaBLECoordinator,
    entities: list[sensor.TuyaBLESensor],
    device: TuyaBLEDevice,
    updates: list[tuple[int, TuyaBLEDataPointType, int]],
) -> None:
    """Deliver datapoint updates and let each sensor re-read them."""
    with patch.object(sensor.TuyaBLESensor, "async_write_ha_state"):
        for entity in entities:
            await entity.async_added_to_hass()
        for dp_id, dp_type, value in updates:
            add_dp(device, dp_id, dp_type, value)
        coordinator.async_set_updated_data({})


# --- Registry ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("category", "product_id", "device_name", "manufacturer", "dp_ids"),
    [
        (*SOIL, "TCS024B Soil Moisture Sensor", "RainPoint", {1, 2, 4}),
        (
            *BLANKET,
            "Multi-zone Electric Blanket",
            "Goldair",
            {1, 14, 15, 16, 17, 18, 19},
        ),
    ],
)
def test_descriptor_metadata_and_mapped_dps(
    category: str,
    product_id: str,
    device_name: str,
    manufacturer: str,
    dp_ids: set[int],
) -> None:
    """Each product resolves with its own name and only the evidenced DPs."""
    descriptor = get_registry().get(category, product_id)
    assert descriptor is not None
    assert descriptor.device_name == device_name
    assert descriptor.model_name == product_id
    assert descriptor.manufacturer == manufacturer
    assert get_mapped_dp_ids(category, product_id) == dp_ids


def test_soil_sensor_has_sensors_only() -> None:
    """The TCS024B exposes readings only and no settings."""
    device = make_device()
    device.initialize_with_credentials(
        make_credentials(category=SOIL[0], product_id=SOIL[1])
    )
    for platform in (climate, number, select, switch):
        assert platform.get_mapping_by_device(device) == []


def test_blanket_platforms() -> None:
    """The blanket maps one switch, four selects and two sensors."""
    device = make_device()
    device.initialize_with_credentials(
        make_credentials(category=BLANKET[0], product_id=BLANKET[1])
    )
    assert [m.dp_id for m in switch.get_mapping_by_device(device)] == [1]
    assert [m.dp_id for m in select.get_mapping_by_device(device)] == [14, 15, 16, 17]
    assert [m.dp_id for m in sensor.get_mapping_by_device(device)] == [18, 19]
    assert climate.get_mapping_by_device(device) == []
    assert number.get_mapping_by_device(device) == []


def test_blanket_zone_keys() -> None:
    """DPs 14/16/18 are the body zone and DPs 15/17/19 the feet zone."""
    descriptor = get_registry().get(*BLANKET)
    assert descriptor is not None
    keys = {
        d.dp_id: d.translation_key
        for platform in ("select", "sensor")
        for d in descriptor.get(platform)
    }
    assert keys == {
        14: "body_level",
        15: "feet_level",
        16: "body_timer",
        17: "feet_timer",
        18: "body_time_left",
        19: "feet_time_left",
    }


# --- Soil sensor readings ---------------------------------------------------


@pytest.mark.parametrize(("raw", "expected"), [(14, 14), (0, 0), (35, 35)])
async def test_soil_readings_are_unscaled(
    hass: HomeAssistant, raw: int, expected: int
) -> None:
    """DP 1 is whole degrees; DP 2 and DP 4 are whole percentages."""
    device, coordinator, entities = _sensors(hass, *SOIL)
    temperature, moisture, battery = entities
    assert temperature.device_class == SensorDeviceClass.TEMPERATURE
    assert temperature.state_class == SensorStateClass.MEASUREMENT
    assert moisture.device_class == SensorDeviceClass.MOISTURE
    assert battery.device_class == SensorDeviceClass.BATTERY
    assert battery.entity_category == EntityCategory.DIAGNOSTIC
    await _push(
        coordinator,
        entities,
        device,
        [
            (1, TuyaBLEDataPointType.DT_VALUE, raw),
            (2, TuyaBLEDataPointType.DT_VALUE, 47),
            (4, TuyaBLEDataPointType.DT_VALUE, 80),
        ],
    )
    assert temperature.native_value == expected
    assert temperature.native_unit_of_measurement == UnitOfTemperature.CELSIUS
    assert moisture.native_value == 47
    assert moisture.native_unit_of_measurement == PERCENTAGE
    assert battery.native_value == 80


# --- Blanket ----------------------------------------------------------------


async def test_blanket_time_remaining_sensors(hass: HomeAssistant) -> None:
    """DPs 18/19 are seconds left on the body and feet timers."""
    device, coordinator, entities = _sensors(hass, *BLANKET)
    body, feet = entities
    for entity in entities:
        assert entity.device_class == SensorDeviceClass.DURATION
        assert entity.native_unit_of_measurement == UnitOfTime.SECONDS
        assert entity.entity_category == EntityCategory.DIAGNOSTIC
    await _push(
        coordinator,
        entities,
        device,
        [
            (18, TuyaBLEDataPointType.DT_VALUE, 420),
            (19, TuyaBLEDataPointType.DT_VALUE, 3600),
        ],
    )
    assert body.native_value == 420
    assert feet.native_value == 3600


def _selects(
    hass: HomeAssistant,
) -> tuple[TuyaBLEDevice, dict[int, select.TuyaBLESelect]]:
    """Build the blanket selects keyed by DP id."""
    device, coordinator, product = _context(hass, *BLANKET)
    entities = {}
    for mapping in select.get_mapping_by_device(device):
        entity = select.TuyaBLESelect(hass, coordinator, device, product, mapping)
        entity.hass = hass
        entities[mapping.dp_id] = entity
    return device, entities


@pytest.mark.parametrize(
    ("dp_id", "options"),
    [(14, LEVELS), (15, LEVELS), (16, TIMERS), (17, TIMERS)],
)
def test_blanket_select_options(
    hass: HomeAssistant, dp_id: int, options: list[str]
) -> None:
    """Levels show as 0-6 and timers as 1h-12h, in enum order."""
    _, entities = _selects(hass)
    assert entities[dp_id].options == options


@pytest.mark.parametrize(
    ("dp_id", "option", "index"),
    [(14, "0", 0), (15, "6", 6), (16, "1h", 0), (17, "6h", 5)],
)
async def test_blanket_select_writes_enum_index(
    hass: HomeAssistant, dp_id: int, option: str, index: int
) -> None:
    """Selecting an option writes its enum index to the DP."""
    device, entities = _selects(hass)
    with patch.object(device, "send_datapoints", new_callable=AsyncMock) as send:
        entities[dp_id].select_option(option)
        await hass.async_block_till_done()
        datapoint = device.datapoints[dp_id]
        assert datapoint is not None
        assert datapoint.dp_type == TuyaBLEDataPointType.DT_ENUM
        assert datapoint.value == index
        send.assert_awaited_once_with([dp_id])
    assert entities[dp_id].current_option == option


@pytest.mark.parametrize("dp_id", [16, 17])
@pytest.mark.parametrize("option", TIMERS[6:])
async def test_blanket_timers_above_6h_are_not_settable(
    hass: HomeAssistant, dp_id: int, option: str
) -> None:
    """7h-12h are unverified ENUM indices over BLE, so they are never written."""
    device, entities = _selects(hass)
    with patch.object(device, "send_datapoints", new_callable=AsyncMock) as send:
        with pytest.raises(ServiceValidationError) as exc_info:
            entities[dp_id].select_option(option)
        await hass.async_block_till_done()
        send.assert_not_awaited()
    assert exc_info.value.translation_key == "option_not_settable"
    assert device.datapoints[dp_id] is None


@pytest.mark.parametrize(("index", "option"), [(1, "1"), (5, "6h"), (11, "12h")])
def test_blanket_select_reads_enum_index(
    hass: HomeAssistant, index: int, option: str
) -> None:
    """A reported enum index shows as the matching option."""
    device, entities = _selects(hass)
    dp_id = 14 if option.isdigit() else 16
    add_dp(device, dp_id, TuyaBLEDataPointType.DT_ENUM, index)
    assert entities[dp_id].current_option == option


async def test_blanket_power_switch(hass: HomeAssistant) -> None:
    """DP 1 is the boolean power switch."""
    device, coordinator, product = _context(hass, *BLANKET)
    (mapping,) = switch.get_mapping_by_device(device)
    entity = switch.TuyaBLESwitch(hass, coordinator, device, product, mapping)
    entity.hass = hass
    assert entity.is_on is False
    add_dp(device, 1, TuyaBLEDataPointType.DT_BOOL, True)
    assert entity.is_on is True
    with patch.object(device, "send_datapoints", new_callable=AsyncMock) as send:
        entity.turn_off()
        await hass.async_block_till_done()
        datapoint = device.datapoints[1]
        assert datapoint is not None
        assert datapoint.value is False
        send.assert_awaited_once_with([1])
