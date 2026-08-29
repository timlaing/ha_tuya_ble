"""Unit tests for the platform mapping-by-device functions and pure helpers."""

# pylint: disable=protected-access
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, cast

from homeassistant.components.number.const import NumberMode
import pytest

from custom_components.tuya_ble import (
    binary_sensor,
    button,
    climate,
    cover,
    light,
    number,
    select,
    sensor,
    switch,
    text,
    valve,
)
from custom_components.tuya_ble.device_descriptors.handlers.co2 import alarm_enabled
from custom_components.tuya_ble.device_descriptors.handlers.fingerbot import (
    get_position,
    in_program_mode,
    set_position,
)
from custom_components.tuya_ble.device_descriptors.handlers.water_valve import (
    is_water_valve_in_switch_mode,
)
from custom_components.tuya_ble.device_registry import EntityDescriptor
from custom_components.tuya_ble.devices import TuyaBLEFingerbotInfo
from custom_components.tuya_ble.products import devices_database
from custom_components.tuya_ble.tuya_ble import TuyaBLEDataPointType, TuyaBLEDevice

PLATFORMS = {
    "binary_sensor": binary_sensor,
    "button": button,
    "climate": climate,
    "number": number,
    "select": select,
    "sensor": sensor,
    "switch": switch,
    "text": text,
    "valve": valve,
}

# These mappings intentionally use category-level product information because
# their exact product IDs have not yet been added to the product registry.
CATEGORY_FALLBACK_PRODUCTS = {
    ("cl", "qqdxfdht"),
    ("ms", "bvclwu9b"),
}


@dataclass
class FakeDevice:
    """Stand-in device object exposing category and product id."""

    category: str
    product_id: str


def all_datapoints(platform_module: Any) -> Iterator[tuple[str, str]]:
    """Iterate every category/product in the module mapping dict."""
    for category, cat_info in platform_module.mapping.items():
        products = getattr(cat_info, "products", None) or {}
        for product_id in products:
            yield category, product_id


@pytest.mark.parametrize("name", sorted(PLATFORMS))
def test_every_product_has_mapping(name: str) -> None:
    """Verify every known product resolves to at least one mapping."""
    mod = PLATFORMS[name]
    for category, product_id in all_datapoints(mod):
        dev = FakeDevice(category, product_id)
        result = mod.get_mapping_by_device(dev)
        assert result, f"{name}:{category}:{product_id} produced no mappings"


@pytest.mark.parametrize("name", sorted(PLATFORMS))
def test_unknown_category_empty(name: str) -> None:
    """Verify an unknown category produces no mappings."""
    mod = PLATFORMS[name]
    assert mod.get_mapping_by_device(FakeDevice("nope", "nope")) == []


@pytest.mark.parametrize("name", sorted(PLATFORMS))
def test_known_category_unknown_product_falls_back(name: str) -> None:
    """Verify unknown products fall back to the category-level mapping."""
    mod = PLATFORMS[name]
    # patch a category-level fallback mapping and assert it is returned
    category = next(iter(mod.mapping))
    cat_info = mod.mapping[category]
    fallback = cat_info.mapping
    cat_info.mapping = ["fallback"]
    try:
        result = mod.get_mapping_by_device(FakeDevice(category, "unknown"))
        assert result == ["fallback"]
    finally:
        cat_info.mapping = fallback


# --- Pure helpers ----------------------------------------------------------


def make_dp(value: Any, dp_id: int = 1) -> Any:
    """Build a fake data point with a recorded set_value history."""

    class _DP:
        """A fake data point recording set_value calls."""

        def __init__(self, value: Any, dp_id: int) -> None:
            self.value = value
            self.dp_id = dp_id
            self.calls: list[Any] = []

        def set_value(self, new_value: Any) -> None:
            """Record a set_value call."""
            self.calls.append(new_value)

    return _DP(value, dp_id)


