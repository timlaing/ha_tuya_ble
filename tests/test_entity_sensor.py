"""Unit tests for the Tuya BLE sensor entity."""

# pylint: disable=protected-access
from __future__ import annotations

from homeassistant.components.sensor import SensorEntityDescription
from homeassistant.core import HomeAssistant

from custom_components.tuya_ble import sensor
from custom_components.tuya_ble.devices import (
    TuyaBLECoordinator,
    TuyaBLEProductInfo,
)
from custom_components.tuya_ble.sensor import TuyaBLESensor
from custom_components.tuya_ble.tuya_ble import (
    TuyaBLEDataPointType,
    TuyaBLEDevice,
)
from tests.conftest import (
    FakeAdvertisementData,
    add_dp,
    build_context,
    connect,
    make_credentials,
)


def _make_entity(
    hass: HomeAssistant,
    device: TuyaBLEDevice,
    coordinator: TuyaBLECoordinator,
    product: TuyaBLEProductInfo,
    mapping: sensor.TuyaBLESensorMapping | None = None,
) -> TuyaBLESensor:
    """Build a sensor entity, creating a default mapping when needed."""
    if mapping is None:
        mapping = sensor.TuyaBLESensorMapping(
            dp_id=1,
            description=SensorEntityDescription(key="temp"),
        )
    entity = sensor.TuyaBLESensor(hass, coordinator, device, product, mapping)
    entity.hass = hass
    return entity


async def test_value_via_datapoint(hass: HomeAssistant) -> None:
    """Verify native_value reflects the datapoint value."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    await entity.async_added_to_hass()
    add_dp(device, 1, TuyaBLEDataPointType.DT_VALUE, 25)
    coordinator.async_set_updated_data({})
    await hass.async_block_till_done()
    assert entity.native_value == 25.0


async def test_value_with_coefficient(hass: HomeAssistant) -> None:
    """Verify native_value is divided by the mapping coefficient."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    await entity.async_added_to_hass()
    entity._mapping.coefficient = 10.0
    add_dp(device, 1, TuyaBLEDataPointType.DT_VALUE, 250)
    coordinator.async_set_updated_data({})
    await hass.async_block_till_done()
    assert entity.native_value == 25.0


async def test_enum_value(hass: HomeAssistant) -> None:
    """Verify an enum datapoint maps to its options entry."""
    device, coordinator, product = build_context(hass)
    mapping = sensor.TuyaBLESensorMapping(
        dp_id=1,
        description=SensorEntityDescription(key="e", options=["a", "b", "c"]),
    )
    entity = _make_entity(hass, device, coordinator, product, mapping)
    await entity.async_added_to_hass()
    add_dp(device, 1, TuyaBLEDataPointType.DT_ENUM, 1)
    coordinator.async_set_updated_data({})
    await hass.async_block_till_done()
    assert entity.native_value == "b"


async def test_enum_value_out_of_range(hass: HomeAssistant) -> None:
    """Verify an out-of-range enum is reported as its raw value."""
    device, coordinator, product = build_context(hass)
    mapping = sensor.TuyaBLESensorMapping(
        dp_id=1,
        description=SensorEntityDescription(key="e", options=["a", "b"]),
    )
    entity = _make_entity(hass, device, coordinator, product, mapping)
    await entity.async_added_to_hass()
    add_dp(device, 1, TuyaBLEDataPointType.DT_ENUM, 9)
    coordinator.async_set_updated_data({})
    await hass.async_block_till_done()
    assert entity.native_value == "9"


async def test_getter(hass: HomeAssistant) -> None:
    """Verify the battery getter converts a value datapoint."""
    device, coordinator, product = build_context(hass)
    mapping = sensor.TuyaBLESensorMapping(
        dp_id=104,
        description=SensorEntityDescription(key="battery"),
        getter=sensor.battery_enum_getter,
    )
    add_dp(device, 104, TuyaBLEDataPointType.DT_VALUE, 3)
    entity = _make_entity(hass, device, coordinator, product, mapping)
    await entity.async_added_to_hass()
    coordinator.async_set_updated_data({})
    await hass.async_block_till_done()
    assert entity.native_value == 60.0


async def test_available_with_is_available(hass: HomeAssistant) -> None:
    """Verify the is_available gate plus connection state."""
    device, coordinator, product = build_context(hass)
    mapping = sensor.TuyaBLESensorMapping(
        dp_id=13,
        description=SensorEntityDescription(key="co2"),
        is_available=sensor.is_co2_alarm_enabled,
    )
    add_dp(device, 13, TuyaBLEDataPointType.DT_ENUM, 1)
    entity = _make_entity(hass, device, coordinator, product, mapping)
    assert entity.available is False
    await connect(coordinator)
    assert entity.available is True


