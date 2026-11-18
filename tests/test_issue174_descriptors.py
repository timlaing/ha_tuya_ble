"""Regression tests for the issue #174 timer, meter, light and diffuser descriptors."""
# pylint: disable=protected-access

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from homeassistant.components.binary_sensor.const import BinarySensorDeviceClass
from homeassistant.components.light.const import ColorMode
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.components.valve.const import ValveDeviceClass
from homeassistant.const import EntityCategory, UnitOfVolume, UnitOfVolumeFlowRate
from homeassistant.core import HomeAssistant
import pytest

from custom_components.tuya_ble import (
    binary_sensor,
    light,
    number,
    select,
    sensor,
    switch,
    valve,
)
from custom_components.tuya_ble.const import DOMAIN
from custom_components.tuya_ble.coordinator import TuyaBLECoordinator
from custom_components.tuya_ble.device_descriptors.handlers import water_valve
from custom_components.tuya_ble.device_registry import get_mapped_dp_ids, get_registry
from custom_components.tuya_ble.entity import (
    TuyaBLEProductInfo,
    get_device_info,
    get_device_product_info,
)
from custom_components.tuya_ble.tuya_ble import TuyaBLEDataPointType
from tests.conftest import (
    EntityDevice,
    add_dp,
    make_credentials,
    make_status_device,
)

_PLATFORMS: dict[str, Any] = {
    "binary_sensor": binary_sensor,
    "light": light,
    "number": number,
    "select": select,
    "sensor": sensor,
    "switch": switch,
    "valve": valve,
}

_Context = tuple[EntityDevice, TuyaBLECoordinator, TuyaBLEProductInfo]


def _context(hass: HomeAssistant, category: str, product_id: str) -> _Context:
    """Build a real device and coordinator resolved from the shipped descriptor."""
    device = make_status_device(device_cls=EntityDevice)
    device.initialize_with_credentials(
        make_credentials(category=category, product_id=product_id)
    )
    return device, TuyaBLECoordinator(hass, device), get_device_product_info(device)


async def _setup_platform(
    hass: HomeAssistant, ctx: _Context, platform: str
) -> list[Any]:
    """Create a platform's entities through its public ``async_setup_entry``."""
    device, coordinator, product = ctx
    entry = SimpleNamespace(entry_id="entry174")
    hass.data[DOMAIN] = {
        entry.entry_id: SimpleNamespace(
            device=device, coordinator=coordinator, product=product
        )
    }
    add_entities = MagicMock()
    await _PLATFORMS[platform].async_setup_entry(hass, entry, add_entities)
    entities: list[Any] = add_entities.call_args.args[0]
    for entity in entities:
        entity.hass = hass
        entity.async_write_ha_state = MagicMock()
    return entities


def _by_dp(entities: list[Any], dp_id: int) -> Any:
    """Return the single entity mapped to ``dp_id``."""
    matches = [entity for entity in entities if entity._mapping.dp_id == dp_id]
    assert len(matches) == 1
    return matches[0]


def _push(ctx: _Context, dp_id: int, dp_type: TuyaBLEDataPointType, value: Any) -> None:
    """Report a data point from the device and notify the coordinator."""
    device, coordinator, _product = ctx
    add_dp(device, dp_id, dp_type, value)
    coordinator.async_set_updated_data({})


def _sent(device: EntityDevice, dp_id: int) -> tuple[TuyaBLEDataPointType, Any]:
    """Return the wire type and value last written to ``dp_id``."""
    datapoint = device.datapoints[dp_id]
    assert datapoint is not None
    return datapoint.dp_type, datapoint.value


