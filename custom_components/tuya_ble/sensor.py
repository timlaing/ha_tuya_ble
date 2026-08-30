"""Sensor platform for Tuya BLE devices (temperature, humidity, battery, RSSI)."""

# pylint: disable=too-many-lines

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfRatio,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolume,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    BATTERY_CHARGED,
    BATTERY_CHARGING,
    BATTERY_NOT_CHARGING,
    BATTERY_STATE_HIGH,
    BATTERY_STATE_LOW,
    BATTERY_STATE_NORMAL,
    CO2_LEVEL_ALARM,
    CO2_LEVEL_NORMAL,
    DOMAIN,
)
from .devices import TuyaBLECoordinator, TuyaBLEData, TuyaBLEEntity, TuyaBLEProductInfo
from .tuya_ble import TuyaBLEDataPoint, TuyaBLEDataPointType, TuyaBLEDevice

SIGNAL_STRENGTH_DP_ID = -1

ICON_BATTERY = "mdi:battery"
ICON_BATTERY_CHECK = "mdi:battery-check"
ICON_COUNTER = "mdi:counter"
ICON_FINGERPRINT = "mdi:fingerprint"


TuyaBLESensorIsAvailable = Callable[["TuyaBLESensor", TuyaBLEProductInfo], bool] | None


@dataclass
class TuyaBLESensorMapping:
    """Map a Tuya datapoint to a Home Assistant sensor entity."""

    dp_id: int
    description: SensorEntityDescription
    force_add: bool = True
    dp_type: TuyaBLEDataPointType | None = None
    getter: Callable[[TuyaBLESensor], None] | None = None
    coefficient: float = 1.0
    icons: list[str] | None = None
    is_available: TuyaBLESensorIsAvailable = None


@dataclass
class TuyaBLEBatteryMapping(TuyaBLESensorMapping):
    """Sensor mapping with default battery entity description."""

    description: SensorEntityDescription = field(
        default_factory=lambda: SensorEntityDescription(
            key="battery",
            device_class=SensorDeviceClass.BATTERY,
            native_unit_of_measurement=PERCENTAGE,
            entity_category=EntityCategory.DIAGNOSTIC,
            state_class=SensorStateClass.MEASUREMENT,
        )
    )


@dataclass
class TuyaBLETemperatureMapping(TuyaBLESensorMapping):
    """Sensor mapping with default temperature entity description."""

    description: SensorEntityDescription = field(
        default_factory=lambda: SensorEntityDescription(
            key="temperature",
            device_class=SensorDeviceClass.TEMPERATURE,
            native_unit_of_measurement=UnitOfTemperature.CELSIUS,
            state_class=SensorStateClass.MEASUREMENT,
        )
    )


def is_co2_alarm_enabled(sensor: TuyaBLESensor, product: TuyaBLEProductInfo) -> bool:
    """Check if CO2 alarm is enabled by reading datapoint 13."""
    result: bool = True
    datapoint = sensor.device.datapoints[13]
    if datapoint:
        result = bool(datapoint.value)
    return result


def battery_enum_getter(sensor: TuyaBLESensor) -> None:
    """Read battery enum datapoint and convert to percentage."""
    datapoint = sensor.device.datapoints[104]
    if datapoint and isinstance(datapoint.value, int):
        sensor.set_native_value(datapoint.value * 20.0)


@dataclass
class TuyaBLECategorySensorMapping:
    """Hold product-specific and category-level sensor mappings."""

    products: dict[str, list[TuyaBLESensorMapping]] | None = None
    mapping: list[TuyaBLESensorMapping] | None = None