async def test_is_co2_alarm_enabled_no_datapoint(hass: HomeAssistant) -> None:
    """Verify is_co2_alarm_enabled returns True when datapoint is absent."""
    device, coordinator, product = build_context(hass)
    mapping = sensor.TuyaBLESensorMapping(
        dp_id=13,
        description=SensorEntityDescription(key="co2"),
        is_available=sensor.is_co2_alarm_enabled,
    )
    entity = _make_entity(hass, device, coordinator, product, mapping)
    await connect(coordinator)
    assert entity.available is True


async def test_battery_enum_getter_no_datapoint(hass: HomeAssistant) -> None:
    """Verify battery_enum_getter with absent datapoint does not set value."""
    device, coordinator, product = build_context(hass)
    mapping = sensor.TuyaBLESensorMapping(
        dp_id=104,
        description=SensorEntityDescription(key="battery"),
        getter=sensor.battery_enum_getter,
    )
    entity = _make_entity(hass, device, coordinator, product, mapping)
    await entity.async_added_to_hass()
    coordinator.async_set_updated_data({})
    await hass.async_block_till_done()
    assert entity.native_value is None


async def test_sensor_update_string_datapoint(hass: HomeAssistant) -> None:
    """Verify sensor handles DT_STRING datapoint via the else branch."""
    device, coordinator, product = build_context(hass)
    mapping = sensor.TuyaBLESensorMapping(
        dp_id=1,
        description=SensorEntityDescription(key="s"),
        dp_type=TuyaBLEDataPointType.DT_STRING,
    )
    entity = _make_entity(hass, device, coordinator, product, mapping)
    await entity.async_added_to_hass()
    add_dp(device, 1, TuyaBLEDataPointType.DT_STRING, "hello")
    coordinator.async_set_updated_data({})
    await hass.async_block_till_done()
    assert entity.native_value == "hello"


async def test_sensor_enum_no_options(hass: HomeAssistant) -> None:
    """Verify sensor handles enum with no options list."""
    device, coordinator, product = build_context(hass)
    mapping = sensor.TuyaBLESensorMapping(
        dp_id=1,
        description=SensorEntityDescription(key="e"),
    )
    entity = _make_entity(hass, device, coordinator, product, mapping)
    await entity.async_added_to_hass()
    add_dp(device, 1, TuyaBLEDataPointType.DT_ENUM, 1)
    coordinator.async_set_updated_data({})
    await hass.async_block_till_done()
    assert entity.native_value is None


async def test_sensor_enum_with_icons(hass: HomeAssistant) -> None:
    """Verify sensor assigns icon from the icons list."""
    device, coordinator, product = build_context(hass)
    mapping = sensor.TuyaBLESensorMapping(
        dp_id=1,
        description=SensorEntityDescription(key="e"),
        icons=["mdi:icon_a", "mdi:icon_b"],
    )
    entity = _make_entity(hass, device, coordinator, product, mapping)
    await entity.async_added_to_hass()
    add_dp(device, 1, TuyaBLEDataPointType.DT_ENUM, 0)
    coordinator.async_set_updated_data({})
    await hass.async_block_till_done()
    assert entity.icon == "mdi:icon_a"


async def test_rssi_getter(hass: HomeAssistant) -> None:
    """Verify rssi_getter reads device.rssi into native_value."""
    device, coordinator, product = build_context(hass)
    mapping = sensor.TuyaBLESensorMapping(
        dp_id=-1,
        description=SensorEntityDescription(key="signal_strength"),
        getter=sensor.rssi_getter,
    )
    entity = _make_entity(hass, device, coordinator, product, mapping)
    await entity.async_added_to_hass()
    device._advertisement_data = FakeAdvertisementData(  # type: ignore[assignment]
        rssi=-72,
    )
    coordinator.async_set_updated_data({})
    await hass.async_block_till_done()
    assert entity.native_value == -72


async def test_get_mapping_by_device_known_category(
    hass: HomeAssistant,
) -> None:
    """Verify get_mapping_by_device returns mappings for a known device."""
    device, _coordinator, _product = build_context(hass)
    device._device_info = make_credentials(category="co2bj", product_id="59s19z5m")
    mappings = sensor.get_mapping_by_device(device)
    assert len(mappings) > 0
    assert mappings[0].dp_id in {1, 2, 15, 18, 19}


async def test_get_mapping_by_device_unknown_category(
    hass: HomeAssistant,
) -> None:
    """Verify get_mapping_by_device returns empty list for unknown category."""
    device, _coordinator, _product = build_context(hass)
    device._device_info = make_credentials(category="unknown", product_id="unknown")
    mappings = sensor.get_mapping_by_device(device)
    assert mappings == []


