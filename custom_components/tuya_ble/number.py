"""Number platform for Tuya BLE devices."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
)
from homeassistant.components.number.const import NumberDeviceClass, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfRatio,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolume,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .devices import TuyaBLECoordinator, TuyaBLEData, TuyaBLEEntity, TuyaBLEProductInfo
from .fingerbot import (
    get_fingerbot_program_position,
    get_fingerbot_program_repeat_count,
    is_fingerbot_in_program_mode,
    is_fingerbot_in_push_mode,
    is_fingerbot_not_in_program_mode,
    is_fingerbot_repeat_count_available,
    set_fingerbot_program_position,
    set_fingerbot_program_repeat_count,
)
from .tuya_ble import TuyaBLEDataPointType, TuyaBLEDevice

ICON_TIMER = "mdi:timer"

TuyaBLENumberGetter = (
    Callable[["TuyaBLENumber", TuyaBLEProductInfo], float | None] | None
)


TuyaBLENumberIsAvailable = Callable[["TuyaBLENumber", TuyaBLEProductInfo], bool] | None


TuyaBLENumberSetter = (
    Callable[["TuyaBLENumber", TuyaBLEProductInfo, float], None] | None
)


@dataclass
class TuyaBLENumberMapping:
    """Data class mapping a Tuya data point to a Home Assistant number entity."""

    dp_id: int
    description: NumberEntityDescription
    force_add: bool = True
    dp_type: TuyaBLEDataPointType | None = None
    coefficient: float = 1.0
    is_available: TuyaBLENumberIsAvailable = None
    getter: TuyaBLENumberGetter = None
    setter: TuyaBLENumberSetter = None
    mode: NumberMode = NumberMode.BOX


class TuyaBLEDownPositionDescription(NumberEntityDescription):
    """Number entity description for fingerbot down position."""

    key: str = "down_position"
    icon: str = "mdi:arrow-down-bold"
    native_max_value: float = 100
    native_min_value: float = 51
    native_unit_of_measurement: str = PERCENTAGE
    native_step: float = 1
    entity_category: EntityCategory = EntityCategory.CONFIG


class TuyaBLEUpPositionDescription(NumberEntityDescription):
    """Number entity description for fingerbot up position."""

    key: str = "up_position"
    icon: str = "mdi:arrow-up-bold"
    native_max_value: float = 50
    native_min_value: float = 0
    native_unit_of_measurement: str = PERCENTAGE
    native_step: float = 1
    entity_category: EntityCategory = EntityCategory.CONFIG


class TuyaBLEHoldTimeDescription(NumberEntityDescription):
    """Number entity description for fingerbot hold time."""

    key: str = "hold_time"
    icon: str = ICON_TIMER
    native_max_value: float = 10
    native_min_value: float = 0
    native_unit_of_measurement: str = UnitOfTime.SECONDS
    native_step: float = 1
    entity_category: EntityCategory = EntityCategory.CONFIG


@dataclass
class TuyaBLEHoldTimeMapping(TuyaBLENumberMapping):
    """Number mapping for fingerbot hold time with push-mode availability."""

    description: NumberEntityDescription = field(
        default_factory=lambda: TuyaBLEHoldTimeDescription(key="hold_time")
    )
    is_available: TuyaBLENumberIsAvailable = is_fingerbot_in_push_mode


@dataclass
class TuyaBLECategoryNumberMapping:
    """Data class grouping number entity mappings by product or category."""

    products: dict[str, list[TuyaBLENumberMapping]] | None = None
    mapping: list[TuyaBLENumberMapping] | None = None


mapping: dict[str, TuyaBLECategoryNumberMapping] = {
    "co2bj": TuyaBLECategoryNumberMapping(
        products={
            "59s19z5m": [  # CO2 Detector
                TuyaBLENumberMapping(
                    dp_id=17,
                    description=NumberEntityDescription(
                        key="brightness",
                        icon="mdi:brightness-percent",
                        native_max_value=100,
                        native_min_value=0,
                        native_unit_of_measurement=PERCENTAGE,
                        native_step=1,
                        entity_category=EntityCategory.CONFIG,
                    ),
                    mode=NumberMode.SLIDER,
                ),
                TuyaBLENumberMapping(
                    dp_id=26,
                    description=NumberEntityDescription(
                        key="carbon_dioxide_alarm_level",
                        icon="mdi:molecule-co2",
                        native_max_value=5000,
                        native_min_value=400,
                        native_unit_of_measurement=UnitOfRatio.PARTS_PER_MILLION,
                        native_step=100,
                        entity_category=EntityCategory.CONFIG,
                    ),
                ),
            ],
        },
    ),
    "szjqr": TuyaBLECategoryNumberMapping(
        products={
            **{
                k: [
                    TuyaBLEHoldTimeMapping(dp_id=3),
                    TuyaBLENumberMapping(
                        dp_id=5,
                        description=TuyaBLEUpPositionDescription(
                            key="up_position", native_max_value=100
                        ),
                    ),
                    TuyaBLENumberMapping(
                        dp_id=6,
                        description=TuyaBLEDownPositionDescription(
                            key="down_position", native_min_value=0
                        ),
                    ),
                ]
                for k in ["3yqdo5yt", "xhf790if"]
            },
            **{
                k: [
                    TuyaBLENumberMapping(
                        dp_id=9,
                        description=TuyaBLEDownPositionDescription(key="down_position"),
                        is_available=is_fingerbot_not_in_program_mode,
                    ),
                    TuyaBLEHoldTimeMapping(dp_id=10),
                    TuyaBLENumberMapping(
                        dp_id=15,
                        description=TuyaBLEUpPositionDescription(key="up_position"),
                        is_available=is_fingerbot_not_in_program_mode,
                    ),
                    TuyaBLENumberMapping(
                        dp_id=121,
                        description=NumberEntityDescription(
                            key="program_repeats_count",
                            icon="mdi:repeat",
                            native_max_value=0xFFFE,
                            native_min_value=1,
                            native_step=1,
                            entity_category=EntityCategory.CONFIG,
                        ),
                        is_available=is_fingerbot_repeat_count_available,
                        getter=get_fingerbot_program_repeat_count,
                        setter=set_fingerbot_program_repeat_count,
                    ),
                    TuyaBLENumberMapping(
                        dp_id=121,
                        description=NumberEntityDescription(
                            key="program_idle_position",
                            icon="mdi:repeat",
                            native_max_value=100,
                            native_min_value=0,
                            native_step=1,
                            native_unit_of_measurement=PERCENTAGE,
                            entity_category=EntityCategory.CONFIG,
                        ),
                        is_available=is_fingerbot_in_program_mode,
                        getter=get_fingerbot_program_position,
                        setter=set_fingerbot_program_position,
                    ),
                ]
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
                k: [
                    TuyaBLENumberMapping(
                        dp_id=9,
                        description=TuyaBLEDownPositionDescription(key="down_position"),
                        is_available=is_fingerbot_not_in_program_mode,
                    ),
                    TuyaBLENumberMapping(
                        dp_id=10,
                        description=TuyaBLEHoldTimeDescription(
                            key="hold_time", native_step=0.1
                        ),
                        coefficient=10.0,
                        is_available=is_fingerbot_in_push_mode,
                    ),
                    TuyaBLENumberMapping(
                        dp_id=15,
                        description=TuyaBLEUpPositionDescription(key="up_position"),
                        is_available=is_fingerbot_not_in_program_mode,
                    ),
                ]
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
            "yn4x5fa7": [
                TuyaBLEHoldTimeMapping(
                    dp_id=3,
                    description=TuyaBLEHoldTimeDescription(
                        key="hold_time",
                        native_min_value=0.3,
                        native_max_value=10.0,
                        native_step=0.1,
                    ),
                    coefficient=10.0,
                ),
                TuyaBLENumberMapping(
                    dp_id=4,
                    description=NumberEntityDescription(
                        key="up_position",
                        icon="mdi:arrow-up-bold",
                        native_max_value=30,
                        native_min_value=0,
                        native_unit_of_measurement=PERCENTAGE,
                        native_step=1,
                        entity_category=EntityCategory.CONFIG,
                    ),
                    is_available=is_fingerbot_not_in_program_mode,
                ),
                TuyaBLENumberMapping(
                    dp_id=5,
                    description=NumberEntityDescription(
                        key="down_position",
                        icon="mdi:arrow-down-bold",
                        native_max_value=30,
                        native_min_value=0,
                        native_unit_of_measurement=PERCENTAGE,
                        native_step=1,
                        entity_category=EntityCategory.CONFIG,
                    ),
                    is_available=is_fingerbot_not_in_program_mode,
                ),
            ],
        },
    ),
    "wk": TuyaBLECategoryNumberMapping(
        products={
            **{
                k: [
                    TuyaBLENumberMapping(
                        dp_id=27,
                        description=NumberEntityDescription(
                            key="temperature_calibration",
                            icon="mdi:thermometer-lines",
                            native_max_value=6,
                            native_min_value=-6,
                            native_unit_of_measurement=UnitOfTemperature.CELSIUS,
                            native_step=1,
                            entity_category=EntityCategory.CONFIG,
                        ),
                    )
                ]
                for k in ["drlajpqc", "nhj2j7su", "zmachryv"]
            },
        },
    ),
    "wsdcg": TuyaBLECategoryNumberMapping(
        products={
            "ojzlzzsw": [  # Soil moisture sensor
                TuyaBLENumberMapping(
                    dp_id=17,
                    description=NumberEntityDescription(
                        key="reporting_period",
                        icon=ICON_TIMER,
                        native_max_value=120,
                        native_min_value=1,
                        native_unit_of_measurement=UnitOfTime.MINUTES,
                        native_step=1,
                        entity_category=EntityCategory.CONFIG,
                    ),
                ),
            ],
        },
    ),
    "znhsb": TuyaBLECategoryNumberMapping(
        products={
            "cdlandip":  # Smart water bottle
            [
                TuyaBLENumberMapping(
                    dp_id=103,
                    description=NumberEntityDescription(
                        key="recommended_water_intake",
                        device_class=NumberDeviceClass.WATER,
                        native_max_value=5000,
                        native_min_value=0,
                        native_unit_of_measurement=UnitOfVolume.MILLILITERS,
                        native_step=1,
                        entity_category=EntityCategory.CONFIG,
                    ),
                ),
            ],
        },
    ),
    "ggq": TuyaBLECategoryNumberMapping(
        products={
            "6pahkcau": [  # Irrigation computer PARKSIDE PPB A1
                TuyaBLENumberMapping(
                    dp_id=5,
                    description=NumberEntityDescription(
                        key="countdown_duration",
                        icon=ICON_TIMER,
                        native_max_value=1440,
                        native_min_value=1,
                        native_unit_of_measurement=UnitOfTime.MINUTES,
                        native_step=1,
                    ),
                ),
            ],
            "hfgdqhho": [  # Irrigation computer - SGW02, SGW08
                TuyaBLENumberMapping(
                    dp_id=106,
                    description=NumberEntityDescription(
                        key="countdown_duration_z1",
                        name="CH1 Countdown",
                        icon=ICON_TIMER,
                        native_max_value=1440,
                        native_min_value=1,
                        native_unit_of_measurement=UnitOfTime.MINUTES,
                        native_step=1,
                    ),
                ),
                TuyaBLENumberMapping(
                    dp_id=103,
                    description=NumberEntityDescription(
                        key="countdown_duration_z2",
                        name="CH2 Countdown",
                        icon=ICON_TIMER,
                        native_max_value=1440,
                        native_min_value=1,
                        native_unit_of_measurement=UnitOfTime.MINUTES,
                        native_step=1,
                    ),
                ),
            ],
            **{
                k: [
                    TuyaBLENumberMapping(
                        dp_id=106,
                        description=NumberEntityDescription(
                            key="countdown_duration_z1",
                            icon=ICON_TIMER,
                            native_max_value=1440,
                            native_min_value=1,
                            native_unit_of_measurement=UnitOfTime.MINUTES,
                            native_step=1,
                        ),
                    ),
                    TuyaBLENumberMapping(
                        dp_id=103,
                        description=NumberEntityDescription(
                            key="countdown_duration_z2",
                            icon=ICON_TIMER,
                            native_max_value=1440,
                            native_min_value=1,
                            native_unit_of_measurement=UnitOfTime.MINUTES,
                            native_step=1,
                        ),
                    ),
                ]
                for k in ["hfgdqhho", "qycalacn", "fnlw6npo", "jjqi2syk"]
            },
        },
    ),
    "sfkzq": TuyaBLECategoryNumberMapping(
        products={
            "16wgjvck": [  # Aldi/Ferrex Smart Water Valve
                TuyaBLENumberMapping(
                    dp_id=2,
                    description=NumberEntityDescription(
                        key="valve_opening_percentage",
                        icon="mdi:valve",
                        native_max_value=100,
                        native_min_value=0,
                        native_unit_of_measurement=PERCENTAGE,
                        native_step=1,
                    ),
                ),
                TuyaBLENumberMapping(
                    dp_id=11,
                    description=NumberEntityDescription(
                        key="countdown",
                        icon=ICON_TIMER,
                        native_max_value=86400,
                        native_min_value=0,
                        native_unit_of_measurement=UnitOfTime.SECONDS,
                        native_step=1,
                    ),
                ),
                TuyaBLENumberMapping(
                    dp_id=15,
                    description=NumberEntityDescription(
                        key="use_time",
                        icon=ICON_TIMER,
                        native_max_value=86400,
                        native_min_value=0,
                        native_unit_of_measurement=UnitOfTime.SECONDS,
                        native_step=1,
                    ),
                ),
            ],
            **{
                k: [
                    TuyaBLENumberMapping(
                        dp_id=11,
                        description=NumberEntityDescription(
                            key="countdown_duration",
                            icon=ICON_TIMER,
                            native_max_value=86400,
                            native_min_value=1,
                            native_unit_of_measurement=UnitOfTime.SECONDS,
                            native_step=1,
                        ),
                    )
                ]
                for k in ["46zia2nz", "1fcnd8xk", "0axr5s0b"]
            },
            "ldcdnigc": [
                TuyaBLENumberMapping(
                    dp_id=11,
                    description=NumberEntityDescription(
                        key="countdown",
                        icon=ICON_TIMER,
                        native_max_value=86400,
                        native_min_value=0,
                        native_unit_of_measurement=UnitOfTime.SECONDS,
                        native_step=1,
                    ),
                ),
            ],
            "svhikeyq": [
                TuyaBLENumberMapping(
                    dp_id=11,
                    description=NumberEntityDescription(
                        key="countdown",
                        icon=ICON_TIMER,
                        native_max_value=86400,
                        native_min_value=1,
                        native_unit_of_measurement=UnitOfTime.SECONDS,
                        native_step=1,
                    ),
                ),
                TuyaBLENumberMapping(
                    dp_id=9,
                    description=NumberEntityDescription(
                        key="countdown_duration",
                        icon=ICON_TIMER,
                        native_max_value=2592000,
                        native_min_value=1,
                        native_unit_of_measurement=UnitOfTime.SECONDS,
                        native_step=1,
                    ),
                ),
            ],
            "nxquc5lb": [  # Smart water timer - SOP10
                TuyaBLENumberMapping(
                    dp_id=11,
                    description=NumberEntityDescription(
                        key="countdown",
                        icon=ICON_TIMER,
                        native_max_value=86400,
                        native_min_value=60,
                        native_unit_of_measurement=UnitOfTime.SECONDS,
                        native_step=1,
                    ),
                ),
            ],
            "fdrbxxbg": [  # Diivoo WT-05 dual water timer
                TuyaBLENumberMapping(
                    dp_id=106,
                    description=NumberEntityDescription(
                        key="countdown_zone1",
                        icon=ICON_TIMER,
                        native_max_value=1440,
                        native_min_value=1,
                        native_unit_of_measurement=UnitOfTime.MINUTES,
                        native_step=1,
                    ),
                ),
                TuyaBLENumberMapping(
                    dp_id=103,
                    description=NumberEntityDescription(
                        key="countdown_zone2",
                        icon=ICON_TIMER,
                        native_max_value=1440,
                        native_min_value=1,
                        native_unit_of_measurement=UnitOfTime.MINUTES,
                        native_step=1,
                    ),
                ),
            ],
        },
    ),
    "dcb": TuyaBLECategoryNumberMapping(
        products={
            **{
                k: [
                    TuyaBLENumberMapping(
                        dp_id=116,
                        description=NumberEntityDescription(
                            key="low_discharge_voltage",
                            device_class=NumberDeviceClass.VOLTAGE,
                            native_unit_of_measurement="mV",
                            entity_category=EntityCategory.CONFIG,
                        ),
                    ),
                    TuyaBLENumberMapping(
                        dp_id=117,
                        description=NumberEntityDescription(
                            key="discharge_current_limit",
                            device_class=NumberDeviceClass.CURRENT,
                            native_unit_of_measurement="A",
                            entity_category=EntityCategory.CONFIG,
                        ),
                    ),
                    TuyaBLENumberMapping(
                        dp_id=118,
                        description=NumberEntityDescription(
                            key="power_indicator_time",
                            device_class=NumberDeviceClass.DURATION,
                            native_unit_of_measurement=UnitOfTime.SECONDS,
                            entity_category=EntityCategory.CONFIG,
                        ),
                    ),
                    TuyaBLENumberMapping(
                        dp_id=164,
                        description=NumberEntityDescription(
                            key="lamp_brightness_percentage",
                            native_unit_of_measurement=PERCENTAGE,
                            icon="mdi:brightness-percent",
                            entity_category=EntityCategory.CONFIG,
                        ),
                    ),
                    TuyaBLENumberMapping(
                        dp_id=165,
                        description=NumberEntityDescription(
                            key="lamp_delay_time",
                            device_class=NumberDeviceClass.DURATION,
                            native_unit_of_measurement=UnitOfTime.SECONDS,
                            icon="mdi:camera-timer",
                            entity_category=EntityCategory.CONFIG,
                        ),
                    ),
                    TuyaBLENumberMapping(
                        dp_id=173,
                        description=NumberEntityDescription(
                            key="kick_back_adjust",
                            icon="mdi:car-esp",
                            entity_category=EntityCategory.CONFIG,
                        ),
                    ),
                    TuyaBLENumberMapping(
                        dp_id=178,
                        description=NumberEntityDescription(
                            key="speed_percentage",
                            native_unit_of_measurement=PERCENTAGE,
                            icon="mdi:speedometer",
                            entity_category=EntityCategory.CONFIG,
                        ),
                    ),
                ]
                for k in ["ajrhf1aj", "z5ztlw3k"]
            },
        },
    ),
    "cl": TuyaBLECategoryNumberMapping(
        products={
            **{
                k: [
                    TuyaBLENumberMapping(
                        dp_id=105,
                        description=NumberEntityDescription(
                            key="cover_speed",
                            icon="mdi:speedometer",
                            native_max_value=40,
                            native_min_value=1,
                            native_step=1,
                            mode=NumberMode.BOX,
                        ),
                    )
                ]
                for k in ["4pbr8eig", "qqdxfdht", "kcy0x4pi", "vlwf3ud6"]
            },
        },
    ),
}


def get_mapping_by_device(device: TuyaBLEDevice) -> list[TuyaBLENumberMapping]:
    """Return the number entity mappings for a given Tuya BLE device."""
    category = mapping.get(device.category)
    if category is not None and category.products is not None:
        product_mapping = category.products.get(device.product_id)
        if product_mapping is not None:
            return product_mapping
        if category.mapping is not None:
            return category.mapping
        return []
    return []


class TuyaBLENumber(TuyaBLEEntity, NumberEntity):  # pylint: disable=abstract-method
    """Representation of a Tuya BLE Number."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: TuyaBLECoordinator,
        device: TuyaBLEDevice,
        product: TuyaBLEProductInfo,
        number_mapping: TuyaBLENumberMapping,
    ) -> None:
        super().__init__(hass, coordinator, device, product, number_mapping.description)
        self._mapping = number_mapping
        self._attr_mode = number_mapping.mode

    @property
    def native_value(self) -> float | None:
        """Read the datapoint, apply the coefficient, and return the value."""
        if self._mapping.getter is not None:
            return self._mapping.getter(self, self._product)

        datapoint = self.device.datapoints[self._mapping.dp_id]
        if datapoint and isinstance(datapoint.value, (int, float)):
            return datapoint.value / self._mapping.coefficient

        return self._mapping.description.native_min_value

    def set_native_value(self, value: float) -> None:
        """Send the new value to the device, applying the coefficient."""
        if self._mapping.setter:
            self._mapping.setter(self, self._product, value)
            return
        int_value = int(value * self._mapping.coefficient)
        datapoint = self.device.datapoints.get_or_create(
            self._mapping.dp_id,
            TuyaBLEDataPointType.DT_VALUE,
            int(int_value),
        )
        self.hass.create_task(datapoint.set_value(int_value))

    @property
    def available(self) -> bool:
        """True when coordinator is connected and the availability predicate passes."""
        result = super().available
        if result and self._mapping.is_available is not None:
            result = self._mapping.is_available(self, self._product)
        return result


async def async_setup_entry(  # noqa: S7503
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tuya BLE number entities for the config entry."""
    data: TuyaBLEData = hass.data[DOMAIN][entry.entry_id]
    mappings = get_mapping_by_device(data.device)
    entities: list[TuyaBLENumber] = []
    for number_mapping in mappings:
        if number_mapping.force_add or data.device.datapoints.has_id(
            number_mapping.dp_id, number_mapping.dp_type
        ):
            entities.append(
                TuyaBLENumber(
                    hass,
                    data.coordinator,
                    data.device,
                    data.product,
                    number_mapping,
                )
            )
    async_add_entities(entities)
