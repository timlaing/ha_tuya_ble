"""Unit tests for the platform mapping-by-device functions and pure helpers."""

# The `type(x) is Base` assertions below deliberately reject subclasses:
# they assert the builders return one flat mapping class.
# pylint: disable=protected-access,unidiomatic-typecheck
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, cast

from homeassistant.components.binary_sensor.const import BinarySensorDeviceClass
from homeassistant.components.number.const import NumberMode
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTemperature
from homeassistant.helpers.typing import UNDEFINED
import pytest

from custom_components.tuya_ble import (
    binary_sensor,
    button,
    climate,
    cover,
    light,
    lock,
    number,
    select,
    sensor,
    switch,
    text,
    valve,
)
from custom_components.tuya_ble.device_descriptors.handlers.fingerbot import (
    get_position,
    in_program_mode,
    set_position,
)
from custom_components.tuya_ble.device_descriptors.handlers.water_valve import (
    is_water_valve_in_switch_mode,
)
from custom_components.tuya_ble.device_registry import (
    EntityDescriptor,
    get_mapped_dp_ids,
    get_registry,
)
from custom_components.tuya_ble.tuya_ble import TuyaBLEDataPointType, TuyaBLEDevice
from tests.conftest import make_product_info

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


def test_build_sensor_mapping_description_comes_from_the_descriptor() -> None:
    """The sensor description is built from descriptor fields alone.

    There is no ``kind`` indirection: the builder always passes an explicit
    description, so a kind class's ``default_factory`` could never apply.
    """

    battery = sensor._build_sensor_mapping(
        EntityDescriptor(
            platform="sensor",
            dp_id=15,
            translation_key="battery",
            icon="mdi:battery",
            device_class="battery",
            unit="%",
            state_class="measurement",
            entity_category="diagnostic",
        )
    )
    assert type(battery) is sensor.TuyaBLESensorMapping
    assert battery.description.key == "battery"
    assert battery.description.device_class is SensorDeviceClass.BATTERY
    assert battery.description.native_unit_of_measurement == PERCENTAGE
    assert battery.description.state_class is SensorStateClass.MEASUREMENT
    assert battery.description.entity_category is EntityCategory.DIAGNOSTIC

    temperature = sensor._build_sensor_mapping(
        EntityDescriptor(
            platform="sensor",
            dp_id=18,
            translation_key="temperature",
            device_class="temperature",
            unit="\N{DEGREE SIGN}C",
            state_class="measurement",
        )
    )
    assert type(temperature) is sensor.TuyaBLESensorMapping
    assert temperature.description.device_class is SensorDeviceClass.TEMPERATURE
    assert temperature.description.native_unit_of_measurement == (
        UnitOfTemperature.CELSIUS
    )


def test_temperature_unit_description() -> None:
    """Verify the temperature unit entity description is built with a key."""
    desc = select.TemperatureUnitDescription(key="temperature_unit")
    assert desc.key == "temperature_unit"
    assert desc.icon == "mdi:thermometer"
    assert desc.entity_category is EntityCategory.CONFIG


def test_temperature_unit_description_defaults() -> None:
    """The class-level icon and entity category survive as frozen dataclass defaults."""
    desc = select.TemperatureUnitDescription()
    assert desc.key == "temperature_unit"
    assert desc.icon == "mdi:thermometer"
    assert desc.entity_category is EntityCategory.CONFIG


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
    """A fingerbot_mode descriptor is described entirely by its own fields.

    The builder used to short-circuit on ``kind: fingerbot_mode`` and return a
    mapping with a hardcoded description, discarding the descriptor's
    ``entity_category`` and ``options``.
    """

    desc = EntityDescriptor(
        platform="select",
        dp_id=8,
        translation_key="fingerbot_mode",
        entity_category="config",
        options=["push", "switch", "program"],
    )
    built = select._build_select_mapping(desc)
    assert type(built) is select.TuyaBLESelectMapping
    assert built.description.key == "fingerbot_mode"
    assert built.description.entity_category is EntityCategory.CONFIG
    assert built.description.options == ["push", "switch", "program"]


def test_build_switch_mapping_is_always_the_base_class() -> None:
    """Switch mappings are one flat class; behaviour comes from fields.

    Five per-platform subclasses used to be selected by ``kind``, but the
    builder always passed an explicit ``description`` and an ``is_available``
    taken from the ``when`` handler, so those subclasses only ever supplied
    values the descriptor already carried.
    """

    desc = EntityDescriptor(
        platform="switch",
        dp_id=1,
        translation_key="water_valve",
    )
    built = switch._build_switch_mapping(desc)
    assert type(built) is switch.TuyaBLESwitchMapping
    assert built.description.key == "water_valve"