_EXPECTED = {
    ("sfkzq", "d4vpmigg"): (
        "YZD05 water valve",
        None,
        {1, 4, 7, 9, 10, 11, 12, 13, 15},
    ),
    ("sfkzq", "ojrvmfkk"): (
        "WT-04W water timer",
        "Unistyle",
        {1, 10, 11, 12, 14, 101, 103, 121, 122, 123, 124},
    ),
    ("sfkzq", "8t5hebn0"): (
        "Water timer",
        "MoistenLand",
        {1, 4, 7, 8, 10, 11, 12, 15},
    ),
    ("slj", "mqqna0px"): (
        "FML026A water meter",
        "RESTMO",
        {1, 2, 4, 5, 101, 107, 108},
    ),
    ("dj", "bpqbwf8y"): ("LED BULB B509Z2", None, {20, 21, 22, 23}),
    ("jsq", "if1nolcm"): (
        "DT-T2190A aroma diffuser",
        "Dituo",
        {1, 2, 3, 5, 6, 7, 9, 12, 101},
    ),
}


@pytest.mark.parametrize(("category", "product_id"), sorted(_EXPECTED))
def test_descriptor_metadata_and_mapped_dps(category: str, product_id: str) -> None:
    """Each product resolves with its name, maker and only the evidenced DPs."""
    name, manufacturer, dp_ids = _EXPECTED[category, product_id]
    descriptor = get_registry().get(category, product_id)
    assert descriptor is not None
    assert descriptor.device_name == name
    assert descriptor.manufacturer == manufacturer
    assert descriptor.model_name == product_id
    assert get_mapped_dp_ids(category, product_id) == dp_ids


async def test_device_info_uses_descriptor_manufacturer(hass: HomeAssistant) -> None:
    """The device registry entry carries the descriptor's manufacturer."""
    device, _coordinator, _product = _context(hass, "slj", "mqqna0px")
    info = get_device_info(device)
    assert info is not None
    assert info["manufacturer"] == "RESTMO"


def test_e1poaiwa_stays_unregistered() -> None:
    """The Rainpoint TTV102B is deferred until model-specific DP evidence exists."""
    assert get_registry().get("sfkzq", "e1poaiwa") is None
    assert get_mapped_dp_ids("sfkzq", "e1poaiwa") == frozenset()


@pytest.mark.parametrize(
    ("product_id", "is_valve"),
    [("d4vpmigg", True), ("8t5hebn0", True), ("ojrvmfkk", False)],
)
def test_water_valve_identity_for_shared_handler(
    hass: HomeAssistant, product_id: str, is_valve: bool
) -> None:
    """Single-outlet timers are water valves; the dual-zone timer is not."""
    _device, _coordinator, product = _context(hass, "sfkzq", product_id)
    assert product.is_water_valve is is_valve
    assert water_valve.is_water_valve_in_switch_mode(MagicMock(), product) is is_valve


# --- sfkzq/d4vpmigg ---------------------------------------------------------


async def test_d4vpmigg_valve_switch_and_countdown_writes(hass: HomeAssistant) -> None:
    """DP1 is a BOOL valve and DP11 a 0-86400 s countdown."""
    ctx = _context(hass, "sfkzq", "d4vpmigg")
    device = ctx[0]
    (valve_entity,) = await _setup_platform(hass, ctx, "valve")
    assert valve_entity.device_class == ValveDeviceClass.WATER
    assert valve_entity.is_closed is None
    valve_entity.open_valve()
    await hass.async_block_till_done()
    assert _sent(device, 1) == (TuyaBLEDataPointType.DT_BOOL, True)
    assert valve_entity.is_closed is False
    valve_entity.close_valve()
    await hass.async_block_till_done()
    assert _sent(device, 1) == (TuyaBLEDataPointType.DT_BOOL, False)

    (switch_entity,) = await _setup_platform(hass, ctx, "switch")
    assert switch_entity.entity_description.key == "water_valve"
    assert switch_entity.available is False  # Coordinator not connected yet.

    (countdown,) = await _setup_platform(hass, ctx, "number")
    assert (countdown.native_min_value, countdown.native_max_value) == (0, 86400)
    assert countdown.native_unit_of_measurement == "s"
    countdown.set_native_value(600)
    await hass.async_block_till_done()
    assert _sent(device, 11) == (TuyaBLEDataPointType.DT_VALUE, 600)
    assert device.sent == [[1], [1], [11]]