def test_build_sensor_mapping_enabled_by_default_false() -> None:
    """A disabled-by-default descriptor yields a disabled description."""

    desc = EntityDescriptor(
        platform="sensor",
        dp_id=21,
        translation_key="low_battery_alarm",
        enabled_by_default=False,
    )
    built = sensor._build_sensor_mapping(desc)
    assert built.dp_id == 21
    assert built.description.key == "low_battery_alarm"
    assert built.description.entity_registry_enabled_default is False


def test_build_sensor_mapping_kind_battery_and_temperature() -> None:
    """Battery/temperature kinds select the specialized mapping classes."""

    battery = sensor._build_sensor_mapping(
        EntityDescriptor(
            platform="sensor",
            dp_id=15,
            kind="battery",
            translation_key="battery",
            icon="mdi:battery",
        )
    )
    assert isinstance(battery, sensor.TuyaBLEBatteryMapping)
    assert battery.description.key == "battery"

    temperature = sensor._build_sensor_mapping(
        EntityDescriptor(
            platform="sensor",
            dp_id=18,
            kind="temperature",
            translation_key="temperature",
        )
    )
    assert isinstance(temperature, sensor.TuyaBLETemperatureMapping)
    assert temperature.description.key == "temperature"


def test_temperature_unit_description() -> None:
    """Verify the temperature unit entity description is built with a key."""
    desc = select.TemperatureUnitDescription(key="temperature_unit")
    assert desc.key == "temperature_unit"


def test_build_select_mapping_temperature_unit() -> None:
    """A temperature_unit descriptor yields a description with the given options."""

    desc = EntityDescriptor(
        platform="select",
        dp_id=101,
        translation_key="temperature_unit",
        options=["°C", "°F"],
    )
    built = select._build_select_mapping(desc)
    assert built.dp_id == 101
    assert built.description.key == "temperature_unit"
    assert built.description.options == ["°C", "°F"]


def test_build_select_mapping_no_options() -> None:
    """A descriptor without options yields a description missing options."""

    desc = EntityDescriptor(
        platform="select",
        dp_id=5,
        translation_key="work_state",
    )
    built = select._build_select_mapping(desc)
    assert isinstance(built, select.TuyaBLESelectMapping)
    assert built.description.key == "work_state"
    assert built.description.options is None


def test_build_select_mapping_fingerbot_mode() -> None:
    """A fingerbot_mode descriptor yields the dedicated mapping class."""

    desc = EntityDescriptor(
        platform="select",
        dp_id=8,
        translation_key="fingerbot_mode",
        kind="fingerbot_mode",
    )
    built = select._build_select_mapping(desc)
    assert isinstance(built, select.TuyaBLEFingerbotModeMapping)
    assert built.dp_id == 8


@pytest.mark.parametrize(
    ("kind", "mapping_class"),
    [
        ("TuyaBLEFingerbotSwitchMapping", "TuyaBLEFingerbotSwitchMapping"),
        ("TuyaBLEReversePositionsMapping", "TuyaBLEReversePositionsMapping"),
        ("TuyaLockMotorStateMapping", "TuyaLockMotorStateMapping"),
        ("TuyaBLEWaterValveSwitchMapping", "TuyaBLEWaterValveSwitchMapping"),
        (
            "TuyaBLEWaterValveWeatherSwitchMapping",
            "TuyaBLEWaterValveWeatherSwitchMapping",
        ),
    ],
)
def test_build_switch_mapping_kinds(kind: str, mapping_class: str) -> None:
    """Each switch kind selects its dedicated mapping class."""

    desc = EntityDescriptor(
        platform="switch",
        dp_id=1,
        translation_key="switch",
        kind=kind,
    )
    built = switch._build_switch_mapping(desc)
    assert isinstance(built, getattr(switch, mapping_class))
    assert built.dp_id == 1