def test_build_switch_mapping_availability_comes_from_the_when_handler() -> None:
    """Availability is resolved from the 'when' handler, not a class default."""

    desc = EntityDescriptor(
        platform="switch",
        dp_id=1,
        translation_key="water_valve",
        handlers={"when": "water_valve.is_water_valve_in_switch_mode"},
    )
    built = switch._build_switch_mapping(desc)
    assert built.is_available is is_water_valve_in_switch_mode


def test_build_switch_mapping_bitmap_mask_and_handlers() -> None:
    """Co2 bitmap masks and resolved handlers are carried into the mapping."""

    desc = EntityDescriptor(
        platform="switch",
        dp_id=11,
        translation_key="carbon_dioxide_severely_exceed_alarm",
        icon="mdi:molecule-co2",
        entity_category="config",
        enabled_by_default=False,
        handlers={
            "when": "water_valve.is_water_valve_in_switch_mode",
            "read": "rssi.rssi",
        },
        extra={"bitmap_mask": b"\x01"},
    )
    built = switch._build_switch_mapping(desc)
    assert isinstance(built, switch.TuyaBLESwitchMapping)
    assert built.dp_id == 11
    assert built.bitmap_mask == b"\x01"
    assert built.description.key == "carbon_dioxide_severely_exceed_alarm"
    assert built.description.entity_registry_enabled_default is False
    assert built.is_available is cast(Any, is_water_valve_in_switch_mode)
    assert built.getter is not None
    assert built.setter is None