@pytest.mark.parametrize(
    ("dp_id", "option", "index"),
    [(10, "cancel", 0), (10, "72h", 3), (13, "sunny", 0), (13, "rainy", 2)],
)
async def test_d4vpmigg_enum_selects_write_cloud_range_index(
    hass: HomeAssistant, dp_id: int, option: str, index: int
) -> None:
    """Weather delay and smart weather write the cloud range index as ENUM."""
    ctx = _context(hass, "sfkzq", "d4vpmigg")
    entity = _by_dp(await _setup_platform(hass, ctx, "select"), dp_id)
    assert entity.entity_category == EntityCategory.CONFIG
    entity.select_option(option)
    await hass.async_block_till_done()
    assert _sent(ctx[0], dp_id) == (TuyaBLEDataPointType.DT_ENUM, index)
    assert entity.current_option == option


async def test_d4vpmigg_select_ignores_unknown_option(hass: HomeAssistant) -> None:
    """An option outside the declared range is never written."""
    ctx = _context(hass, "sfkzq", "d4vpmigg")
    entity = _by_dp(await _setup_platform(hass, ctx, "select"), 10)
    entity.select_option("96h")
    await hass.async_block_till_done()
    assert ctx[0].datapoints[10] is None
    assert not ctx[0].sent


async def test_d4vpmigg_sensors_read_and_reject_unknown_enum(
    hass: HomeAssistant,
) -> None:
    """Report-only DPs decode, and an out-of-range work state is unknown."""
    ctx = _context(hass, "sfkzq", "d4vpmigg")
    entities = await _setup_platform(hass, ctx, "sensor")
    for entity in entities:
        await entity.async_added_to_hass()
    work_state, accumulated = _by_dp(entities, 12), _by_dp(entities, 9)
    assert accumulated.state_class == SensorStateClass.TOTAL_INCREASING
    assert accumulated.device_class == SensorDeviceClass.DURATION
    _push(ctx, 7, TuyaBLEDataPointType.DT_VALUE, 100)
    _push(ctx, 9, TuyaBLEDataPointType.DT_VALUE, 739)
    _push(ctx, 15, TuyaBLEDataPointType.DT_VALUE, 1)
    _push(ctx, 12, TuyaBLEDataPointType.DT_ENUM, 2)
    assert _by_dp(entities, 7).native_value == 100
    assert accumulated.native_value == 739
    assert _by_dp(entities, 15).native_value == 1
    assert work_state.native_value == "idle"
    _push(ctx, 12, TuyaBLEDataPointType.DT_ENUM, 3)
    assert work_state.native_value is None


@pytest.mark.parametrize(
    ("product_id", "payload", "is_on"),
    [("d4vpmigg", b"\x00", False), ("d4vpmigg", b"\x01", True), ("8t5hebn0", 0, False)],
)
async def test_timer_fault_bitmap(
    hass: HomeAssistant, product_id: str, payload: bytes | int, is_on: bool
) -> None:
    """A zero fault bitmap is healthy and any set bit is a problem."""
    ctx = _context(hass, "sfkzq", product_id)
    (fault,) = await _setup_platform(hass, ctx, "binary_sensor")
    assert fault.device_class == BinarySensorDeviceClass.PROBLEM
    assert fault._mapping.dp_type == TuyaBLEDataPointType.DT_BITMAP
    await fault.async_added_to_hass()
    _push(ctx, 4, TuyaBLEDataPointType.DT_BITMAP, payload)
    assert fault.is_on is is_on


# --- sfkzq/ojrvmfkk ---------------------------------------------------------


