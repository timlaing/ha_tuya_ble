"""Unit tests for the Tuya BLE sensor entity."""

# pylint: disable=protected-access
from __future__ import annotations

from decimal import Decimal
import logging

from homeassistant.components.sensor import SensorDeviceClass, SensorEntityDescription
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant, State
import pytest
from pytest_homeassistant_custom_component.common import (
    mock_restore_cache_with_extra_data,
)

from custom_components.tuya_ble import sensor
from custom_components.tuya_ble.device_descriptors.handlers import battery, co2, rssi
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


def _stored_sensor_data(native_value: object) -> dict[str, object]:
    """Build the extra restore payload a sensor stores."""
    return {"native_value": native_value}


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


def _make_restorable(
    hass: HomeAssistant, restore: bool = True
) -> tuple[TuyaBLESensor, TuyaBLEDevice, TuyaBLECoordinator]:
    """Build the last-use sensor of a ggq dual timer, optionally restorable."""
    device, coordinator, product = build_context(hass)
    device._device_info = make_credentials(category="ggq", product_id="fdrbxxbg")
    mapping = next(
        item for item in sensor.get_mapping_by_device(device) if item.dp_id == 111
    )
    mapping.restore = restore
    entity = _make_entity(hass, device, coordinator, product, mapping)
    entity.entity_id = "sensor.last_use"
    return entity, device, coordinator


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


async def test_diivoo_use_time_is_a_duration(hass: HomeAssistant) -> None:
    """Expose the Diivoo use-time value as seconds rather than a timestamp."""
    device, coordinator, product = build_context(hass)
    device._device_info = make_credentials(category="ggq", product_id="fdrbxxbg")
    mapping = next(
        item for item in sensor.get_mapping_by_device(device) if item.dp_id == 111
    )
    assert mapping.description.device_class is SensorDeviceClass.DURATION
    assert mapping.description.native_unit_of_measurement is UnitOfTime.SECONDS

    entity = _make_entity(hass, device, coordinator, product, mapping)
    add_dp(device, 111, TuyaBLEDataPointType.DT_VALUE, 600)
    datapoint = device.datapoints[111]
    assert datapoint is not None
    entity._update_from_datapoint(datapoint)

    assert entity.native_value == 600.0


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
        getter=battery.battery_enum,
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
        is_available=co2.alarm_enabled,
    )
    add_dp(device, 13, TuyaBLEDataPointType.DT_ENUM, 1)
    entity = _make_entity(hass, device, coordinator, product, mapping)
    assert entity.available is False
    await connect(coordinator)
    assert entity.available is True


async def test_is_co2_alarm_enabled_no_datapoint(hass: HomeAssistant) -> None:
    """Verify co2.alarm_enabled returns True when datapoint is absent."""
    device, coordinator, product = build_context(hass)
    mapping = sensor.TuyaBLESensorMapping(
        dp_id=13,
        description=SensorEntityDescription(key="co2"),
        is_available=co2.alarm_enabled,
    )
    entity = _make_entity(hass, device, coordinator, product, mapping)
    await connect(coordinator)
    assert entity.available is True