mapping: dict[str, TuyaBLECategorySensorMapping] = {
    "co2bj": TuyaBLECategorySensorMapping(
        products={
            "59s19z5m": [  # CO2 Detector
                TuyaBLESensorMapping(
                    dp_id=1,
                    description=SensorEntityDescription(
                        key="carbon_dioxide_alarm",
                        icon="mdi:molecule-co2",
                        device_class=SensorDeviceClass.ENUM,
                        options=[
                            CO2_LEVEL_ALARM,
                            CO2_LEVEL_NORMAL,
                        ],
                    ),
                    is_available=is_co2_alarm_enabled,
                ),
                TuyaBLESensorMapping(
                    dp_id=2,
                    description=SensorEntityDescription(
                        key="carbon_dioxide",
                        device_class=SensorDeviceClass.CO2,
                        native_unit_of_measurement=UnitOfRatio.PARTS_PER_MILLION,
                        state_class=SensorStateClass.MEASUREMENT,
                    ),
                ),
                TuyaBLEBatteryMapping(dp_id=15),
                TuyaBLETemperatureMapping(dp_id=18),
                TuyaBLESensorMapping(
                    dp_id=19,
                    description=SensorEntityDescription(
                        key="humidity",
                        device_class=SensorDeviceClass.HUMIDITY,
                        native_unit_of_measurement=PERCENTAGE,
                        state_class=SensorStateClass.MEASUREMENT,
                    ),
                ),
            ]
        }
    ),
    "ms": TuyaBLECategorySensorMapping(
        products={
            **{
                k: [
                    TuyaBLESensorMapping(
                        dp_id=21,
                        description=SensorEntityDescription(
                            key="alarm_lock",
                            device_class=SensorDeviceClass.ENUM,
                            options=[
                                "wrong_finger",
                                "wrong_password",
                                "wrong_card",
                                "wrong_face",
                                "tongue_bad",
                                "too_hot",
                                "unclosed_time",
                                "tongue_not_out",
                                "pry",
                                "key_in",
                                "low_battery",
                                "power_off",
                                "shock",
                            ],
                        ),
                    ),
                    TuyaBLEBatteryMapping(dp_id=8),
                    TuyaBLESensorMapping(
                        dp_id=40,
                        description=SensorEntityDescription(
                            key="lock_door_status",
                            entity_category=EntityCategory.DIAGNOSTIC,
                            device_class=SensorDeviceClass.ENUM,
                            options=[
                                "door_status_unknown",
                                "door_status_open",
                                "door_status_closed",
                            ],
                        ),
                    ),
                ]
                for k in [
                    "ludzroix",
                    "isk2p555",
                    "gumrixyt",
                    "uamrw6h3",
                    "okkyfgfs",
                    "sidhzylo",
                    "bvclwu9b",
                    "k53ok3u9",
                ]
            },
            "mqc2hevy": [  # Smart Lock - YSG_T8_8G_htr
                TuyaBLESensorMapping(
                    dp_id=21,
                    description=SensorEntityDescription(
                        key="alarm_lock",
                        icon="mdi:alert",
                        device_class=SensorDeviceClass.ENUM,
                        options=[
                            "wrong_finger",
                            "wrong_password",
                            "low_battery",
                        ],
                    ),
                ),
                TuyaBLEBatteryMapping(dp_id=8),
                TuyaBLESensorMapping(
                    dp_id=19,
                    description=SensorEntityDescription(
                        key="unlock_ble",
                        icon="mdi:bluetooth",
                        suggested_display_precision=0,
                        entity_category=EntityCategory.DIAGNOSTIC,
                    ),
                ),
                TuyaBLESensorMapping(
                    dp_id=12,
                    description=SensorEntityDescription(
                        key="unlock_fingerprint",
                        icon=ICON_FINGERPRINT,
                        suggested_display_precision=0,
                        entity_category=EntityCategory.DIAGNOSTIC,
                    ),
                ),
                TuyaBLESensorMapping(
                    dp_id=62,
                    description=SensorEntityDescription(
                        key="unlock_phone_remote",
                        icon="mdi:cellphone-lock",
                        suggested_display_precision=0,
                        entity_category=EntityCategory.DIAGNOSTIC,
                    ),
                ),
                TuyaBLESensorMapping(
                    dp_id=13,
                    description=SensorEntityDescription(
                        key="unlock_password",
                        icon="mdi:numeric-0-box-multiple-outline",
                        suggested_display_precision=0,
                        entity_category=EntityCategory.DIAGNOSTIC,
                    ),
                ),
                TuyaBLESensorMapping(
                    dp_id=14,
                    description=SensorEntityDescription(
                        key="unlock_dynamic",
                        icon="mdi:lock-reset",
                        suggested_display_precision=0,
                        entity_category=EntityCategory.DIAGNOSTIC,
                    ),
                ),
            ],
            "a6nttc41": [  # ORION Smart Lock
                TuyaBLEBatteryMapping(dp_id=8),
                TuyaBLESensorMapping(
                    dp_id=19,
                    description=SensorEntityDescription(
                        key="unlock_ble",
                        icon="mdi:bluetooth",
                        suggested_display_precision=0,
                        entity_category=EntityCategory.DIAGNOSTIC,
                    ),
                ),
                TuyaBLESensorMapping(
                    dp_id=12,
                    description=SensorEntityDescription(
                        key="unlock_fingerprint",
                        icon=ICON_FINGERPRINT,
                        suggested_display_precision=0,
                        entity_category=EntityCategory.DIAGNOSTIC,
                    ),
                ),
            ],
        }
    ),
    "jtmspro": TuyaBLECategorySensorMapping(
        products={
            **{
                k: [
                    TuyaBLESensorMapping(
                        dp_id=21,
                        description=SensorEntityDescription(
                            key="alarm_lock",
                            device_class=SensorDeviceClass.ENUM,
                            options=[
                                "wrong_finger",
                                "wrong_password",
                                "wrong_card",
                                "wrong_face",
                                "tongue_bad",
                                "too_hot",
                                "unclosed_time",
                                "tongue_not_out",
                                "pry",
                                "key_in",
                                "low_battery",
                                "power_off",
                                "shock",
                            ],
                        ),
                    ),
                    TuyaBLESensorMapping(
                        dp_id=12,
                        description=SensorEntityDescription(
                            key="unlock_fingerprint", icon=ICON_FINGERPRINT
                        ),
                    ),
                    TuyaBLESensorMapping(
                        dp_id=15,
                        description=SensorEntityDescription(
                            key="unlock_card", icon="mdi:nfc-variant"
                        ),
                    ),
                    TuyaBLESensorMapping(
                        dp_id=13,
                        description=SensorEntityDescription(
                            key="unlock_password", icon="mdi:keyboard-outline"
                        ),
                    ),
                    TuyaBLEBatteryMapping(dp_id=8),
                ]
                for k in [
                    "xicdxood",
                    "rlyxv7pe",
                    "oyqux5vv",
                    "ajk32biq",
                    "z7lj676i",
                    "hs21i377",
                ]
            },
        }
    ),
    "szjqr": TuyaBLECategorySensorMapping(
        products={
            **{
                k: [
                    TuyaBLESensorMapping(
                        dp_id=7,
                        description=SensorEntityDescription(
                            key="battery_charging",
                            device_class=SensorDeviceClass.ENUM,
                            entity_category=EntityCategory.DIAGNOSTIC,
                            options=[
                                BATTERY_NOT_CHARGING,
                                BATTERY_CHARGING,
                                BATTERY_CHARGED,
                            ],
                        ),
                        icons=[
                            ICON_BATTERY,
                            "mdi:power-plug-battery",
                            ICON_BATTERY_CHECK,
                        ],
                    ),
                    TuyaBLEBatteryMapping(dp_id=8),
                ]
                for k in ["3yqdo5yt", "xhf790if"]
            },
            **{
                k: [TuyaBLEBatteryMapping(dp_id=12)]
                for k in [
                    "blliqpsj",
                    "ndvkgsrm",
                    "yiihr7zh",
                    "neq16kgd",
                    "6jcvqwh0",
                    "riecov42",
                    "h8kdwywx",
                ]
            },
            **{
                k: [TuyaBLEBatteryMapping(dp_id=12)]
                for k in [
                    "ltak7e1p",
                    "y6kttvd6",
                    "yrnk7mnn",
                    "nvr2rocq",
                    "bnt7wajf",
                    "rvdceqjh",
                    "5xhbk964",
                ]
            },
        },
    ),
    "kg": TuyaBLECategorySensorMapping(
        products={
            **{k: [TuyaBLEBatteryMapping(dp_id=105)] for k in ["mknd4lci", "riecov42"]},
        },
    ),
    "wsdcg": TuyaBLECategorySensorMapping(
        products={
            "ojzlzzsw": [  # Soil moisture sensor
                TuyaBLETemperatureMapping(
                    dp_id=1,
                    coefficient=10.0,
                ),
                TuyaBLESensorMapping(
                    dp_id=2,
                    description=SensorEntityDescription(
                        key="moisture",
                        device_class=SensorDeviceClass.MOISTURE,
                        native_unit_of_measurement=PERCENTAGE,
                        state_class=SensorStateClass.MEASUREMENT,
                    ),
                ),
                TuyaBLESensorMapping(
                    dp_id=3,
                    description=SensorEntityDescription(
                        key="battery_state",
                        icon=ICON_BATTERY,
                        device_class=SensorDeviceClass.ENUM,
                        entity_category=EntityCategory.DIAGNOSTIC,
                        options=[
                            BATTERY_STATE_LOW,
                            BATTERY_STATE_NORMAL,
                            BATTERY_STATE_HIGH,
                        ],
                    ),
                    icons=[
                        "mdi:battery-alert",
                        "mdi:battery-50",
                        ICON_BATTERY_CHECK,
                    ],
                ),
                TuyaBLEBatteryMapping(dp_id=4),
            ],
            "iv7hudlj": [  # Bluetooth Temperature Humidity Sensor
                TuyaBLETemperatureMapping(
                    dp_id=1,
                    coefficient=10.0,
                    description=SensorEntityDescription(
                        key="va_temperature",
                        device_class=SensorDeviceClass.TEMPERATURE,
                        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
                        state_class=SensorStateClass.MEASUREMENT,
                    ),
                ),
                TuyaBLESensorMapping(
                    dp_id=2,
                    description=SensorEntityDescription(
                        key="va_moisture",
                        device_class=SensorDeviceClass.MOISTURE,
                        native_unit_of_measurement=PERCENTAGE,
                        state_class=SensorStateClass.MEASUREMENT,
                    ),
                ),
                TuyaBLEBatteryMapping(
                    dp_id=4,
                    description=SensorEntityDescription(
                        key="battery_percentage",
                        device_class=SensorDeviceClass.BATTERY,
                        native_unit_of_measurement=PERCENTAGE,
                        entity_category=EntityCategory.DIAGNOSTIC,
                        state_class=SensorStateClass.MEASUREMENT,
                    ),
                ),
            ],
            "jm6iasmb": [  # Bluetooth Temperature Humidity Sensor
                TuyaBLETemperatureMapping(
                    dp_id=1,
                    coefficient=10.0,
                    description=SensorEntityDescription(
                        key="va_temperature",
                        device_class=SensorDeviceClass.TEMPERATURE,
                        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
                        state_class=SensorStateClass.MEASUREMENT,
                    ),
                ),
                TuyaBLESensorMapping(
                    dp_id=2,
                    description=SensorEntityDescription(
                        key="va_moisture",
                        device_class=SensorDeviceClass.MOISTURE,
                        native_unit_of_measurement=PERCENTAGE,
                        state_class=SensorStateClass.MEASUREMENT,
                    ),
                ),
                TuyaBLEBatteryMapping(
                    dp_id=4,
                    description=SensorEntityDescription(
                        key="battery_percentage",
                        device_class=SensorDeviceClass.BATTERY,
                        native_unit_of_measurement=PERCENTAGE,
                        entity_category=EntityCategory.DIAGNOSTIC,
                        state_class=SensorStateClass.MEASUREMENT,
                    ),
                ),
            ],
            "tv6peegl": [  # Soil Thermo-Hygrometer
                TuyaBLETemperatureMapping(dp_id=101),
                TuyaBLESensorMapping(
                    dp_id=102,
                    description=SensorEntityDescription(
                        key="moisture",
                        device_class=SensorDeviceClass.MOISTURE,
                        native_unit_of_measurement=PERCENTAGE,
                        state_class=SensorStateClass.MEASUREMENT,
                    ),
                ),
            ],
            "vlzqwckk": [
                TuyaBLETemperatureMapping(
                    dp_id=1,
                    coefficient=10.0,
                    description=SensorEntityDescription(
                        key="va_temperature",
                        device_class=SensorDeviceClass.TEMPERATURE,
                        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
                        state_class=SensorStateClass.MEASUREMENT,
                    ),
                ),
                TuyaBLESensorMapping(
                    dp_id=2,
                    description=SensorEntityDescription(
                        key="va_humidity",
                        device_class=SensorDeviceClass.HUMIDITY,
                        native_unit_of_measurement=PERCENTAGE,
                        state_class=SensorStateClass.MEASUREMENT,
                    ),
                ),
                TuyaBLEBatteryMapping(
                    dp_id=4,
                    description=SensorEntityDescription(
                        key="battery_percentage",
                        device_class=SensorDeviceClass.BATTERY,
                        native_unit_of_measurement=PERCENTAGE,
                        entity_category=EntityCategory.DIAGNOSTIC,
                        state_class=SensorStateClass.MEASUREMENT,
                    ),
                ),
            ],
            "tr0kabuq": [  # Bluetooth Temperature Humidity Sensor
                TuyaBLETemperatureMapping(
                    dp_id=1,
                    coefficient=10.0,
                    description=SensorEntityDescription(
                        key="temp_current",
                        device_class=SensorDeviceClass.TEMPERATURE,
                        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
                        state_class=SensorStateClass.MEASUREMENT,
                    ),
                ),
                TuyaBLESensorMapping(
                    dp_id=2,
                    description=SensorEntityDescription(
                        key="humidity_value",
                        device_class=SensorDeviceClass.MOISTURE,
                        native_unit_of_measurement=PERCENTAGE,
                        state_class=SensorStateClass.MEASUREMENT,
                    ),
                ),
                TuyaBLEBatteryMapping(
                    dp_id=4,
                    description=SensorEntityDescription(
                        key="battery_percentage",
                        device_class=SensorDeviceClass.BATTERY,
                        native_unit_of_measurement=PERCENTAGE,
                        entity_category=EntityCategory.DIAGNOSTIC,
                        state_class=SensorStateClass.MEASUREMENT,
                    ),
                ),
            ],
        },
    ),
    "dcb": TuyaBLECategorySensorMapping(
        products={
            **{
                k: [
                    TuyaBLEBatteryMapping(dp_id=16),
                    TuyaBLETemperatureMapping(dp_id=11),
                    TuyaBLESensorMapping(
                        dp_id=172,
                        description=SensorEntityDescription(
                            key="battery_temp_current",
                            device_class=SensorDeviceClass.TEMPERATURE,
                            native_unit_of_measurement=UnitOfTemperature.CELSIUS,
                            state_class=SensorStateClass.MEASUREMENT,
                        ),
                    ),
                    TuyaBLESensorMapping(
                        dp_id=102,
                        description=SensorEntityDescription(
                            key="battery_status",
                            device_class=SensorDeviceClass.ENUM,
                            options=[
                                "Ready",
                                "Charging",
                                "Discharging",
                                "Full",
                                "Sleep",
                                "Error",
                            ],
                        ),
                    ),
                    TuyaBLESensorMapping(
                        dp_id=2,
                        description=SensorEntityDescription(
                            key="charge_current",
                            device_class=SensorDeviceClass.CURRENT,
                            native_unit_of_measurement="mA",
                            state_class=SensorStateClass.MEASUREMENT,
                            entity_category=EntityCategory.DIAGNOSTIC,
                        ),
                    ),
                    TuyaBLESensorMapping(
                        dp_id=3,
                        description=SensorEntityDescription(
                            key="charge_voltage",
                            device_class=SensorDeviceClass.VOLTAGE,
                            native_unit_of_measurement="mV",
                            state_class=SensorStateClass.MEASUREMENT,
                            entity_category=EntityCategory.DIAGNOSTIC,
                        ),
                    ),
                    TuyaBLESensorMapping(
                        dp_id=101,
                        description=SensorEntityDescription(
                            key="discharging_current",
                            device_class=SensorDeviceClass.CURRENT,
                            native_unit_of_measurement="mA",
                            state_class=SensorStateClass.MEASUREMENT,
                        ),
                    ),
                    TuyaBLESensorMapping(
                        dp_id=103,
                        description=SensorEntityDescription(
                            key="charge_to_full_time",
                            device_class=SensorDeviceClass.DURATION,
                            native_unit_of_measurement=UnitOfTime.MINUTES,
                            state_class=SensorStateClass.MEASUREMENT,
                            entity_category=EntityCategory.DIAGNOSTIC,
                        ),
                    ),
                    TuyaBLESensorMapping(
                        dp_id=104,
                        description=SensorEntityDescription(
                            key="discharge_to_empty_time",
                            device_class=SensorDeviceClass.DURATION,
                            native_unit_of_measurement=UnitOfTime.SECONDS,
                            state_class=SensorStateClass.MEASUREMENT,
                            entity_category=EntityCategory.DIAGNOSTIC,
                        ),
                    ),
                    TuyaBLESensorMapping(
                        dp_id=8,
                        description=SensorEntityDescription(
                            key="charge_times",
                            icon=ICON_COUNTER,
                            state_class=SensorStateClass.TOTAL_INCREASING,
                            entity_category=EntityCategory.DIAGNOSTIC,
                        ),
                    ),
                    TuyaBLESensorMapping(
                        dp_id=9,
                        description=SensorEntityDescription(
                            key="discharge_times",
                            icon=ICON_COUNTER,
                            state_class=SensorStateClass.TOTAL_INCREASING,
                            entity_category=EntityCategory.DIAGNOSTIC,
                        ),
                    ),
                    TuyaBLESensorMapping(
                        dp_id=14,
                        description=SensorEntityDescription(
                            key="use_time",
                            device_class=SensorDeviceClass.DURATION,
                            native_unit_of_measurement=UnitOfTime.MINUTES,
                            state_class=SensorStateClass.MEASUREMENT,
                            entity_category=EntityCategory.DIAGNOSTIC,
                        ),
                    ),
                    TuyaBLESensorMapping(
                        dp_id=15,
                        description=SensorEntityDescription(
                            key="runtime_total",
                            device_class=SensorDeviceClass.DURATION,
                            native_unit_of_measurement=UnitOfTime.MINUTES,
                            state_class=SensorStateClass.TOTAL_INCREASING,
                            entity_category=EntityCategory.DIAGNOSTIC,
                        ),
                    ),
                    TuyaBLESensorMapping(
                        dp_id=10,
                        description=SensorEntityDescription(
                            key="peak_current_times",
                            icon=ICON_COUNTER,
                            state_class=SensorStateClass.TOTAL_INCREASING,
                            entity_category=EntityCategory.DIAGNOSTIC,
                        ),
                    ),
                    TuyaBLESensorMapping(
                        dp_id=21,
                        description=SensorEntityDescription(
                            key="fault",
                            icon="mdi:alert-circle-outline",
                            entity_category=EntityCategory.DIAGNOSTIC,
                        ),
                    ),
                    TuyaBLESensorMapping(
                        dp_id=107,
                        description=SensorEntityDescription(
                            key="over_voltage_times",
                            icon=ICON_COUNTER,
                            state_class=SensorStateClass.TOTAL_INCREASING,
                            entity_category=EntityCategory.DIAGNOSTIC,
                        ),
                    ),
                    TuyaBLESensorMapping(
                        dp_id=108,
                        description=SensorEntityDescription(
                            key="under_voltage_times",
                            icon=ICON_COUNTER,
                            state_class=SensorStateClass.TOTAL_INCREASING,
                            entity_category=EntityCategory.DIAGNOSTIC,
                        ),
                    ),
                    TuyaBLESensorMapping(
                        dp_id=109,
                        description=SensorEntityDescription(
                            key="overtemp_discharge_times",
                            icon=ICON_COUNTER,
                            state_class=SensorStateClass.TOTAL_INCREASING,
                            entity_category=EntityCategory.DIAGNOSTIC,
                        ),
                    ),
                    TuyaBLESensorMapping(
                        dp_id=110,
                        description=SensorEntityDescription(
                            key="overtemp_charge_times",
                            icon=ICON_COUNTER,
                            state_class=SensorStateClass.TOTAL_INCREASING,
                            entity_category=EntityCategory.DIAGNOSTIC,
                        ),
                    ),
                    TuyaBLESensorMapping(
                        dp_id=111,
                        description=SensorEntityDescription(
                            key="undertemp_discharge_times",
                            icon=ICON_COUNTER,
                            state_class=SensorStateClass.TOTAL_INCREASING,
                            entity_category=EntityCategory.DIAGNOSTIC,
                        ),
                    ),
                    TuyaBLESensorMapping(
                        dp_id=112,
                        description=SensorEntityDescription(
                            key="undertemp_charge_times",
                            icon=ICON_COUNTER,
                            state_class=SensorStateClass.TOTAL_INCREASING,
                            entity_category=EntityCategory.DIAGNOSTIC,
                        ),
                    ),
                    TuyaBLESensorMapping(
                        dp_id=113,
                        description=SensorEntityDescription(
                            key="short_circuit_times",
                            icon=ICON_COUNTER,
                            state_class=SensorStateClass.TOTAL_INCREASING,
                            entity_category=EntityCategory.DIAGNOSTIC,
                        ),
                    ),
                    TuyaBLESensorMapping(
                        dp_id=114,
                        description=SensorEntityDescription(
                            key="over_current_times",
                            icon=ICON_COUNTER,
                            state_class=SensorStateClass.TOTAL_INCREASING,
                            entity_category=EntityCategory.DIAGNOSTIC,
                        ),
                    ),
                    TuyaBLESensorMapping(
                        dp_id=19,
                        description=SensorEntityDescription(
                            key="product_type",
                            entity_category=EntityCategory.DIAGNOSTIC,
                        ),
                    ),
                    TuyaBLESensorMapping(
                        dp_id=150,
                        description=SensorEntityDescription(
                            key="tool_product_type",
                            entity_category=EntityCategory.DIAGNOSTIC,
                        ),
                    ),
                    TuyaBLESensorMapping(
                        dp_id=152,
                        description=SensorEntityDescription(
                            key="tool_rotation_speed",
                            icon="mdi:rotate-3d-variant",
                            state_class=SensorStateClass.MEASUREMENT,
                            entity_category=EntityCategory.DIAGNOSTIC,
                        ),
                    ),
                    TuyaBLESensorMapping(
                        dp_id=153,
                        description=SensorEntityDescription(
                            key="tool_torque",
                            icon="mdi:screw-lag",
                            state_class=SensorStateClass.MEASUREMENT,
                            entity_category=EntityCategory.DIAGNOSTIC,
                        ),
                    ),
                    TuyaBLESensorMapping(
                        dp_id=154,
                        description=SensorEntityDescription(
                            key="tool_runtime_total",
                            device_class=SensorDeviceClass.DURATION,
                            native_unit_of_measurement=UnitOfTime.MINUTES,
                            state_class=SensorStateClass.TOTAL_INCREASING,
                            entity_category=EntityCategory.DIAGNOSTIC,
                        ),
                    ),
                    TuyaBLESensorMapping(
                        dp_id=156,
                        description=SensorEntityDescription(
                            key="tool_fault",
                            icon="mdi:alert-circle-outline",
                            entity_category=EntityCategory.DIAGNOSTIC,
                        ),
                    ),
                    TuyaBLESensorMapping(
                        dp_id=157,
                        description=SensorEntityDescription(
                            key="tools_current",
                            device_class=SensorDeviceClass.CURRENT,
                            state_class=SensorStateClass.MEASUREMENT,
                            entity_category=EntityCategory.DIAGNOSTIC,
                        ),
                    ),
                    TuyaBLESensorMapping(
                        dp_id=158,
                        description=SensorEntityDescription(
                            key="tool_ot_times",
                            state_class=SensorStateClass.TOTAL_INCREASING,
                            entity_category=EntityCategory.DIAGNOSTIC,
                        ),
                    ),
                    TuyaBLESensorMapping(
                        dp_id=159,
                        description=SensorEntityDescription(
                            key="tool_locked_times",
                            state_class=SensorStateClass.TOTAL_INCREASING,
                            entity_category=EntityCategory.DIAGNOSTIC,
                        ),
                    ),
                    TuyaBLESensorMapping(
                        dp_id=160,
                        description=SensorEntityDescription(
                            key="tool_oc_times",
                            state_class=SensorStateClass.TOTAL_INCREASING,
                            entity_category=EntityCategory.DIAGNOSTIC,
                        ),
                    ),
                ]
                for k in ["z5ztlw3k", "ajrhf1aj"]
            },
        },
    ),
    "zwjcy": TuyaBLECategorySensorMapping(
        products={
            **{
                k: [
                    TuyaBLETemperatureMapping(
                        dp_id=5,
                        coefficient=10.0,
                        description=SensorEntityDescription(
                            key="temp_current",
                            device_class=SensorDeviceClass.TEMPERATURE,
                            native_unit_of_measurement=UnitOfTemperature.CELSIUS,
                            state_class=SensorStateClass.MEASUREMENT,
                        ),
                    ),
                    TuyaBLESensorMapping(
                        dp_id=3,
                        description=SensorEntityDescription(
                            key="humidity",
                            device_class=SensorDeviceClass.HUMIDITY,
                            native_unit_of_measurement=PERCENTAGE,
                            state_class=SensorStateClass.MEASUREMENT,
                        ),
                    ),
                    TuyaBLESensorMapping(
                        dp_id=14,
                        description=SensorEntityDescription(
                            key="battery_state",
                            icon=ICON_BATTERY,
                            device_class=SensorDeviceClass.ENUM,
                            entity_category=EntityCategory.DIAGNOSTIC,
                            options=[
                                BATTERY_STATE_LOW,
                                BATTERY_STATE_NORMAL,
                                BATTERY_STATE_HIGH,
                            ],
                        ),
                        icons=[
                            "mdi:battery-alert",
                            "mdi:battery-50",
                            ICON_BATTERY_CHECK,
                        ],
                    ),
                    TuyaBLEBatteryMapping(
                        dp_id=15,
                        description=SensorEntityDescription(
                            key="battery_percentage",
                            device_class=SensorDeviceClass.BATTERY,
                            native_unit_of_measurement=PERCENTAGE,
                            entity_category=EntityCategory.DIAGNOSTIC,
                            state_class=SensorStateClass.MEASUREMENT,
                        ),
                    ),
                ]
                for k in ["gvygg3m8", "jabotj1z"]
            },
        },
    ),
    "znhsb": TuyaBLECategorySensorMapping(
        products={
            "cdlandip":  # Smart water bottle
            [
                TuyaBLETemperatureMapping(
                    dp_id=101,
                ),
                TuyaBLESensorMapping(
                    dp_id=102,
                    description=SensorEntityDescription(
                        key="water_intake",
                        device_class=SensorDeviceClass.WATER,
                        native_unit_of_measurement=UnitOfVolume.MILLILITERS,
                        state_class=SensorStateClass.MEASUREMENT,
                    ),
                ),
                TuyaBLESensorMapping(
                    dp_id=104,
                    description=SensorEntityDescription(
                        key="battery",
                        device_class=SensorDeviceClass.BATTERY,
                        native_unit_of_measurement=PERCENTAGE,
                        entity_category=EntityCategory.DIAGNOSTIC,
                        state_class=SensorStateClass.MEASUREMENT,
                    ),
                    getter=battery_enum_getter,
                ),
            ],
        },
    ),
    "ggq": TuyaBLECategorySensorMapping(
        products={
            "6pahkcau": [  # Irrigation computer PARKSIDE PPB A1
                TuyaBLEBatteryMapping(dp_id=11),
                TuyaBLESensorMapping(
                    dp_id=6,
                    description=SensorEntityDescription(
                        key="time_left",
                        device_class=SensorDeviceClass.DURATION,
                        native_unit_of_measurement=UnitOfTime.MINUTES,
                        state_class=SensorStateClass.MEASUREMENT,
                    ),
                ),
            ],
            **{
                k: [
                    TuyaBLEBatteryMapping(dp_id=11),
                    TuyaBLESensorMapping(
                        dp_id=111,
                        description=SensorEntityDescription(
                            key="use_time_z1",
                            device_class=SensorDeviceClass.DURATION,
                            native_unit_of_measurement=UnitOfTime.SECONDS,
                            state_class=SensorStateClass.MEASUREMENT,
                        ),
                    ),
                    TuyaBLESensorMapping(
                        dp_id=110,
                        description=SensorEntityDescription(
                            key="use_time_z2",
                            device_class=SensorDeviceClass.DURATION,
                            native_unit_of_measurement=UnitOfTime.SECONDS,
                            state_class=SensorStateClass.MEASUREMENT,
                        ),
                    ),
                ]
                for k in ["hfgdqhho", "qycalacn", "fnlw6npo", "jjqi2syk"]
            },
            "fdrbxxbg": [  # Diivoo WT-05 dual water timer
                TuyaBLEBatteryMapping(dp_id=11),
                TuyaBLESensorMapping(
                    dp_id=110,
                    description=SensorEntityDescription(
                        key="last_use_time_zone1",
                        icon="mdi:clock-outline",
                        device_class=SensorDeviceClass.DURATION,
                        native_unit_of_measurement=UnitOfTime.SECONDS,
                        state_class=SensorStateClass.MEASUREMENT,
                    ),
                ),
                TuyaBLESensorMapping(
                    dp_id=111,
                    description=SensorEntityDescription(
                        key="last_use_time_zone2",
                        icon="mdi:clock-outline",
                        device_class=SensorDeviceClass.DURATION,
                        native_unit_of_measurement=UnitOfTime.SECONDS,
                        state_class=SensorStateClass.MEASUREMENT,
                    ),
                ),
                TuyaBLESensorMapping(
                    dp_id=112,
                    description=SensorEntityDescription(
                        key="work_states_zone1",
                        device_class=SensorDeviceClass.ENUM,
                        options=["off", "manual", "auto"],
                    ),
                ),
                TuyaBLESensorMapping(
                    dp_id=113,
                    description=SensorEntityDescription(
                        key="work_states_zone2",
                        device_class=SensorDeviceClass.ENUM,
                        options=["off", "manual", "auto"],
                    ),
                ),
            ],
        },
    ),
    "sfkzq": TuyaBLECategorySensorMapping(
        products={
            "16wgjvck": [  # Aldi/Ferrex Smart Water Valve
                TuyaBLEBatteryMapping(dp_id=7),
                TuyaBLESensorMapping(
                    dp_id=8,
                    dp_type=TuyaBLEDataPointType.DT_ENUM,
                    description=SensorEntityDescription(
                        key="battery_state",
                        device_class=SensorDeviceClass.ENUM,
                        options=["low", "middle", "high"],
                        entity_category=EntityCategory.DIAGNOSTIC,
                    ),
                ),
                TuyaBLESensorMapping(
                    dp_id=104,
                    dp_type=TuyaBLEDataPointType.DT_VALUE,
                    description=SensorEntityDescription(
                        key="battery_percentage_alt",
                        device_class=SensorDeviceClass.BATTERY,
                        native_unit_of_measurement=PERCENTAGE,
                        state_class=SensorStateClass.MEASUREMENT,
                        entity_category=EntityCategory.DIAGNOSTIC,
                    ),
                ),
            ],
            "0axr5s0b": [  # Valve Controller
                TuyaBLEBatteryMapping(dp_id=7),
                TuyaBLESensorMapping(
                    dp_id=11,
                    description=SensorEntityDescription(
                        key="time_left",
                        device_class=SensorDeviceClass.DURATION,
                        native_unit_of_measurement=UnitOfTime.SECONDS,
                        state_class=SensorStateClass.MEASUREMENT,
                    ),
                ),
            ],
            "ldcdnigc": [  # ZX-7378 Smart Irrigation Controller
                TuyaBLESensorMapping(
                    dp_id=12,
                    dp_type=TuyaBLEDataPointType.DT_ENUM,
                    description=SensorEntityDescription(
                        key="work_state",
                        device_class=SensorDeviceClass.ENUM,
                        options=["auto", "manual", "idle"],
                    ),
                ),
                TuyaBLESensorMapping(
                    dp_id=8,
                    dp_type=TuyaBLEDataPointType.DT_ENUM,
                    description=SensorEntityDescription(
                        key="battery_state",
                        device_class=SensorDeviceClass.ENUM,
                        options=["low", "middle", "high"],
                        entity_category=EntityCategory.DIAGNOSTIC,
                    ),
                ),
                TuyaBLESensorMapping(
                    dp_id=7,
                    dp_type=TuyaBLEDataPointType.DT_VALUE,
                    description=SensorEntityDescription(
                        key="battery_percentage",
                        device_class=SensorDeviceClass.BATTERY,
                        native_unit_of_measurement=PERCENTAGE,
                        state_class=SensorStateClass.MEASUREMENT,
                    ),
                ),
                TuyaBLESensorMapping(
                    dp_id=15,
                    description=SensorEntityDescription(
                        key="use_time_one",
                        device_class=SensorDeviceClass.DURATION,
                        native_unit_of_measurement=UnitOfTime.SECONDS,
                        entity_category=EntityCategory.DIAGNOSTIC,
                        state_class=SensorStateClass.MEASUREMENT,
                    ),
                ),
            ],
            **{
                k: [
                    TuyaBLEBatteryMapping(dp_id=7),
                    TuyaBLESensorMapping(
                        dp_id=12,
                        description=SensorEntityDescription(
                            key="work_state",
                            device_class=SensorDeviceClass.ENUM,
                            options=["auto", "manual", "idle"],
                        ),
                    ),
                    TuyaBLESensorMapping(
                        dp_id=15,
                        description=SensorEntityDescription(
                            key="use_time_one",
                            device_class=SensorDeviceClass.DURATION,
                            native_unit_of_measurement=UnitOfTime.SECONDS,
                            state_class=SensorStateClass.MEASUREMENT,
                        ),
                    ),
                    TuyaBLESensorMapping(
                        dp_id=9,
                        description=SensorEntityDescription(
                            key="time_use",
                            device_class=SensorDeviceClass.DURATION,
                            native_unit_of_measurement=UnitOfTime.SECONDS,
                            state_class=SensorStateClass.MEASUREMENT,
                        ),
                    ),
                ]
                for k in ["46zia2nz", "1fcnd8xk", "svhikeyq"]
            },
            "nxquc5lb": [  # Smart water timer - SOP10
                TuyaBLEBatteryMapping(dp_id=7),
                TuyaBLESensorMapping(
                    dp_id=12,
                    description=SensorEntityDescription(
                        key="work_state",
                        device_class=SensorDeviceClass.ENUM,
                        options=["auto", "manual", "idle"],
                    ),
                ),
                TuyaBLESensorMapping(
                    dp_id=9,
                    description=SensorEntityDescription(
                        key="time_use",
                        device_class=SensorDeviceClass.DURATION,
                        native_unit_of_measurement=UnitOfTime.SECONDS,
                        state_class=SensorStateClass.MEASUREMENT,
                    ),
                ),
            ],
        },
    ),
    "cl": TuyaBLECategorySensorMapping(
        products={
            **{
                k: [
                    TuyaBLEBatteryMapping(dp_id=13),
                    TuyaBLESensorMapping(
                        dp_id=7,
                        description=SensorEntityDescription(
                            key="cover_work_state",
                            entity_category=EntityCategory.DIAGNOSTIC,
                            device_class=SensorDeviceClass.ENUM,
                            options=["STANDBY", "SUCCESS", "LEARNING"],
                        ),
                    ),
                ]
                for k in ["4pbr8eig", "qqdxfdht", "kcy0x4pi", "vlwf3ud6"]
            },
        },
    ),
}