async def test_ojrvmfkk_zones_are_independent(hass: HomeAssistant) -> None:
    """Zone 1 drives DP1/DP11 and zone 2 DP121/DP122, without crosstalk."""
    ctx = _context(hass, "sfkzq", "ojrvmfkk")
    device = ctx[0]
    valves = await _setup_platform(hass, ctx, "valve")
    assert [v.entity_description.key for v in valves] == ["valve_zone1", "valve_zone2"]
    _by_dp(valves, 121).open_valve()
    await hass.async_block_till_done()
    assert _sent(device, 121) == (TuyaBLEDataPointType.DT_BOOL, True)
    assert device.datapoints[1] is None

    numbers = await _setup_platform(hass, ctx, "number")
    _by_dp(numbers, 11).set_native_value(30)
    _by_dp(numbers, 122).set_native_value(86400)
    await hass.async_block_till_done()
    assert _sent(device, 11) == (TuyaBLEDataPointType.DT_VALUE, 30)
    assert _sent(device, 122) == (TuyaBLEDataPointType.DT_VALUE, 86400)

    switches = await _setup_platform(hass, ctx, "switch")
    assert [s._mapping.dp_id for s in switches] == [1, 121, 14]
    assert _by_dp(switches, 14).entity_category == EntityCategory.CONFIG


async def test_ojrvmfkk_weather_delay_and_status(hass: HomeAssistant) -> None:
    """The 1-7 day delay writes its index; per-zone status decodes five states."""
    ctx = _context(hass, "sfkzq", "ojrvmfkk")
    (delay,) = await _setup_platform(hass, ctx, "select")
    delay.select_option("7day")
    await hass.async_block_till_done()
    assert _sent(ctx[0], 10) == (TuyaBLEDataPointType.DT_ENUM, 7)

    sensors = await _setup_platform(hass, ctx, "sensor")
    for entity in sensors:
        await entity.async_added_to_hass()
    _push(ctx, 12, TuyaBLEDataPointType.DT_ENUM, 3)
    _push(ctx, 123, TuyaBLEDataPointType.DT_ENUM, 4)
    _push(ctx, 103, TuyaBLEDataPointType.DT_VALUE, 120)
    _push(ctx, 101, TuyaBLEDataPointType.DT_VALUE, 80)
    assert _by_dp(sensors, 12).native_value == "spray"
    assert _by_dp(sensors, 123).native_value == "delay"
    assert _by_dp(sensors, 103).native_value == 120
    assert _by_dp(sensors, 124).native_value is None
    assert _by_dp(sensors, 101).native_value == 80


# --- sfkzq/8t5hebn0 ---------------------------------------------------------


async def test_8t5hebn0_controls_and_battery_state(hass: HomeAssistant) -> None:
    """Countdown and weather delay write; battery state decodes ``middle``."""
    ctx = _context(hass, "sfkzq", "8t5hebn0")
    device = ctx[0]
    (countdown,) = await _setup_platform(hass, ctx, "number")
    countdown.set_native_value(0)
    (delay,) = await _setup_platform(hass, ctx, "select")
    delay.select_option("48h")
    await hass.async_block_till_done()
    assert _sent(device, 11) == (TuyaBLEDataPointType.DT_VALUE, 0)
    assert _sent(device, 10) == (TuyaBLEDataPointType.DT_ENUM, 2)

    sensors = await _setup_platform(hass, ctx, "sensor")
    battery_state = _by_dp(sensors, 8)
    await battery_state.async_added_to_hass()
    _push(ctx, 8, TuyaBLEDataPointType.DT_ENUM, 1)
    assert battery_state.native_value == "middle"
    _push(ctx, 8, TuyaBLEDataPointType.DT_ENUM, 5)
    assert battery_state.native_value is None


# --- slj/mqqna0px -----------------------------------------------------------


_METER_COUNTERS = (1, 2, 101, 107, 108)