def test_build_switch_mapping_kind_preserves_class_default_availability() -> None:
    """Kinds keep their class-default availability when no 'when' handler exists."""

    desc = EntityDescriptor(
        platform="switch",
        dp_id=1,
        translation_key="water_valve",
        kind="TuyaBLEWaterValveSwitchMapping",
    )
    built = switch._build_switch_mapping(desc)
    assert built.is_available is is_water_valve_in_switch_mode


def test_build_switch_mapping_unknown_kind() -> None:
    """An unknown kind falls back to the base switch mapping class."""

    desc = EntityDescriptor(
        platform="switch",
        dp_id=1,
        translation_key="water_valve",
        kind="TuyaBLEBogusMapping",
    )
    built = switch._build_switch_mapping(desc)
    assert isinstance(built, switch.TuyaBLESwitchMapping)


def test_build_switch_mapping_bitmap_mask_and_handlers() -> None:
    """Co2 bitmap masks and resolved handlers are carried into the mapping."""

    desc = EntityDescriptor(
        platform="switch",
        dp_id=11,
        translation_key="carbon_dioxide_severely_exceed_alarm",
        icon="mdi:molecule-co2",
        entity_category="config",
        enabled_by_default=False,
        handlers={"when": "co2.alarm_enabled", "read": "rssi.rssi"},
        extra={"bitmap_mask": b"\x01"},
    )
    built = switch._build_switch_mapping(desc)
    assert isinstance(built, switch.TuyaBLESwitchMapping)
    assert built.dp_id == 11
    assert built.bitmap_mask == b"\x01"
    assert built.description.key == "carbon_dioxide_severely_exceed_alarm"
    assert built.description.entity_registry_enabled_default is False
    assert built.is_available is cast(Any, alarm_enabled)
    assert built.getter is not None
    assert built.setter is None


def test_build_switch_mapping_dp_type() -> None:
    """A descriptor with an explicit dp_type sets it on the mapping."""

    desc = EntityDescriptor(
        platform="switch",
        dp_id=1,
        translation_key="water_valve",
        dp_type=4,  # type: ignore[arg-type]
    )
    built = switch._build_switch_mapping(desc)
    assert built.dp_type == TuyaBLEDataPointType.DT_ENUM


def test_build_number_mapping_ranges_and_coefficient() -> None:
    """Number builder carries ranges, step, mode, and coefficient into the mapping."""

    desc = EntityDescriptor(
        platform="number",
        dp_id=17,
        translation_key="reporting_period",
        unit="min",
        entity_category="config",
        min_value=1,
        max_value=120,
        step=1,
        mode="slider",
        coefficient=10.0,
    )
    built = number._build_number_mapping(desc)
    assert built.dp_id == 17
    assert built.coefficient == 10.0
    assert built.mode is NumberMode.SLIDER
    assert built.description.key == "reporting_period"
    assert built.description.native_min_value == 1
    assert built.description.native_max_value == 120
    assert built.description.native_step == 1
    assert built.description.native_unit_of_measurement == "min"


def test_build_number_mapping_name_and_box_default() -> None:
    """The optional name field is carried and an absent mode defaults to box."""

    desc = EntityDescriptor(
        platform="number",
        dp_id=106,
        translation_key="countdown_duration_z1",
        name="CH1 Countdown",
    )
    built = number._build_number_mapping(desc)
    assert built.mode is NumberMode.BOX
    assert built.description.name == "CH1 Countdown"
    assert built.description.native_min_value is None


def test_build_number_mapping_dp_type_and_handlers() -> None:
    """Number builder resolves dp_type and read/write/when handlers."""

    desc = EntityDescriptor(
        platform="number",
        dp_id=121,
        translation_key="program_idle_position",
        dp_type=4,  # type: ignore[arg-type]
        handlers={
            "read": "fingerbot.program.get_position",
            "write": "fingerbot.program.set_position",
            "when": "fingerbot.mode.in_program_mode",
        },
    )
    built = number._build_number_mapping(desc)
    assert built.dp_type == TuyaBLEDataPointType.DT_ENUM
    assert built.getter is get_position
    assert built.setter is set_position
    assert built.is_available is in_program_mode


