"""Audit tests for the PARKSIDE smart battery (``dcb``) descriptors.

Evidence behind the values locked in here (issue #175, refs #103):

* ``vllfabvs`` / ``r04rvmaq`` diagnostics (#103) report DP 3 as raw
  millivolts -- 19872 and 20017 on a 20 V pack at 82 % and 96 % -- although
  the cloud schema declares ``scale: 1``. Applying that scale would read
  1.99 V, so the descriptors keep ``mV`` with no coefficient.
* DP 101 on the 2026 packs is whole amperes: a drill load read "2 mA" while
  it was declared ``mA`` (#103), and the source fork's PR 283 multiplies the
  same raw value by 1000 to show milliamperes.
* DP 102 on the 2026 packs follows the reporter-observed order
  Charging, Discharging, Ready, Full, Sleep, Error (#103). An idle pack
  reports code 2, and the cloud names an idle ``vllfabvs`` pack "Ready". The
  source fork's PR 283 order (Charging, Ready, Sleep, ...) carries no device
  evidence and contradicts both, so it is not adopted.
* ``fay1puxy`` (12 Ah) comes only from PR 283, which lists it beside
  ``vllfabvs``. With no diagnostics of its own it maps read-only sensors and
  leaves out DP 102 and every writable data point.
* The previous-generation ``z5ztlw3k`` keeps its original DP 101 ``mA`` and
  DP 102 order: no diagnostics from that PID exist to confirm PR 283's claim
  that it matches the 2026 packs.
"""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.core import HomeAssistant
import pytest

from custom_components.tuya_ble import (
    binary_sensor,
    button,
    number,
    select,
    sensor,
    switch,
    text,
)
from custom_components.tuya_ble.device_registry import EntityDescriptor, get_registry
from custom_components.tuya_ble.entity import get_product_info_by_ids
from custom_components.tuya_ble.sensor import TuyaBLESensor
from custom_components.tuya_ble.tuya_ble import TuyaBLEDataPointType
from tests.conftest import add_dp, build_context, make_credentials

_PREVIOUS_GENERATION = ("z5ztlw3k", "ajrhf1aj")
_2026_GENERATION = ("vllfabvs", "r04rvmaq", "fab74vxu")
_12AH = "fay1puxy"
_READ_ONLY_12AH_SENSORS = [
    16,
    11,
    172,
    2,
    3,
    101,
    103,
    104,
    8,
    9,
    14,
    15,
    10,
    21,
    107,
    108,
    109,
    110,
    111,
    112,
    113,
    114,
    19,
    150,
    152,
    153,
    154,
    156,
    157,
    158,
    159,
    160,
]


def _sensor(product_id: str, dp_id: int) -> EntityDescriptor:
    """Return the registry descriptor of one ``dcb`` sensor data point."""
    product = get_registry().get("dcb", product_id)
    assert product is not None, f"dcb/{product_id} is not in the registry"
    return next(desc for desc in product.get("sensor") if desc.dp_id == dp_id)


async def _sensor_state(
    hass: HomeAssistant,
    product_id: str,
    dp_id: int,
    dp_type: TuyaBLEDataPointType,
    value: int,
) -> TuyaBLESensor:
    """Build the platform's sensor for a data point and push a device report."""
    device, coordinator, product = build_context(hass)
    device._device_info = make_credentials(  # pylint: disable=protected-access
        category="dcb", product_id=product_id
    )
    mapping = next(
        item for item in sensor.get_mapping_by_device(device) if item.dp_id == dp_id
    )
    entity = TuyaBLESensor(hass, coordinator, device, product, mapping)
    entity.hass = hass
    entity.entity_id = f"sensor.dcb_{product_id}_{dp_id}"
    await entity.async_added_to_hass()
    add_dp(device, dp_id, dp_type, value)
    coordinator.async_set_updated_data({})
    await hass.async_block_till_done()
    return entity


@pytest.mark.parametrize("product_id", _PREVIOUS_GENERATION)
def test_tool_current_declares_no_current_device_class(product_id: str) -> None:
    """DP 157's unit is unconfirmed, so it must not claim the current class.

    A ``current`` sensor without a unit is invalid in Home Assistant; the 2026
    descriptors already dropped the class, the previous generation had not.
    """
    tool_current = _sensor(product_id, 157)
    assert tool_current.translation_key == "tools_current"
    assert tool_current.device_class is None
    assert tool_current.unit is None
    assert tool_current.state_class == "measurement"


@pytest.mark.parametrize("product_id", _PREVIOUS_GENERATION)
async def test_tool_current_reads_as_a_plain_measurement(
    hass: HomeAssistant, product_id: str
) -> None:
    """The platform builds DP 157 as a class-less, unit-less measurement."""
    entity = await _sensor_state(
        hass, product_id, 157, TuyaBLEDataPointType.DT_VALUE, 1200
    )
    assert entity.device_class is None
    assert entity.native_unit_of_measurement is None
    assert entity.native_value == 1200.0