async def test_mqqna0px_classes_units_and_scaling(hass: HomeAssistant) -> None:
    """Counters are total_increasing litres and every reading is scaled by 0.1."""
    ctx = _context(hass, "slj", "mqqna0px")
    entities = await _setup_platform(hass, ctx, "sensor")
    # The RSSI diagnostic is always first; the descriptor adds seven more.
    assert len(entities) == 8
    for entity in entities:
        await entity.async_added_to_hass()

    flow = _by_dp(entities, 5)
    assert flow.device_class == SensorDeviceClass.VOLUME_FLOW_RATE
    assert flow.state_class == SensorStateClass.MEASUREMENT
    assert flow.native_unit_of_measurement == UnitOfVolumeFlowRate.LITERS_PER_MINUTE
    _push(ctx, 5, TuyaBLEDataPointType.DT_VALUE, 125)
    assert flow.native_value == pytest.approx(12.5)

    for dp_id in _METER_COUNTERS:
        counter = _by_dp(entities, dp_id)
        assert counter.device_class == SensorDeviceClass.WATER
        assert counter.state_class == SensorStateClass.TOTAL_INCREASING
        assert counter.native_unit_of_measurement == UnitOfVolume.LITERS
        assert counter.suggested_display_precision == 1
        _push(ctx, dp_id, TuyaBLEDataPointType.DT_VALUE, 12345)
        assert counter.native_value == pytest.approx(1234.5)

    battery = _by_dp(entities, 4)
    _push(ctx, 4, TuyaBLEDataPointType.DT_VALUE, 87)
    assert battery.native_value == 87
    assert battery.entity_category == EntityCategory.DIAGNOSTIC


async def test_mqqna0px_session_counter_restart_and_invalid_payload(
    hass: HomeAssistant,
) -> None:
    """A session restart reads as zero; a non-integer payload keeps the last value."""
    ctx = _context(hass, "slj", "mqqna0px")
    session = _by_dp(await _setup_platform(hass, ctx, "sensor"), 2)
    await session.async_added_to_hass()
    _push(ctx, 2, TuyaBLEDataPointType.DT_VALUE, 50)
    assert session.native_value == pytest.approx(5.0)
    _push(ctx, 2, TuyaBLEDataPointType.DT_VALUE, 0)
    assert session.native_value == 0
    _push(ctx, 2, TuyaBLEDataPointType.DT_VALUE, 30)
    _push(ctx, 2, TuyaBLEDataPointType.DT_VALUE, "bad")
    assert session.native_value == pytest.approx(3.0)


@pytest.mark.parametrize("platform", ["switch", "number", "select", "valve"])
async def test_mqqna0px_exposes_no_actuators(
    hass: HomeAssistant, platform: str
) -> None:
    """No reset command is documented, so the meter has nothing to write."""
    ctx = _context(hass, "slj", "mqqna0px")
    assert await _setup_platform(hass, ctx, platform) == []


# --- dj/bpqbwf8y ------------------------------------------------------------


async def test_bpqbwf8y_is_tunable_white_only(hass: HomeAssistant) -> None:
    """The bulb has no colour_data, so only colour temperature is offered."""
    ctx = _context(hass, "dj", "bpqbwf8y")
    (bulb,) = await _setup_platform(hass, ctx, "light")
    assert bulb.entity_description.key == "switch_led"
    assert bulb.supported_color_modes == {ColorMode.COLOR_TEMP}
    assert bulb.color_mode == ColorMode.COLOR_TEMP
    _push(ctx, 21, TuyaBLEDataPointType.DT_ENUM, 1)  # colour: not supported here.
    assert bulb.color_mode == ColorMode.COLOR_TEMP
    assert bulb.hs_color is None
    assert await _setup_platform(hass, ctx, "number") == []


async def test_bpqbwf8y_writes_standard_v2_dps(hass: HomeAssistant) -> None:
    """Brightness scales to 10-1000 and colour temperature selects white mode."""
    ctx = _context(hass, "dj", "bpqbwf8y")
    device = ctx[0]
    (bulb,) = await _setup_platform(hass, ctx, "light")
    bulb.turn_on(brightness=255, color_temp_kelvin=6500, hs_color=(0.0, 100.0))
    await hass.async_block_till_done()
    assert _sent(device, 20) == (TuyaBLEDataPointType.DT_BOOL, True)
    assert _sent(device, 21) == (TuyaBLEDataPointType.DT_ENUM, 0)
    assert _sent(device, 22) == (TuyaBLEDataPointType.DT_VALUE, 1000)
    assert _sent(device, 23)[0] == TuyaBLEDataPointType.DT_VALUE
    assert device.datapoints[24] is None
    bulb.turn_on(brightness=0)
    await hass.async_block_till_done()
    assert _sent(device, 22) == (TuyaBLEDataPointType.DT_VALUE, 10)


