"""Parkside PAMRC 250 A1 robot mower (``gcj`` / ``9hdajpiw``) entities.

The supporting entities are built from the shipped descriptor and driven
through their public platform entry points.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
import pytest

from custom_components.tuya_ble import binary_sensor, button, number, sensor, switch
from custom_components.tuya_ble.device_descriptors.handlers import parkside_mower
from custom_components.tuya_ble.devices import get_product_info_by_ids
from custom_components.tuya_ble.tuya_ble import TuyaBLEDataPointType
from tests.conftest import add_dp, build_context, make_product_info

CATEGORY, PRODUCT_ID = "gcj", "9hdajpiw"
RAIN_DELAY_DP = 139


class _FakeDevice:
    """Stand-in exposing only the category and product id."""

    category = CATEGORY
    product_id = PRODUCT_ID


def _mapping(module: Any, key: str) -> Any:
    """Return the shipped mapping of *module* whose key is *key*."""
    for item in module.get_mapping_by_device(_FakeDevice()):
        if item.description.key == key:
            return item
    raise AssertionError(f"{key} is not mapped")


def _entity(hass: HomeAssistant, module: Any, cls: str, key: str) -> Any:
    """Build an entity of *module* from the shipped descriptor."""
    device, coordinator, _ = build_context(hass)
    product = get_product_info_by_ids(CATEGORY, PRODUCT_ID)
    entity = getattr(module, cls)(
        hass, coordinator, device, product, _mapping(module, key)
    )
    entity.hass = hass
    entity.async_write_ha_state = lambda: None
    return entity


def _keys(module: Any) -> list[str]:
    return [
        item.description.key for item in module.get_mapping_by_device(_FakeDevice())
    ]


def test_descriptor_exposes_the_supported_entities() -> None:
    """Only the verified mower data points get entities."""
    product = get_product_info_by_ids(CATEGORY, PRODUCT_ID)
    assert product.manufacturer == "PARKSIDE"
    assert product.name == "PARKSIDE Robot Mower PAMRC 250 A1 (BT) City"
    assert _keys(button) == []
    assert _keys(binary_sensor) == ["machine_problem", "machine_cover"]
    assert _keys(sensor) == [
        "battery",
        "activity",
        "machine_warning",
        "error_log",
        "work_log",
        "firmware_version",
    ]
    assert _keys(switch) == [
        "rain_delay",
        "rain_mode",
        "hedgehog_protection",
        "backward_blade_stop",
    ]
    assert _keys(number) == ["rain_delay_time", "work_time"]
    cover = _mapping(binary_sensor, "machine_cover")
    assert cover.description.entity_registry_enabled_default is False
    assert _mapping(
        binary_sensor, "machine_problem"
    ).description.entity_registry_enabled_default


# --- Status sensors ---------------------------------------------------------


@pytest.mark.parametrize(
    ("payload", "is_on"), [(b"\x00\x00\x00\x00", False), (b"\x00\x00\x02\x00", True)]
)
async def test_machine_problem_reads_the_error_bitmap(
    hass: HomeAssistant, payload: bytes, is_on: bool
) -> None:
    """Any set bit of the machine error bitmap reports a problem."""
    entity = _entity(hass, binary_sensor, "TuyaBLEBinarySensor", "machine_problem")
    add_dp(entity.device, 102, TuyaBLEDataPointType.DT_BITMAP, payload)
    entity._handle_coordinator_update()  # pylint: disable=protected-access
    assert entity.is_on is is_on


@pytest.mark.parametrize(
    ("key", "dp_id", "code", "state"),
    [
        ("activity", 101, 3, "paused"),
        ("activity", 101, 12, "edge"),
        ("machine_warning", 103, 26, "hedgehog"),
    ],
)
async def test_enum_sensors_report_the_status_name(
    hass: HomeAssistant, key: str, dp_id: int, code: int, state: str
) -> None:
    """Status and warning enums report their declared names."""
    entity = _entity(hass, sensor, "TuyaBLESensor", key)
    add_dp(entity.device, dp_id, TuyaBLEDataPointType.DT_ENUM, code)
    entity._handle_coordinator_update()  # pylint: disable=protected-access
    assert entity.native_value == state


async def test_battery_ignores_the_raw_duplicate(hass: HomeAssistant) -> None:
    """The battery keeps its value when DP 13 is also reported as raw."""
    entity = _entity(hass, sensor, "TuyaBLESensor", "battery")
    add_dp(entity.device, 13, TuyaBLEDataPointType.DT_VALUE, 87)
    entity._handle_coordinator_update()  # pylint: disable=protected-access
    add_dp(entity.device, 13, TuyaBLEDataPointType.DT_RAW, b"\x01\x57")
    entity._handle_coordinator_update()  # pylint: disable=protected-access
    assert entity.native_value == 87


# --- Logs -----------------------------------------------------------------


async def test_error_log_decodes_entries(hass: HomeAssistant) -> None:
    """Error log entries carry an epoch and a named error; empty slots are skipped."""
    entity = _entity(hass, sensor, "TuyaBLESensor", "error_log")
    payload = (
        (1_700_000_000).to_bytes(4, "big")
        + b"\x15"
        + bytes(5)
        + (1_700_000_060).to_bytes(4, "big")
        + b"\x63"
        + b"\x01\x02"  # trailing partial entry
    )
    add_dp(entity.device, 111, TuyaBLEDataPointType.DT_RAW, payload)
    entity._handle_coordinator_update()  # pylint: disable=protected-access
    assert entity.native_value == 2
    assert entity.extra_state_attributes == {
        "errors": [
            {"time": "2023-11-14T22:13:20+00:00", "error": "LIFTE"},
            {"time": "2023-11-14T22:14:20+00:00", "error": "99"},
        ]
    }


async def test_work_log_decodes_entries(hass: HomeAssistant) -> None:
    """Work log entries carry a start, a duration and a mowing mode."""
    entity = _entity(hass, sensor, "TuyaBLESensor", "work_log")
    payload = (
        (1_700_000_000).to_bytes(4, "big")
        + (3600).to_bytes(4, "big")
        + b"\x01"
        + bytes(9)
        + (1_700_010_000).to_bytes(4, "big")
        + (60).to_bytes(4, "big")
        + b"\x07"
    )
    add_dp(entity.device, 112, TuyaBLEDataPointType.DT_RAW, payload)
    entity._handle_coordinator_update()  # pylint: disable=protected-access
    assert entity.native_value == 2
    assert entity.extra_state_attributes == {
        "sessions": [
            {
                "start": "2023-11-14T22:13:20+00:00",
                "duration_seconds": 3600,
                "mode": "auto_mowing",
            },
            {
                "start": "2023-11-15T01:00:00+00:00",
                "duration_seconds": 60,
                "mode": "7",
            },
        ]
    }


@pytest.mark.parametrize("key", ["error_log", "work_log"])
async def test_logs_stay_unknown_until_reported(hass: HomeAssistant, key: str) -> None:
    """A log that is missing or not raw leaves the sensor unknown."""
    entity = _entity(hass, sensor, "TuyaBLESensor", key)
    entity._handle_coordinator_update()  # pylint: disable=protected-access
    assert entity.native_value is None
    dp_id = 111 if key == "error_log" else 112
    add_dp(entity.device, dp_id, TuyaBLEDataPointType.DT_VALUE, 5)
    entity._handle_coordinator_update()  # pylint: disable=protected-access
    assert entity.native_value is None
    assert entity.extra_state_attributes is None


# --- Rain delay (one raw DP shared by a switch and a number) ----------------


async def test_rain_delay_unavailable_until_reported(hass: HomeAssistant) -> None:
    """Neither half of the rain delay is writable before the payload is known."""
    toggle = _entity(hass, switch, "TuyaBLESwitch", "rain_delay")
    delay = _entity(hass, number, "TuyaBLENumber", "rain_delay_time")
    product = get_product_info_by_ids(CATEGORY, PRODUCT_ID)
    for entity in (toggle, delay):
        assert not parkside_mower.rain_delay_reported(entity, product)
    assert toggle.is_on is False
    assert delay.native_value is None
    toggle.turn_on()
    delay.set_native_value(30)
    await hass.async_block_till_done()
    assert toggle.device.sent == []
    assert delay.device.sent == []


@pytest.mark.parametrize("payload", [b"\x01", 5])
async def test_rain_delay_ignores_a_malformed_payload(
    hass: HomeAssistant, payload: Any
) -> None:
    """A payload shorter than two bytes or not raw is not used."""
    entity = _entity(hass, switch, "TuyaBLESwitch", "rain_delay")
    dp_type = (
        TuyaBLEDataPointType.DT_RAW
        if isinstance(payload, bytes)
        else TuyaBLEDataPointType.DT_VALUE
    )
    add_dp(entity.device, RAIN_DELAY_DP, dp_type, payload)
    product = get_product_info_by_ids(CATEGORY, PRODUCT_ID)
    assert not parkside_mower.rain_delay_reported(entity, product)
    assert parkside_mower.get_rain_delay(entity, product) is None


async def test_rain_delay_switch_keeps_the_delay(hass: HomeAssistant) -> None:
    """Toggling the rain delay keeps the minutes held in the second byte."""
    entity = _entity(hass, switch, "TuyaBLESwitch", "rain_delay")
    add_dp(entity.device, RAIN_DELAY_DP, TuyaBLEDataPointType.DT_RAW, b"\x00\x2d")
    product = get_product_info_by_ids(CATEGORY, PRODUCT_ID)
    assert parkside_mower.rain_delay_reported(entity, product)
    assert entity.is_on is False
    entity.turn_on()
    await hass.async_block_till_done()
    datapoint = entity.device.datapoints[RAIN_DELAY_DP]
    assert datapoint is not None
    assert datapoint.value == b"\x01\x2d"
    assert entity.is_on is True
    entity.turn_off()
    await hass.async_block_till_done()
    assert datapoint.value == b"\x00\x2d"


async def test_rain_delay_time_keeps_the_switch(hass: HomeAssistant) -> None:
    """Setting the delay keeps the switch byte and any trailing bytes."""
    entity = _entity(hass, number, "TuyaBLENumber", "rain_delay_time")
    add_dp(entity.device, RAIN_DELAY_DP, TuyaBLEDataPointType.DT_RAW, b"\x01\x0a\x99")
    assert entity.native_value == 10.0
    entity.set_native_value(60)
    await hass.async_block_till_done()
    datapoint = entity.device.datapoints[RAIN_DELAY_DP]
    assert datapoint is not None
    assert datapoint.value == b"\x01\x3c\x99"
    assert entity.native_value == 60.0
    entity.set_native_value(999)
    await hass.async_block_till_done()
    assert datapoint.value == b"\x01\xff\x99"


async def test_rain_delay_handlers_without_a_rain_delay_entity(
    hass: HomeAssistant,
) -> None:
    """A product declaring neither rain delay entity has no payload to use."""
    entity = _entity(hass, switch, "TuyaBLESwitch", "rain_delay")
    add_dp(entity.device, RAIN_DELAY_DP, TuyaBLEDataPointType.DT_RAW, b"\x01\x0a")
    other = make_product_info()
    assert not parkside_mower.rain_delay_reported(entity, other)
    assert parkside_mower.get_rain_delay_time(entity, other) is None
    await parkside_mower.set_rain_delay(entity, other, False)
    await parkside_mower.set_rain_delay_time(entity, other, 20)
    await hass.async_block_till_done()
    assert entity.device.sent == []