def test_get_mapping_by_device_category_default() -> None:
    """Category-level default mappings apply for an unknown product id."""
    original = switch.mapping
    try:
        switch.mapping = {
            "test_cat": switch.TuyaBLECategorySwitchMapping(
                products={
                    "known": [
                        switch.TuyaBLESwitchMapping(
                            dp_id=1,
                            description=cast(Any, SimpleNamespace(key="known")),
                        )
                    ]
                },
                mapping=[
                    switch.TuyaBLESwitchMapping(
                        dp_id=1,
                        description=cast(Any, SimpleNamespace(key="default")),
                    )
                ],
            ),
        }
        result = switch.get_mapping_by_device(
            cast(Any, FakeDevice("test_cat", "unknown"))
        )
        assert [m.description.key for m in result] == ["default"]
        assert (
            switch.get_mapping_by_device(cast(Any, FakeDevice("test_cat", "known")))[
                0
            ].description.key
            == "known"
        )
    finally:
        switch.mapping = original


def test_build_climate_mapping_full_fields() -> None:
    """Climate builder carries humidity, hvac mode, preset, and icon fields."""

    desc = EntityDescriptor(
        platform="climate",
        dp_id=0,
        translation_key="thermostat",
        icon="mdi:thermostat",
        extra={
            "hvac_mode_dp_id": 5,
            "hvac_modes": ["off", "heat"],
            "current_humidity_dp_id": 6,
            "current_humidity_coefficient": 2.0,
            "target_humidity_dp_id": 7,
            "target_humidity_coefficient": 2.0,
            "target_humidity_max": 80.0,
            "target_humidity_min": 20.0,
            "preset_mode_dp_ids": {"away": 8, "none": 8},
            "temperature_unit": "°C",
        },
    )
    built = climate._build_climate_mapping(desc)
    assert built.description.key == "thermostat"
    assert built.description.icon == "mdi:thermostat"
    assert built.hvac_mode_dp_id == 5
    assert built.hvac_modes == ["off", "heat"]
    assert built.current_humidity_dp_id == 6
    assert built.current_humidity_coefficient == 2.0
    assert built.target_humidity_dp_id == 7
    assert built.target_humidity_max == 80.0
    assert built.target_humidity_min == 20.0
    assert built.preset_mode_dp_ids == {"away": 8, "none": 8}
    assert built.temperature_unit == "°C"


def test_build_climate_mapping_defaults() -> None:
    """A descriptor with no extra fields yields the class defaults."""

    desc = EntityDescriptor(platform="climate", dp_id=0, translation_key="thermostat")
    built = climate._build_climate_mapping(desc)
    assert built.hvac_mode_dp_id == 0
    assert built.target_temperature_max == 30.0
    assert built.target_temperature_min == 5
    assert built.preset_mode_dp_ids is None


def test_build_cover_mapping() -> None:
    """Cover builder reads extra dp ids and icon."""

    desc = EntityDescriptor(
        platform="cover",
        dp_id=0,
        translation_key="ble_cover",
        icon="mdi:curtains",
        extra={
            "state_dp_id": 1,
            "position_set_dp_id": 2,
            "position_dp_id": 3,
            "tilt_dp_id": 101,
            "battery_dp_id": 13,
            "speed_dp_id": 105,
        },
    )
    built = cover._build_cover_mapping(desc)
    assert built.description.key == "ble_cover"
    assert built.description.icon == "mdi:curtains"
    assert built.state_dp_id == 1
    assert built.position_set_dp_id == 2
    assert built.position_dp_id == 3
    assert built.tilt_dp_id == 101
    assert built.battery_dp_id == 13
    assert built.speed_dp_id == 105


