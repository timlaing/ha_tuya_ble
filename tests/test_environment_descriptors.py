"""Descriptors for the environment sensors ported in issue #173.

Covers the XZ-JCO01 CO2 detector (``co2bj`` / ``wmb2xzj6``), the SS302
(``wsdcg`` / ``6lbesej0``) and TH05 (``wsdcg`` / ``vyfoip9h``, ``1jvidcsf``)
temperature and humidity sensors, and the SGS01B-JR (``zwjcy`` / ``gpkyrocn``)
temperature unit. BLE I/O is stubbed; datapoints arrive through the public
``update_from_device`` path.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from homeassistant.components.number.const import (
    ATTR_VALUE,
    SERVICE_SET_VALUE,
)
from homeassistant.components.number.const import (
    DOMAIN as NUMBER_DOMAIN,
)
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import ATTR_ENTITY_ID, EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import (
    setup_test_component_platform,
)

from custom_components.tuya_ble import climate, number, select, sensor, switch
from custom_components.tuya_ble.const import DOMAIN
from custom_components.tuya_ble.coordinator import TuyaBLECoordinator
from custom_components.tuya_ble.device_registry import get_mapped_dp_ids, get_registry
from custom_components.tuya_ble.entity import (
    TuyaBLEProductInfo,
    get_device_product_info,
)
from custom_components.tuya_ble.tuya_ble import TuyaBLEDataPointType, TuyaBLEDevice
from tests.conftest import add_dp, connect, make_credentials, make_device

TH05_PRODUCTS = ("vyfoip9h", "1jvidcsf")
ZWJCY_PRODUCTS = ("gpkyrocn", "gvygg3m8", "jabotj1z")
DEVICE_ID = "device123"


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
    """Build every descriptor sensor for a product."""
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
    ("category", "product_id", "device_name", "dp_ids"),
    [
        (
            "co2bj",
            "wmb2xzj6",
            "XZ-JCO01 CO2 Detector",
            {1, 2, 11, 13, 15, 17, 18, 19, 26, 101},
        ),
        ("wsdcg", "6lbesej0", "SS302 Temperature Humidity Sensor", {1, 2, 4}),
        (
            "wsdcg",
            "vyfoip9h",
            "TH05 Temperature Humidity Sensor",
            {1, 2, 4, 9, 23, 24},
        ),
        (
            "wsdcg",
            "1jvidcsf",
            "TH05F Temperature Humidity Sensor",
            {1, 2, 4, 9, 23, 24},
        ),
        ("zwjcy", "gpkyrocn", "SGS01B-JR Soil Sensor", {3, 5, 9, 14, 15}),
    ],
)
def test_descriptor_metadata_and_mapped_dps(
    category: str, product_id: str, device_name: str, dp_ids: set[int]
) -> None:
    """Each product resolves with its own name and only the evidenced DPs."""
    descriptor = get_registry().get(category, product_id)
    assert descriptor is not None
    assert descriptor.device_name == device_name
    assert descriptor.model_name == product_id
    assert get_mapped_dp_ids(category, product_id) == dp_ids


def test_xz_jco01_reuses_the_co2_detector_layout() -> None:
    """The reporter ran wmb2xzj6 on the 59s19z5m mappings unchanged (#317)."""
    reference = get_registry().get("co2bj", "59s19z5m")
    product = get_registry().get("co2bj", "wmb2xzj6")
    assert reference is not None
    assert product is not None
    assert product.entities == reference.entities


def test_th05_variants_share_one_layout() -> None:
    """TH05-BLE and TH05F-BLE come from one tuya-local definition."""
    first, second = (get_registry().get("wsdcg", pid) for pid in TH05_PRODUCTS)
    assert first is not None
    assert second is not None
    assert first.entities == second.entities


def test_ss302_matches_the_th05_sensors_without_settings() -> None:
    """SS302 exposes the TH05 readings but none of the settings without evidence."""
    ss302 = get_registry().get("wsdcg", "6lbesej0")
    th05 = get_registry().get("wsdcg", "vyfoip9h")
    assert ss302 is not None
    assert th05 is not None
    assert set(ss302.entities) == {"sensor"}
    assert [
        (d.dp_id, d.translation_key, d.coefficient) for d in ss302.get("sensor")
    ] == [(d.dp_id, d.translation_key, d.coefficient) for d in th05.get("sensor")]
    assert "unit_dp_id" not in ss302.get("sensor")[0].extra


@pytest.mark.parametrize("product_id", ["6lbesej0", "vyfoip9h", "1jvidcsf"])
def test_new_wsdcg_products_only_add_entities_on_their_platforms(
    product_id: str,
) -> None:
    """No climate/switch entities and no fallback to another product."""
    device = make_device()
    device.initialize_with_credentials(
        make_credentials(category="wsdcg", product_id=product_id)
    )
    assert climate.get_mapping_by_device(device) == []
    assert switch.get_mapping_by_device(device) == []


def test_unknown_wkf_product_has_no_mappings() -> None:
    """The deferred wkf TRV gets no entities rather than a copied wk layout."""
    device = make_device()
    device.initialize_with_credentials(
        make_credentials(category="wkf", product_id="llflaywg")
    )
    assert get_registry().get("wkf", "llflaywg") is None
    for platform in (climate, number, select, sensor, switch):
        assert platform.get_mapping_by_device(device) == []


# --- Sensor reads and scaling ---------------------------------------------


@pytest.mark.parametrize("product_id", ["6lbesej0", *TH05_PRODUCTS])
@pytest.mark.parametrize(("raw", "expected"), [(235, 23.5), (-55, -5.5), (0, 0.0)])
async def test_th_sensor_readings_are_scaled(
    hass: HomeAssistant, product_id: str, raw: int, expected: float
) -> None:
    """DP 1 is tenths of a degree; DP 2 and DP 4 are whole percentages."""
    device, coordinator, entities = _sensors(hass, "wsdcg", product_id)
    temperature, humidity, battery = entities
    assert temperature.device_class == SensorDeviceClass.TEMPERATURE
    assert temperature.state_class == SensorStateClass.MEASUREMENT
    assert humidity.device_class == SensorDeviceClass.HUMIDITY
    assert battery.device_class == SensorDeviceClass.BATTERY
    assert battery.entity_category == EntityCategory.DIAGNOSTIC
    await _push(
        coordinator,
        entities,
        device,
        [
            (1, TuyaBLEDataPointType.DT_VALUE, raw),
            (2, TuyaBLEDataPointType.DT_VALUE, 48),
            (4, TuyaBLEDataPointType.DT_VALUE, 87),
        ],
    )
    assert temperature.native_value == expected
    assert temperature.native_unit_of_measurement == UnitOfTemperature.CELSIUS
    assert humidity.native_value == 48
    assert battery.native_value == 87


@pytest.mark.parametrize(
    ("category", "product_id", "temp_dp", "raw", "expected"),
    [
        ("wsdcg", "vyfoip9h", 1, 725, 72.5),
        ("wsdcg", "1jvidcsf", 1, 725, 72.5),
        ("zwjcy", "gpkyrocn", 5, 725, 72.5),
    ],
)
@pytest.mark.parametrize(
    ("unit_value", "unit"),
    [(0, UnitOfTemperature.CELSIUS), (1, UnitOfTemperature.FAHRENHEIT)],
)
async def test_temperature_follows_dp9_unit(
    hass: HomeAssistant,
    category: str,
    product_id: str,
    temp_dp: int,
    raw: int,
    expected: float,
    unit_value: int,
    unit: str,
) -> None:
    """The temperature sensor labels its value with the DP 9 unit."""
    device, coordinator, entities = _sensors(hass, category, product_id)
    temperature = next(e for e in entities if e.device_class == "temperature")
    assert temperature.native_unit_of_measurement == UnitOfTemperature.CELSIUS
    await _push(
        coordinator,
        entities,
        device,
        [
            (temp_dp, TuyaBLEDataPointType.DT_VALUE, raw),
            (9, TuyaBLEDataPointType.DT_ENUM, unit_value),
        ],
    )
    assert temperature.native_value == expected
    assert temperature.native_unit_of_measurement == unit


async def test_ss302_temperature_ignores_dp9(hass: HomeAssistant) -> None:
    """SS302 has no evidenced unit DP, so a stray DP 9 never relabels it."""
    device, coordinator, entities = _sensors(hass, "wsdcg", "6lbesej0")
    await _push(coordinator, entities, device, [(9, TuyaBLEDataPointType.DT_ENUM, 1)])
    assert entities[0].native_unit_of_measurement == UnitOfTemperature.CELSIUS


@pytest.mark.parametrize("product_id", ZWJCY_PRODUCTS)
async def test_soil_reading_uses_the_moisture_class(
    hass: HomeAssistant, product_id: str
) -> None:
    """DP 3 is soil moisture, not air humidity."""
    device, coordinator, entities = _sensors(hass, "zwjcy", product_id)
    moisture = next(e for e in entities if e.entity_description.key == "moisture")
    assert moisture.device_class == SensorDeviceClass.MOISTURE
    assert moisture.native_unit_of_measurement == "%"
    await _push(coordinator, entities, device, [(3, TuyaBLEDataPointType.DT_VALUE, 41)])
    assert moisture.native_value == 41
    assert moisture.unique_id == f"{DEVICE_ID}-moisture"


@pytest.mark.parametrize("product_id", ZWJCY_PRODUCTS)
async def test_soil_moisture_keeps_the_existing_unique_id(
    hass: HomeAssistant, entity_registry: er.EntityRegistry, product_id: str
) -> None:
    """An entity registered under the old ``humidity`` key keeps its id."""
    entity_registry.async_get_or_create(
        "sensor", DOMAIN, f"{DEVICE_ID}-humidity", suggested_object_id="soil"
    )
    _, _, entities = _sensors(hass, "zwjcy", product_id)
    moisture = next(e for e in entities if e.entity_description.key == "moisture")
    assert moisture.unique_id == f"{DEVICE_ID}-humidity"
    temperature = next(e for e in entities if e.device_class == "temperature")
    assert temperature.unique_id == f"{DEVICE_ID}-temp_current"


@pytest.mark.parametrize(
    ("legacy", "key"),
    [
        ("temp_current", "temperature"),
        ("humidity_value", "humidity"),
        ("battery_percentage", "battery"),
    ],
)
@pytest.mark.parametrize("product_id", ["6lbesej0", *TH05_PRODUCTS])
async def test_th_sensors_adopt_source_fork_unique_ids(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    product_id: str,
    legacy: str,
    key: str,
) -> None:
    """Entities created by the source fork's keys keep their unique ids."""
    entity_registry.async_get_or_create("sensor", DOMAIN, f"{DEVICE_ID}-{legacy}")
    _, _, entities = _sensors(hass, "wsdcg", product_id)
    entity = next(e for e in entities if e.entity_description.key == key)
    assert entity.unique_id == f"{DEVICE_ID}-{legacy}"


# --- Temperature unit select ------------------------------------------------


@pytest.mark.parametrize(
    ("category", "product_id"),
    [("wsdcg", "vyfoip9h"), ("wsdcg", "1jvidcsf"), ("zwjcy", "gpkyrocn")],
)
@pytest.mark.parametrize(("option", "value"), [("°C", 0), ("°F", 1)])
async def test_temperature_unit_select_writes_dp9_enum(
    hass: HomeAssistant, category: str, product_id: str, option: str, value: int
) -> None:
    """DP 9 is an ENUM; the select is disabled by default and writes the index."""
    device, coordinator, product = _context(hass, category, product_id)
    mappings = select.get_mapping_by_device(device)
    assert [m.dp_id for m in mappings] == [9]
    entity = select.TuyaBLESelect(hass, coordinator, device, product, mappings[0])
    entity.hass = hass
    assert entity.options == ["°C", "°F"]
    assert entity.entity_category == EntityCategory.CONFIG
    assert entity.entity_registry_enabled_default is False
    assert entity.unique_id == f"{DEVICE_ID}-temperature_unit"
    with patch.object(device, "send_datapoints", new_callable=AsyncMock) as send:
        entity.select_option(option)
        await hass.async_block_till_done()
        datapoint = device.datapoints[9]
        assert datapoint is not None
        assert datapoint.dp_type == TuyaBLEDataPointType.DT_ENUM
        assert datapoint.value == value
        send.assert_awaited_once_with([9])

        send.reset_mock()
        entity.select_option("K")
        await hass.async_block_till_done()
        send.assert_not_awaited()
    assert entity.current_option == option


async def test_temperature_unit_select_reports_unknown_enum(
    hass: HomeAssistant,
) -> None:
    """An enum index outside c/f is shown raw rather than as a wrong unit."""
    device, coordinator, product = _context(hass, "zwjcy", "gpkyrocn")
    entity = select.TuyaBLESelect(
        hass, coordinator, device, product, select.get_mapping_by_device(device)[0]
    )
    assert entity.current_option is None
    add_dp(device, 9, TuyaBLEDataPointType.DT_ENUM, 1)
    assert entity.current_option == "°F"
    add_dp(device, 9, TuyaBLEDataPointType.DT_ENUM, 2)
    assert entity.current_option == "2"


# --- TH05 calibration numbers ---------------------------------------------


def _numbers(
    hass: HomeAssistant, product_id: str
) -> tuple[TuyaBLEDevice, TuyaBLECoordinator, list[number.TuyaBLENumber]]:
    """Build the TH05 calibration numbers."""
    device, coordinator, product = _context(hass, "wsdcg", product_id)
    entities = [
        number.TuyaBLENumber(hass, coordinator, device, product, mapping)
        for mapping in number.get_mapping_by_device(device)
    ]
    for entity in entities:
        entity.hass = hass
    return device, coordinator, entities


@pytest.mark.parametrize("product_id", TH05_PRODUCTS)
def test_th05_calibration_ranges(hass: HomeAssistant, product_id: str) -> None:
    """Both calibrations are hidden config numbers with the schema ranges."""
    _, _, (temperature, humidity) = _numbers(hass, product_id)
    assert temperature.entity_description.key == "temperature_calibration"
    assert (temperature.native_min_value, temperature.native_max_value) == (-2, 2)
    assert temperature.native_step == 0.1
    assert temperature.native_unit_of_measurement == UnitOfTemperature.CELSIUS
    assert humidity.entity_description.key == "humidity_calibration"
    assert (humidity.native_min_value, humidity.native_max_value) == (-10, 10)
    assert humidity.native_step == 1
    assert humidity.native_unit_of_measurement == "%"
    for entity in (temperature, humidity):
        assert entity.entity_category == EntityCategory.CONFIG
        assert entity.entity_registry_enabled_default is False
        assert entity.device_class is None


@pytest.mark.parametrize("product_id", TH05_PRODUCTS)
@pytest.mark.parametrize(
    ("dp_id", "raw", "expected"),
    [(23, -15, -1.5), (23, 20, 2.0), (24, -10, -10), (24, 7, 7)],
)
def test_th05_calibration_reads(
    hass: HomeAssistant, product_id: str, dp_id: int, raw: int, expected: float
) -> None:
    """Calibration reads apply the DP 23 scale and leave DP 24 unscaled."""
    device, _, entities = _numbers(hass, product_id)
    entity = entities[0] if dp_id == 23 else entities[1]
    add_dp(device, dp_id, TuyaBLEDataPointType.DT_VALUE, raw)
    assert entity.native_value == expected


@pytest.mark.parametrize("product_id", TH05_PRODUCTS)
@pytest.mark.parametrize(
    ("index", "value", "dp_id", "raw"),
    [(0, -1.5, 23, -15), (0, 0.3, 23, 3), (0, 2.0, 23, 20), (1, -10, 24, -10)],
)
async def test_th05_calibration_writes(
    hass: HomeAssistant,
    product_id: str,
    index: int,
    value: float,
    dp_id: int,
    raw: int,
) -> None:
    """Writes are signed DT_VALUE integers with float steps rounded."""
    device, _, entities = _numbers(hass, product_id)
    with patch.object(device, "send_datapoints", new_callable=AsyncMock) as send:
        entities[index].set_native_value(value)
        await hass.async_block_till_done()
    datapoint = device.datapoints[dp_id]
    assert datapoint is not None
    assert datapoint.dp_type == TuyaBLEDataPointType.DT_VALUE
    assert datapoint.value == raw
    send.assert_awaited_once_with([dp_id])


@pytest.mark.parametrize("value", [-2.1, 2.5, 20])
async def test_th05_calibration_rejects_out_of_range(
    hass: HomeAssistant, entity_registry: er.EntityRegistry, value: float
) -> None:
    """The number service refuses values outside the DP 23 range."""
    device, coordinator, entities = _numbers(hass, "vyfoip9h")
    # The user has enabled the disabled-by-default calibration entity.
    entity_registry.async_get_or_create(
        NUMBER_DOMAIN, "test", f"{DEVICE_ID}-temperature_calibration"
    )
    with (
        patch.object(device, "update", new_callable=AsyncMock),
        patch.object(device, "send_datapoints", new_callable=AsyncMock) as send,
    ):
        setup_test_component_platform(hass, NUMBER_DOMAIN, entities[:1])
        assert await async_setup_component(
            hass, NUMBER_DOMAIN, {NUMBER_DOMAIN: {"platform": "test"}}
        )
        await hass.async_block_till_done()
        await connect(coordinator)
        entity_id = entities[0].entity_id
        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(
                NUMBER_DOMAIN,
                SERVICE_SET_VALUE,
                {ATTR_ENTITY_ID: entity_id, ATTR_VALUE: value},
                blocking=True,
            )
        await hass.services.async_call(
            NUMBER_DOMAIN,
            SERVICE_SET_VALUE,
            {ATTR_ENTITY_ID: entity_id, ATTR_VALUE: -0.5},
            blocking=True,
        )
        await hass.async_block_till_done()
    datapoint = device.datapoints[23]
    assert datapoint is not None
    assert datapoint.value == -5
    send.assert_awaited_once_with([23])


# --- Unit change while the write is pending ---------------------------------


@pytest.mark.parametrize(
    ("category", "product_id", "temp_dp", "other_dp"),
    [("zwjcy", "gpkyrocn", 5, 3), ("wsdcg", "vyfoip9h", 1, 2)],
)
async def test_pending_unit_write_keeps_the_reading_in_its_unit(
    hass: HomeAssistant,
    category: str,
    product_id: str,
    temp_dp: int,
    other_dp: int,
) -> None:
    """A pending unit write never relabels the current reading.

    The select applies DP 9 optimistically before the send completes. A report
    for another DP during that wait must not show the old reading in °F; once
    the device reports DP 9 itself, the sensor follows it.
    """
    device, coordinator, entities = _sensors(hass, category, product_id)
    temperature = next(e for e in entities if e.dp_id == temp_dp)
    product = get_device_product_info(device)
    unit_select = select.TuyaBLESelect(
        hass, coordinator, device, product, select.get_mapping_by_device(device)[0]
    )
    unit_select.hass = hass
    await _push(
        coordinator,
        entities,
        device,
        [
            (temp_dp, TuyaBLEDataPointType.DT_VALUE, 200),
            (9, TuyaBLEDataPointType.DT_ENUM, 0),
        ],
    )
    assert (temperature.native_value, temperature.native_unit_of_measurement) == (
        20.0,
        UnitOfTemperature.CELSIUS,
    )

    release = asyncio.Event()

    async def _held_send(_dp_ids: list[int]) -> None:
        await release.wait()

    with (
        patch.object(device, "send_datapoints", side_effect=_held_send),
        patch.object(sensor.TuyaBLESensor, "async_write_ha_state"),
    ):
        unit_select.select_option("°F")
        await asyncio.sleep(0)
        unit = device.datapoints[9]
        assert unit is not None
        assert unit.value == 1  # applied optimistically, send still pending

        add_dp(device, other_dp, TuyaBLEDataPointType.DT_VALUE, 40)
        coordinator.async_set_updated_data({})
        assert temperature.native_value == 20.0
        assert temperature.native_unit_of_measurement == UnitOfTemperature.CELSIUS

        release.set()
        await hass.async_block_till_done()

        # Still only the local write: the device has not confirmed the unit.
        coordinator.async_set_updated_data({})
        assert temperature.native_unit_of_measurement == UnitOfTemperature.CELSIUS

        add_dp(device, 9, TuyaBLEDataPointType.DT_ENUM, 1)
        coordinator.async_set_updated_data({})
        assert temperature.native_unit_of_measurement == UnitOfTemperature.FAHRENHEIT

        add_dp(device, temp_dp, TuyaBLEDataPointType.DT_VALUE, 680)
        coordinator.async_set_updated_data({})
    assert temperature.native_value == 68.0
    assert temperature.native_unit_of_measurement == UnitOfTemperature.FAHRENHEIT


@pytest.mark.parametrize(
    ("category", "product_id", "temp_dp"),
    [("zwjcy", "gpkyrocn", 5), ("wsdcg", "vyfoip9h", 1)],
)
async def test_unit_report_after_the_reading_relabels_it(
    hass: HomeAssistant, category: str, product_id: str, temp_dp: int
) -> None:
    """The declared °C is only a fallback until DP 9 is reported.

    A reading that arrives before the device's first unit report is shown in
    the unit the device then reports, without waiting for another reading.
    """
    device, coordinator, entities = _sensors(hass, category, product_id)
    temperature = next(e for e in entities if e.dp_id == temp_dp)
    await _push(
        coordinator, entities, device, [(temp_dp, TuyaBLEDataPointType.DT_VALUE, 725)]
    )
    assert temperature.native_unit_of_measurement == UnitOfTemperature.CELSIUS
    await _push(coordinator, entities, device, [(9, TuyaBLEDataPointType.DT_ENUM, 1)])
    assert temperature.native_value == 72.5
    assert temperature.native_unit_of_measurement == UnitOfTemperature.FAHRENHEIT
    await _push(coordinator, entities, device, [(9, TuyaBLEDataPointType.DT_ENUM, 0)])
    assert temperature.native_unit_of_measurement == UnitOfTemperature.CELSIUS