def rssi_getter(sensor: TuyaBLESensor) -> None:
    """Read the RSSI signal strength from the device."""
    sensor.set_native_value(sensor.device.rssi)


rssi_mapping = TuyaBLESensorMapping(
    dp_id=SIGNAL_STRENGTH_DP_ID,
    description=SensorEntityDescription(
        key="signal_strength",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    getter=rssi_getter,
)


def get_mapping_by_device(device: TuyaBLEDevice) -> list[TuyaBLESensorMapping]:
    """Return sensor mappings for a given device by category and product."""
    category = mapping.get(device.category)
    if category is not None and category.products is not None:
        product_mapping = category.products.get(device.product_id)
        if product_mapping is not None:
            return product_mapping
        if category.mapping is not None:
            return category.mapping
    return []


class TuyaBLESensor(TuyaBLEEntity, SensorEntity):
    """Representation of a Tuya BLE sensor."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: TuyaBLECoordinator,
        device: TuyaBLEDevice,
        product: TuyaBLEProductInfo,
        sensor_mapping: TuyaBLESensorMapping,
    ) -> None:
        super().__init__(hass, coordinator, device, product, sensor_mapping.description)
        self._mapping = sensor_mapping

    @callback
    def _handle_coordinator_update(self) -> None:
        """Invoke the mapping getter or read the datapoint."""
        if self._mapping.getter is not None:
            self._mapping.getter(self)
        else:
            datapoint = self.device.datapoints[self._mapping.dp_id]
            if datapoint:
                self._update_from_datapoint(datapoint)
        self.async_write_ha_state()

    @callback
    def _update_from_datapoint(self, datapoint: TuyaBLEDataPoint) -> None:
        """Update attributes from a datapoint based on its type."""
        if datapoint.dp_type == TuyaBLEDataPointType.DT_ENUM:
            self._update_enum_value(datapoint)
        elif datapoint.dp_type == TuyaBLEDataPointType.DT_VALUE:
            if isinstance(datapoint.value, int):
                self._attr_native_value = datapoint.value / self._mapping.coefficient
        else:
            if isinstance(datapoint.value, (int, float, str)):
                self._attr_native_value = datapoint.value

    @callback
    def _update_enum_value(self, datapoint: TuyaBLEDataPoint) -> None:
        """Update attributes from an enum datapoint."""
        if self.entity_description.options is not None:
            if isinstance(datapoint.value, int) and 0 <= datapoint.value < len(
                self.entity_description.options
            ):
                self._attr_native_value = self.entity_description.options[
                    datapoint.value
                ]
            else:
                self._attr_native_value = str(datapoint.value)
        if (
            self._mapping.icons is not None
            and isinstance(datapoint.value, int)
            and 0 <= datapoint.value < len(self._mapping.icons)
        ):
            self._attr_icon = self._mapping.icons[datapoint.value]

    @property
    def available(self) -> bool:
        """True when coordinator is connected and the availability predicate passes."""
        result = super().available
        if result and self._mapping.is_available is not None:
            result = self._mapping.is_available(self, self._product)
        return result

    def set_native_value(self, value: float | int | None) -> None:
        """Set the native value of the sensor."""
        self._attr_native_value = value


async def async_setup_entry(  # noqa: S7503
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Tuya BLE sensors."""
    data: TuyaBLEData = hass.data[DOMAIN][entry.entry_id]
    mappings = get_mapping_by_device(data.device)
    entities: list[TuyaBLESensor] = [
        TuyaBLESensor(
            hass,
            data.coordinator,
            data.device,
            data.product,
            rssi_mapping,
        )
    ]
    for sensor_mapping in mappings:
        if sensor_mapping.force_add or data.device.datapoints.has_id(
            sensor_mapping.dp_id, sensor_mapping.dp_type
        ):
            entities.append(
                TuyaBLESensor(
                    hass,
                    data.coordinator,
                    data.device,
                    data.product,
                    sensor_mapping,
                )
            )
    async_add_entities(entities)