def test_build_light_mapping() -> None:
    """Light builder reads extra dp ids and icon."""

    desc = EntityDescriptor(
        platform="light",
        dp_id=0,
        translation_key="switch_led",
        icon="mdi:lightbulb",
        extra={
            "switch_dp_id": 1,
            "color_mode_dp_id": 2,
            "brightness_dp_id": 3,
            "color_temp_dp_id": 4,
            "color_data_dp_id": 5,
            "brightness_min": 0,
            "brightness_max": 1000,
            "color_temp_min": 10,
            "color_temp_max": 90,
        },
    )
    built = light._build_light_mapping(desc)
    assert built.description.key == "switch_led"
    assert built.description.name is None
    assert built.description.icon == "mdi:lightbulb"
    assert built.switch_dp_id == 1
    assert built.color_mode_dp_id == 2
    assert built.brightness_dp_id == 3
    assert built.color_temp_dp_id == 4
    assert built.color_data_dp_id == 5
    assert built.brightness_min == 0
    assert built.brightness_max == 1000
    assert built.color_temp_min == 10
    assert built.color_temp_max == 90


def test_fingerbot_info_defaults() -> None:
    """Verify default fingerbot info fields."""
    info = TuyaBLEFingerbotInfo(
        switch=1,
        mode=2,
        up_position=5,
        down_position=6,
        hold_time=3,
        reverse_positions=4,
    )
    assert info.manual_control == 0
    assert info.program == 0


@pytest.mark.parametrize(
    "name,category",
    [
        ("switch", "co2bj"),
        ("button", "znhsb"),
        ("binary_sensor", "wk"),
        ("text", "szjqr"),
        ("select", "co2bj"),
        ("number", "co2bj"),
        ("sensor", "co2bj"),
        ("valve", "ggq"),
        ("climate", "wk"),
    ],
)
def test_get_mapping_by_device_no_default_mapping(name: str, category: str) -> None:
    """Known category with no default mapping returns [] for unknown product."""
    mod = PLATFORMS[name]
    result = mod.get_mapping_by_device(FakeDevice(category, "unknown_xyz"))
    assert result == []


@pytest.mark.parametrize("name", ["number", "select", "sensor", "valve"])
def test_diivoo_dual_water_timer_mappings_use_ggq(name: str) -> None:
    """Resolve every dual water timer platform using its cloud category."""
    mod = PLATFORMS[name]
    assert mod.get_mapping_by_device(FakeDevice("ggq", "fdrbxxbg"))
    assert mod.get_mapping_by_device(FakeDevice("sfkzq", "fdrbxxbg")) == []


def test_sop10_water_timer_has_one_entity_per_datapoint_role() -> None:
    """Keep SOP10 status and control datapoints in their proper domains."""
    device = cast(TuyaBLEDevice, FakeDevice("sfkzq", "nxquc5lb"))

    assert [item.dp_id for item in sensor.get_mapping_by_device(device)] == [
        7,
        12,
        9,
        13,
        15,
        16,
        17,
    ]
    assert [item.dp_id for item in number.get_mapping_by_device(device)] == [11]
    assert [item.dp_id for item in switch.get_mapping_by_device(device)] == [14]
    assert [item.dp_id for item in valve.get_mapping_by_device(device)] == [1]
    assert [item.dp_id for item in select.get_mapping_by_device(device)] == [10]
    assert [item.dp_id for item in binary_sensor.get_mapping_by_device(device)] == [4]


def test_all_product_mappings_use_registered_category() -> None:
    """Keep product-specific mappings in their registered Tuya category."""
    mismatches: list[tuple[str, str, str]] = []

    for platform_name, platform in PLATFORMS.items():
        for category, category_mapping in platform.mapping.items():
            for product_id in category_mapping.products or {}:
                product_is_registered = (
                    category in devices_database
                    and devices_database[category].products is not None
                    and product_id in devices_database[category].products
                )
                if (
                    not product_is_registered
                    and (
                        category,
                        product_id,
                    )
                    not in CATEGORY_FALLBACK_PRODUCTS
                ):
                    mismatches.append((platform_name, category, product_id))

    assert not mismatches, mismatches