async def test_dj_category_default_still_offers_colour(hass: HomeAssistant) -> None:
    """Other unregistered dj lights keep the RGB-capable category default."""
    ctx = _context(hass, "dj", "unknownpid")
    (default_light,) = await _setup_platform(hass, ctx, "light")
    assert ColorMode.HS in default_light.supported_color_modes
    assert default_light._mapping.color_data_dp_id == 24


# --- jsq/if1nolcm -----------------------------------------------------------


async def test_if1nolcm_switches_selects_and_sensors(hass: HomeAssistant) -> None:
    """Power and mist are BOOLs; mode, timer and scene write ENUM indices."""
    ctx = _context(hass, "jsq", "if1nolcm")
    device = ctx[0]
    switches = await _setup_platform(hass, ctx, "switch")
    assert [s.entity_description.key for s in switches] == ["switch", "switch_spray"]

    selects = await _setup_platform(hass, ctx, "select")
    for dp_id, option, index in ((3, "large", 1), (5, "6h", 3), (101, "dazzle", 6)):
        _by_dp(selects, dp_id).select_option(option)
        await hass.async_block_till_done()
        assert _sent(device, dp_id) == (TuyaBLEDataPointType.DT_ENUM, index)
    _by_dp(selects, 5).select_option("12h")
    await hass.async_block_till_done()
    assert _sent(device, 5) == (TuyaBLEDataPointType.DT_ENUM, 3)

    sensors = await _setup_platform(hass, ctx, "sensor")
    remaining = _by_dp(sensors, 6)
    await remaining.async_added_to_hass()
    _push(ctx, 6, TuyaBLEDataPointType.DT_VALUE, 45)
    assert remaining.native_value == 45
    assert remaining.native_unit_of_measurement == "min"

    (fault,) = await _setup_platform(hass, ctx, "binary_sensor")
    assert fault._mapping.dp_id == 12


async def test_if1nolcm_light_is_on_off_and_brightness_only(
    hass: HomeAssistant,
) -> None:
    """DP7/DP9 drive the light; the non-standard work mode/colour DPs are unused."""
    ctx = _context(hass, "jsq", "if1nolcm")
    device = ctx[0]
    (lamp,) = await _setup_platform(hass, ctx, "light")
    assert lamp.supported_color_modes == {ColorMode.BRIGHTNESS}
    assert lamp.color_mode == ColorMode.BRIGHTNESS
    assert lamp.is_on is False
    lamp.turn_on(brightness=255, hs_color=(120.0, 100.0))
    await hass.async_block_till_done()
    assert _sent(device, 7) == (TuyaBLEDataPointType.DT_BOOL, True)
    assert _sent(device, 9) == (TuyaBLEDataPointType.DT_VALUE, 100)
    assert device.datapoints[8] is None
    assert device.datapoints[10] is None
    _push(ctx, 9, TuyaBLEDataPointType.DT_VALUE, 1)
    assert lamp.brightness == 0
    lamp.turn_off()
    await hass.async_block_till_done()
    assert _sent(device, 7) == (TuyaBLEDataPointType.DT_BOOL, False)


def test_battery_state_middle_is_translated() -> None:
    """The ``middle`` battery state used by several timers has a label."""
    root = Path(sensor.__file__).parent
    for name in ("strings.json", "translations/en.json"):
        strings = json.loads((root / name).read_text(encoding="utf-8"))
        states = strings["entity"]["sensor"]["battery_state"]["state"]
        assert states["middle"] == "Medium"