def test_previous_generation_keeps_its_unconfirmed_mappings() -> None:
    """``z5ztlw3k`` keeps DP 101 in mA and its original DP 102 order."""
    assert _sensor("z5ztlw3k", 101).unit == "mA"
    assert _sensor("z5ztlw3k", 102).options == [
        "Ready",
        "Charging",
        "Discharging",
        "Full",
        "Sleep",
        "Error",
    ]


@pytest.mark.parametrize(
    "product_id", (*_PREVIOUS_GENERATION, *_2026_GENERATION, _12AH)
)
def test_charge_current_and_voltage_are_unscaled(product_id: str) -> None:
    """DP 2 is mA and DP 3 is mV, both read without a coefficient."""
    charge_current = _sensor(product_id, 2)
    charge_voltage = _sensor(product_id, 3)
    assert (charge_current.unit, charge_current.coefficient) == ("mA", 1.0)
    assert (charge_voltage.unit, charge_voltage.coefficient) == ("mV", 1.0)


@pytest.mark.parametrize("product_id", (*_2026_GENERATION, _12AH))
async def test_charge_voltage_reads_the_reported_millivolts(
    hass: HomeAssistant, product_id: str
) -> None:
    """A captured 19872 reading stays 19872 mV rather than the cloud's 1987.2."""
    entity = await _sensor_state(
        hass, product_id, 3, TuyaBLEDataPointType.DT_VALUE, 19872
    )
    assert entity.device_class is SensorDeviceClass.VOLTAGE
    assert entity.native_unit_of_measurement == "mV"
    assert entity.native_value == 19872.0


@pytest.mark.parametrize("product_id", (*_2026_GENERATION, _12AH))
async def test_discharging_current_reads_whole_amperes(
    hass: HomeAssistant, product_id: str
) -> None:
    """DP 101 reports amperes, so a raw 2 is a 2 A drill load."""
    entity = await _sensor_state(
        hass, product_id, 101, TuyaBLEDataPointType.DT_VALUE, 2
    )
    assert entity.native_unit_of_measurement == "A"
    assert entity.native_value == 2.0


@pytest.mark.parametrize(
    ("code", "label"),
    [
        (0, "Charging"),
        (1, "Discharging"),
        (2, "Ready"),
        (3, "Full"),
        (4, "Sleep"),
        (5, "Error"),
        (6, None),
    ],
)
async def test_2026_battery_status_labels_follow_the_device_codes(
    hass: HomeAssistant, code: int, label: str | None
) -> None:
    """DP 102 codes map to the observed labels; unknown codes read as unknown."""
    entity = await _sensor_state(
        hass, "vllfabvs", 102, TuyaBLEDataPointType.DT_ENUM, code
    )
    assert entity.native_value == label


def test_12ah_battery_is_its_own_product() -> None:
    """``fay1puxy`` resolves to its own 12 Ah identity, not an alias of a 4/8 Ah."""
    product = get_registry().get("dcb", _12AH)
    assert product is not None
    assert product.manufacturer == "PARKSIDE"
    assert product.device_name == "PARKSIDE Smart battery 12Ah"
    assert product.model_name == _12AH
    for product_id in _2026_GENERATION:
        sibling = get_registry().get("dcb", product_id)
        assert sibling is not None
        assert sibling.device_name != product.device_name

    info = get_product_info_by_ids("dcb", _12AH)
    assert info is not None
    assert (info.name, info.manufacturer) == (
        "PARKSIDE Smart battery 12Ah",
        "PARKSIDE",
    )


def test_12ah_battery_maps_read_only_sensors(hass: HomeAssistant) -> None:
    """Only the sensors are mapped; DP 102 and all writable DPs are deferred."""
    device, _, _ = build_context(hass)
    device._device_info = make_credentials(  # pylint: disable=protected-access
        category="dcb", product_id=_12AH
    )
    assert [
        item.dp_id for item in sensor.get_mapping_by_device(device)
    ] == _READ_ONLY_12AH_SENSORS
    for platform in (binary_sensor, button, number, select, switch, text):
        assert platform.get_mapping_by_device(device) == [], platform.__name__


async def test_12ah_battery_reads_percentage_and_temperature(
    hass: HomeAssistant,
) -> None:
    """The standard DP 16 and DP 11 readings come through unscaled."""
    battery = await _sensor_state(hass, _12AH, 16, TuyaBLEDataPointType.DT_VALUE, 82)
    temperature = await _sensor_state(
        hass, _12AH, 11, TuyaBLEDataPointType.DT_VALUE, 24
    )
    assert (battery.native_value, battery.native_unit_of_measurement) == (82, "%")
    assert temperature.native_value == 24