async def test_get_mapping_by_device_known_category_unknown_product(
    hass: HomeAssistant,
) -> None:
    """Verify get_mapping_by_device returns empty for unknown product."""
    device, _coordinator, _product = build_context(hass)
    device._device_info = make_credentials(category="co2bj", product_id="unknown")
    mappings = sensor.get_mapping_by_device(device)
    assert mappings == []


async def test_get_mapping_by_device_fallback_to_category_mapping(
    hass: HomeAssistant,
) -> None:
    """Verify get_mapping_by_device returns category.mapping when product not found."""
    device, _coordinator, _product = build_context(hass)
    device._device_info = make_credentials(category="test_cat", product_id="unknown")
    # Temporarily patch the mapping dict to add a category with a mapping fallback
    fallback_mapping = [
        sensor.TuyaBLESensorMapping(
            dp_id=99,
            description=SensorEntityDescription(key="fallback"),
        )
    ]
    sensor.mapping["test_cat"] = sensor.TuyaBLECategorySensorMapping(
        products={"known_product": []},
        mapping=fallback_mapping,
    )
    try:
        mappings = sensor.get_mapping_by_device(device)
        assert mappings == fallback_mapping
    finally:
        del sensor.mapping["test_cat"]


async def test_handle_update_no_datapoint(hass: HomeAssistant) -> None:
    """Verify _handle_coordinator_update with no datapoint."""
    device, coordinator, product = build_context(hass)
    mapping = sensor.TuyaBLESensorMapping(
        dp_id=99,
        description=SensorEntityDescription(key="missing"),
    )
    entity = _make_entity(hass, device, coordinator, product, mapping)
    await entity.async_added_to_hass()
    coordinator.async_set_updated_data({})
    await hass.async_block_till_done()
    assert entity.native_value is None


async def test_dt_value_non_int_no_change(hass: HomeAssistant) -> None:
    """Verify DT_VALUE with non-int value does not update native_value."""
    device, coordinator, product = build_context(hass)
    mapping = sensor.TuyaBLESensorMapping(
        dp_id=1,
        description=SensorEntityDescription(key="val"),
    )
    entity = _make_entity(hass, device, coordinator, product, mapping)
    await entity.async_added_to_hass()
    # Manually create a datapoint with a non-int value using update_from_user path
    dp = device.datapoints.get_or_create(1, TuyaBLEDataPointType.DT_VALUE, 42)
    dp._type = TuyaBLEDataPointType.DT_VALUE  # ensure type is DT_VALUE
    dp._value = "not_an_int"  # simulate corrupt value
    dp._changed_by_device = False
    # Directly call _update_from_datapoint to test the branch
    entity._update_from_datapoint(dp)
    assert entity.native_value is None


async def test_dt_raw_with_str_value(hass: HomeAssistant) -> None:
    """Verify DT_RAW with str value updates native_value."""
    device, coordinator, product = build_context(hass)
    mapping = sensor.TuyaBLESensorMapping(
        dp_id=1,
        description=SensorEntityDescription(key="raw"),
    )
    entity = _make_entity(hass, device, coordinator, product, mapping)
    await entity.async_added_to_hass()
    dp = device.datapoints.get_or_create(1, TuyaBLEDataPointType.DT_RAW, b"\x01\x02")
    dp._value = "string_in_raw"
    entity._update_from_datapoint(dp)
    assert entity.native_value == "string_in_raw"


async def test_dt_bitmap_with_bytes_no_update(hass: HomeAssistant) -> None:
    """Verify DT_BITMAP with bytes value does not update native_value."""
    device, coordinator, product = build_context(hass)
    mapping = sensor.TuyaBLESensorMapping(
        dp_id=1,
        description=SensorEntityDescription(key="bmp"),
    )
    entity = _make_entity(hass, device, coordinator, product, mapping)
    await entity.async_added_to_hass()
    dp = device.datapoints.get_or_create(1, TuyaBLEDataPointType.DT_BITMAP, b"\xff")
    entity._update_from_datapoint(dp)
    # bytes is not (int, float, str), so native_value stays None
    assert entity.native_value is None


async def test_dt_value_with_float_no_change(hass: HomeAssistant) -> None:
    """Verify DT_VALUE with float value does not update native_value."""
    device, coordinator, product = build_context(hass)
    mapping = sensor.TuyaBLESensorMapping(
        dp_id=1,
        description=SensorEntityDescription(key="val"),
    )
    entity = _make_entity(hass, device, coordinator, product, mapping)
    await entity.async_added_to_hass()
    dp = device.datapoints.get_or_create(1, TuyaBLEDataPointType.DT_VALUE, 42)
    dp._value = 3.14  # type: ignore[assignment]  # float, not int
    entity._update_from_datapoint(dp)
    assert entity.native_value is None