def test_build_switch_mapping_dp_type() -> None:
    """A descriptor with an explicit dp_type sets it on the mapping."""

    desc = EntityDescriptor(
        platform="switch",
        dp_id=1,
        translation_key="water_valve",
        dp_type=4,
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
        dp_type=4,
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


def test_get_mapping_by_device_category_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Category-level default mappings apply for an unknown product id."""
    monkeypatch.setattr(
        switch,
        "mapping",
        {
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
        },
    )
    result = switch.get_mapping_by_device(cast(Any, FakeDevice("test_cat", "unknown")))
    assert [m.description.key for m in result] == ["default"]
    assert (
        switch.get_mapping_by_device(cast(Any, FakeDevice("test_cat", "known")))[
            0
        ].description.key
        == "known"
    )


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


def test_get_mapped_dp_ids_includes_the_water_valve_entity() -> None:
    """The water valve's own data points count as mapped.

    The switch handler reads data points 15, 11 and 2 on top of the switch's own
    1, and all four are declared as entities, so nothing it touches is reported
    as an unmapped data point.
    """
    assert {1, 2, 11, 15} <= get_mapped_dp_ids("sfkzq", "16wgjvck")


def test_get_mapped_dp_ids_includes_weather_data_points_with_entities() -> None:
    """Data points 10 and 13 are mapped because entities now declare them.

    They used to be declared mapped by the water valve spec while no entity read
    them, which silenced the unmapped-data-point diagnostic. The descriptor now
    exposes both, so the claim is backed by a real entity.
    """
    assert {10, 13} <= get_mapped_dp_ids("sfkzq", "16wgjvck")


def test_get_mapped_dp_ids_excludes_data_points_no_entity_uses() -> None:
    """A data point no entity declares is not treated as mapped.

    12 and 9 exist on the sibling sfkzq products but not on this one, so they
    stay unmapped and are still reported.
    """
    assert {9, 12}.isdisjoint(get_mapped_dp_ids("sfkzq", "16wgjvck"))


def test_product_info_dp_ids_are_descriptor_derived() -> None:
    """A Fingerbot's dp ids come from its entities, absent ones stay None."""
    info = make_product_info(mode=2, switch=1)
    assert info.fingerbot_mode_dp_id == 2
    assert info.fingerbot_switch_dp_id == 1
    assert info.fingerbot_manual_control_dp_id is None
    assert info.fingerbot_program_dp_id is None
    assert not info.is_water_valve


def test_plain_product_has_no_fingerbot_dp_ids() -> None:
    """A product with no fingerbot entities resolves no fingerbot dp ids."""
    info = make_product_info()
    assert info.fingerbot_mode_dp_id is None
    assert info.fingerbot_switch_dp_id is None
    assert info.fingerbot_program_dp_id is None


@pytest.mark.parametrize(
    ("platform_name", "builder"),
    [
        ("sensor", sensor._build_sensor_mapping),
        ("binary_sensor", binary_sensor._build_binary_sensor_mapping),
        ("button", button._build_button_mapping),
        ("switch", switch._build_switch_mapping),
        ("select", select._build_select_mapping),
        ("text", text._build_text_mapping),
        ("number", number._build_number_mapping),
        ("valve", valve._build_valve_mapping),
        ("cover", cover._build_cover_mapping),
        ("light", light._build_light_mapping),
        ("climate", climate._build_climate_mapping),
        ("lock", lock._build_lock_mapping),
    ],
)
def test_build_mapping_entity_name(platform_name: str, builder: Any) -> None:
    """The optional literal name is carried onto the entity description."""

    desc = EntityDescriptor(
        platform=platform_name,
        dp_id=101,
        translation_key="some_entity",
        name="Custom Label",
    )
    built = cast(Any, builder)(desc)
    assert built.description.name == "Custom Label"


@pytest.mark.parametrize(
    ("platform_name", "builder"),
    [
        ("sensor", sensor._build_sensor_mapping),
        ("binary_sensor", binary_sensor._build_binary_sensor_mapping),
        ("button", button._build_button_mapping),
        ("switch", switch._build_switch_mapping),
        ("select", select._build_select_mapping),
        ("text", text._build_text_mapping),
        ("number", number._build_number_mapping),
        ("valve", valve._build_valve_mapping),
        ("cover", cover._build_cover_mapping),
        ("light", light._build_light_mapping),
        ("climate", climate._build_climate_mapping),
        ("lock", lock._build_lock_mapping),
    ],
)
def test_build_mapping_entity_name_none(platform_name: str, builder: Any) -> None:
    """Without a literal name the description has no override (translation used)."""

    built = cast(Any, builder)(
        EntityDescriptor(
            platform=platform_name,
            dp_id=101,
            translation_key="some_entity",
        )
    )
    assert built.description.name in (None, UNDEFINED)


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


def test_ggq_dual_water_timer_exposes_every_confirmed_datapoint() -> None:
    """Each confirmed BLE data point of the dual timer has a home."""
    device = cast(TuyaBLEDevice, FakeDevice("ggq", "fdrbxxbg"))

    assert [item.dp_id for item in sensor.get_mapping_by_device(device)] == [
        11,
        111,
        110,
        112,
        113,
        101,
        102,
    ]
    assert [item.dp_id for item in binary_sensor.get_mapping_by_device(device)] == [19]
    assert [item.dp_id for item in select.get_mapping_by_device(device)] == [117, 114]
    assert get_mapped_dp_ids("ggq", "fdrbxxbg") == frozenset({
        11,
        19,
        101,
        102,
        103,
        104,
        105,
        106,
        110,
        111,
        112,
        113,
        114,
        117,
    })


def test_ggq_dual_water_timer_status_sensors_are_restored() -> None:
    """Only the values the device cannot report after a restart are restored."""
    device = cast(TuyaBLEDevice, FakeDevice("ggq", "fdrbxxbg"))
    restored = {
        item.dp_id: item.restore for item in sensor.get_mapping_by_device(device)
    }

    assert restored == {
        11: True,
        111: True,
        110: True,
        112: True,
        113: True,
        101: True,
        102: True,
    }
    countdown = number.get_mapping_by_device(device)
    assert [item.dp_id for item in countdown] == [106, 103]
    assert all(item.getter is None for item in countdown)


@pytest.mark.parametrize("product_id", ["fdrbxxbg", "jntxv3q4", "qycalacn"])
def test_ggq_weather_delay_uses_the_captured_enum_codes(
    product_id: str,
) -> None:
    """The weather delay codes are read and written as enum indices."""
    device = cast(TuyaBLEDevice, FakeDevice("ggq", product_id))
    mappings = select.get_mapping_by_device(device)

    assert [item.dp_id for item in mappings] == [117, 114]
    for mapping in mappings:
        assert mapping.dp_type is TuyaBLEDataPointType.DT_ENUM
        assert mapping.values is None
        assert mapping.description.options == ["cancel", "24h", "48h", "72h"]


@pytest.mark.parametrize("product_id", ["nxquc5lb", "c8800fd30884068f", "so5ybnw9"])
def test_sfkzq_weather_delay_is_written_as_an_enum_index(
    product_id: str,
) -> None:
    """The weather delay is an enum, so the option index is written as the code.

    The ``sfkzq`` category definition types ``weather_delay`` as an ``Enum``
    over the same four options. Declaring it as a string table with a parallel
    ``values`` list meant ``select_option`` fed a non-numeric string into a data
    point the device had already reported as an enum, which raised
    ``TuyaBLEDataFormatError`` on serialisation instead of sending a packet.
    """
    device = cast(TuyaBLEDevice, FakeDevice("sfkzq", product_id))
    mappings = select.get_mapping_by_device(device)

    assert [item.dp_id for item in mappings] == [10]
    for mapping in mappings:
        assert mapping.dp_type is TuyaBLEDataPointType.DT_ENUM
        assert mapping.values is None
        assert mapping.description.options == ["cancel", "24h", "48h", "72h"]


def test_ggq_dual_water_timer_fault_sensor_is_a_problem() -> None:
    """The fault data point is a diagnostic problem binary sensor."""
    device = cast(TuyaBLEDevice, FakeDevice("ggq", "fdrbxxbg"))
    mapping = binary_sensor.get_mapping_by_device(device)[0]

    assert mapping.description.device_class is BinarySensorDeviceClass.PROBLEM
    assert mapping.description.entity_category is EntityCategory.DIAGNOSTIC


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
                    get_registry().get(category, product_id) is not None
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