async def test_battery_enum_getter_no_datapoint(hass: HomeAssistant) -> None:
    """Verify battery.battery_enum with absent datapoint does not set value."""
    device, coordinator, product = build_context(hass)
    mapping = sensor.TuyaBLESensorMapping(
        dp_id=104,
        description=SensorEntityDescription(key="battery"),
        getter=battery.battery_enum,
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
    """Verify an enum without an options list falls back to its raw value."""
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
    assert entity.native_value == "1"


async def test_sensor_enum_no_options_non_int(hass: HomeAssistant) -> None:
    """Verify a non-int enum without options is reported as a string."""
    device, coordinator, product = build_context(hass)
    mapping = sensor.TuyaBLESensorMapping(
        dp_id=1,
        description=SensorEntityDescription(key="e"),
    )
    entity = _make_entity(hass, device, coordinator, product, mapping)
    await entity.async_added_to_hass()
    add_dp(device, 1, TuyaBLEDataPointType.DT_ENUM, "auto")
    coordinator.async_set_updated_data({})
    await hass.async_block_till_done()
    assert entity.native_value == "auto"


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
    """Verify rssi.rssi reads device.rssi into native_value."""
    device, coordinator, product = build_context(hass)
    mapping = sensor.TuyaBLESensorMapping(
        dp_id=-1,
        description=SensorEntityDescription(key="signal_strength"),
        getter=rssi.rssi,
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


# ---- restore ----


async def test_restore_enabled_sensor_adopts_stored_value(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A restore-enabled sensor shows the value of the previous run."""
    entity, _device, _coordinator = _make_restorable(hass)
    mock_restore_cache_with_extra_data(
        hass,
        [(State("sensor.last_use", "600"), _stored_sensor_data(600))],
    )

    with caplog.at_level(logging.DEBUG):
        await entity.async_added_to_hass()

    assert entity.native_value == 600
    assert "Restored value for last_use_time_zone1" in caplog.text


@pytest.mark.parametrize("native_value", [True, 42, 4.5, "manual", None])
def test_restore_data_serializes_plain_values(native_value: object) -> None:
    """Numbers, strings, booleans and None are stored as they are."""
    assert sensor.SensorRestoreData(native_value).as_dict() == {
        "native_value": native_value
    }


def test_restore_data_serializes_other_values_as_text() -> None:
    """A value the state dump cannot encode is stored as text, not dropped."""
    assert sensor.SensorRestoreData(Decimal("1.5")).as_dict() == {"native_value": "1.5"}


async def test_restore_data_holds_the_live_value(hass: HomeAssistant) -> None:
    """The value stored for the next run is the one the device last reported."""
    entity, device, coordinator = _make_restorable(hass)
    await entity.async_added_to_hass()

    assert entity.extra_restore_state_data is not None
    assert entity.extra_restore_state_data.as_dict() == {"native_value": None}

    add_dp(device, 111, TuyaBLEDataPointType.DT_VALUE, 600)
    coordinator.async_set_updated_data({})
    await hass.async_block_till_done()

    assert entity.native_value == 600
    stored = entity.extra_restore_state_data
    assert stored is not None
    assert stored.as_dict() == {"native_value": 600}


async def test_restore_reads_a_payload_without_a_value(hass: HomeAssistant) -> None:
    """A stored payload without a value leaves the entity unknown."""
    entity, _device, _coordinator = _make_restorable(hass)

    mock_restore_cache_with_extra_data(
        hass,
        [(State("sensor.last_use", "600"), {})],
    )

    await entity.async_added_to_hass()

    assert entity.native_value is None


async def test_restore_enabled_sensor_keeps_storing_while_device_answers(
    hass: HomeAssistant,
) -> None:
    """Restoring does not stop the entity from following the live value."""
    entity, device, coordinator = _make_restorable(hass)
    mock_restore_cache_with_extra_data(
        hass,
        [(State("sensor.last_use", "600"), _stored_sensor_data(600))],
    )
    await entity.async_added_to_hass()
    assert entity.native_value == 600

    add_dp(device, 111, TuyaBLEDataPointType.DT_VALUE, 120)
    coordinator.async_set_updated_data({})
    await hass.async_block_till_done()

    assert entity.native_value == 120.0


async def test_restore_is_skipped_when_device_reports_a_value(
    hass: HomeAssistant,
) -> None:
    """Nothing is restored while the device already holds the data point."""
    entity, device, _coordinator = _make_restorable(hass)
    mock_restore_cache_with_extra_data(
        hass,
        [(State("sensor.last_use", "600"), _stored_sensor_data(600))],
    )
    add_dp(device, 111, TuyaBLEDataPointType.DT_VALUE, 120)

    await entity.async_added_to_hass()

    assert entity.native_value is None


async def test_restore_is_skipped_when_disabled(hass: HomeAssistant) -> None:
    """A sensor without the restore opt-in ignores the stored value."""
    entity, _device, _coordinator = _make_restorable(hass, restore=False)
    mock_restore_cache_with_extra_data(
        hass,
        [(State("sensor.last_use", "600"), _stored_sensor_data(600))],
    )

    await entity.async_added_to_hass()

    assert entity.native_value is None


async def test_restore_without_stored_value(hass: HomeAssistant) -> None:
    """A restore-enabled sensor with no history stays empty."""
    entity, _device, _coordinator = _make_restorable(hass)
    mock_restore_cache_with_extra_data(hass, [])

    await entity.async_added_to_hass()

    assert entity.native_value is None


async def test_restore_ignores_empty_stored_value(hass: HomeAssistant) -> None:
    """A stored entry without a native value is not adopted."""
    entity, _device, _coordinator = _make_restorable(hass)
    mock_restore_cache_with_extra_data(
        hass,
        [(State("sensor.last_use", "unknown"), _stored_sensor_data(None))],
    )

    await entity.async_added_to_hass()

    assert entity.native_value is None


async def test_ggq_work_state_sensor_labels_captured_codes(
    hass: HomeAssistant,
) -> None:
    """The operation sensor reports the captured 0/2 work codes as labels."""
    device, coordinator, product = build_context(hass)
    device._device_info = make_credentials(category="ggq", product_id="fdrbxxbg")
    mapping = next(
        item for item in sensor.get_mapping_by_device(device) if item.dp_id == 112
    )
    entity = _make_entity(hass, device, coordinator, product, mapping)
    await entity.async_added_to_hass()

    add_dp(device, 112, TuyaBLEDataPointType.DT_ENUM, 0)
    coordinator.async_set_updated_data({})
    await hass.async_block_till_done()
    assert entity.native_value == "watering"

    add_dp(device, 112, TuyaBLEDataPointType.DT_ENUM, 2)
    coordinator.async_set_updated_data({})
    await hass.async_block_till_done()
    assert entity.native_value == "idle"


async def test_ggq_schedule_sensor_reports_raw_payload(
    hass: HomeAssistant,
) -> None:
    """The schedule sensor exposes the undecoded payload as hex."""
    device, coordinator, product = build_context(hass)
    device._device_info = make_credentials(category="ggq", product_id="fdrbxxbg")
    mapping = next(
        item for item in sensor.get_mapping_by_device(device) if item.dp_id == 101
    )
    assert mapping.description.entity_registry_enabled_default is False
    assert mapping.description.entity_category == "diagnostic"
    entity = _make_entity(hass, device, coordinator, product, mapping)
    await entity.async_added_to_hass()

    add_dp(device, 101, TuyaBLEDataPointType.DT_RAW, b"", b"\x07\x00\x3c")
    coordinator.async_set_updated_data({})
    await hass.async_block_till_done()

    assert entity.native_value == "07003c"
