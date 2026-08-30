"""Select platform for Tuya BLE devices (temperature units, fingerbot modes)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from homeassistant.components.select import (
    SelectEntity,
    SelectEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    FINGERBOT_MODE_PROGRAM,
    FINGERBOT_MODE_PUSH,
    FINGERBOT_MODE_SWITCH,
)
from .devices import TuyaBLECoordinator, TuyaBLEData, TuyaBLEEntity, TuyaBLEProductInfo
from .tuya_ble import TuyaBLEDataPointType, TuyaBLEDevice

ICON_WEATHER_PARTLY_CLOUDY = "mdi:weather-partly-cloudy"


@dataclass
class TuyaBLESelectMapping:
    """Mapping of a Tuya BLE data point to a Home Assistant select entity."""

    dp_id: int
    description: SelectEntityDescription
    force_add: bool = True
    dp_type: TuyaBLEDataPointType | None = None


class TemperatureUnitDescription(SelectEntityDescription):
    """Select entity description for temperature unit selection."""

    key: str = "temperature_unit"
    icon: str = "mdi:thermometer"
    entity_category: EntityCategory = EntityCategory.CONFIG


class TuyaBLEFingerbotModeMapping(TuyaBLESelectMapping):
    """Select mapping for fingerbot mode selection (push/switch/program)."""

    def __init__(self, dp_id: int) -> None:
        super().__init__(
            dp_id=dp_id,
            description=SelectEntityDescription(
                key="fingerbot_mode",
                entity_category=EntityCategory.CONFIG,
                options=[
                    FINGERBOT_MODE_PUSH,
                    FINGERBOT_MODE_SWITCH,
                    FINGERBOT_MODE_PROGRAM,
                ],
            ),
        )


@dataclass
class TuyaBLECategorySelectMapping:
    """Container for product-specific and default select mappings."""

    products: dict[str, list[TuyaBLESelectMapping]] | None = None
    mapping: list[TuyaBLESelectMapping] | None = None


mapping: dict[str, TuyaBLECategorySelectMapping] = {
    "co2bj": TuyaBLECategorySelectMapping(
        products={
            "59s19z5m":  # CO2 Detector
            [
                TuyaBLESelectMapping(
                    dp_id=101,
                    description=TemperatureUnitDescription(
                        key="temperature_unit",
                        options=[
                            UnitOfTemperature.CELSIUS,
                            UnitOfTemperature.FAHRENHEIT,
                        ],
                    ),
                ),
            ],
        },
    ),
    "ms": TuyaBLECategorySelectMapping(
        products={
            k: [
                TuyaBLESelectMapping(
                    dp_id=31,
                    description=SelectEntityDescription(
                        key="beep_volume",
                        options=[
                            "mute",
                            "low",
                            "normal",
                            "high",
                        ],
                        entity_category=EntityCategory.CONFIG,
                    ),
                ),
            ]
            for k in ["ludzroix", "isk2p555"]
        }
    ),
    "szjqr": TuyaBLECategorySelectMapping(
        products={
            k: [
                cast(TuyaBLESelectMapping, TuyaBLEFingerbotModeMapping(dp_id=2)),
            ]
            for k in ["3yqdo5yt", "xhf790if"]
        }
        | {
            k: [
                cast(TuyaBLESelectMapping, TuyaBLEFingerbotModeMapping(dp_id=8)),
            ]
            for k in [
                "blliqpsj",
                "ndvkgsrm",
                "yiihr7zh",
                "neq16kgd",
            ]
        }
        | {
            k: [
                cast(TuyaBLESelectMapping, TuyaBLEFingerbotModeMapping(dp_id=8)),
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
    ),
    "wsdcg": TuyaBLECategorySelectMapping(
        products={
            "ojzlzzsw":  # Soil moisture sensor
            [
                TuyaBLESelectMapping(
                    dp_id=9,
                    description=TemperatureUnitDescription(
                        key="temperature_unit",
                        options=[
                            UnitOfTemperature.CELSIUS,
                            UnitOfTemperature.FAHRENHEIT,
                        ],
                        entity_registry_enabled_default=False,
                    ),
                ),
            ],
        },
    ),
    "znhsb": TuyaBLECategorySelectMapping(
        products={
            "cdlandip":  # Smart water bottle
            [
                TuyaBLESelectMapping(
                    dp_id=106,
                    description=TemperatureUnitDescription(
                        key="temperature_unit",
                        options=[
                            UnitOfTemperature.CELSIUS,
                            UnitOfTemperature.FAHRENHEIT,
                        ],
                    ),
                ),
                TuyaBLESelectMapping(
                    dp_id=107,
                    description=SelectEntityDescription(
                        key="reminder_mode",
                        options=[
                            "interval_reminder",
                            "alarm_reminder",
                        ],
                        entity_category=EntityCategory.CONFIG,
                    ),
                ),
            ],
        },
    ),
    "ggq": TuyaBLECategorySelectMapping(
        products={
            "fdrbxxbg": [  # Diivoo WT-05 dual water timer
                TuyaBLESelectMapping(
                    dp_id=117,
                    description=SelectEntityDescription(
                        key="weather_delay_zone1",
                        icon=ICON_WEATHER_PARTLY_CLOUDY,
                        options=[
                            "off",
                            "1h",
                            "2h",
                            "4h",
                            "8h",
                            "12h",
                            "24h",
                            "48h",
                            "72h",
                        ],
                        entity_category=EntityCategory.CONFIG,
                    ),
                ),
                TuyaBLESelectMapping(
                    dp_id=114,
                    description=SelectEntityDescription(
                        key="weather_delay_zone2",
                        icon=ICON_WEATHER_PARTLY_CLOUDY,
                        options=[
                            "off",
                            "1h",
                            "2h",
                            "4h",
                            "8h",
                            "12h",
                            "24h",
                            "48h",
                            "72h",
                        ],
                        entity_category=EntityCategory.CONFIG,
                    ),
                ),
            ],
        },
    ),
    "sfkzq": TuyaBLECategorySelectMapping(
        products={
            "nxquc5lb": [  # SOP10 water timer
                TuyaBLESelectMapping(
                    dp_id=10,
                    description=SelectEntityDescription(
                        key="weather_delay",
                        icon=ICON_WEATHER_PARTLY_CLOUDY,
                        options=[
                            "off",
                            "1h",
                            "2h",
                            "4h",
                            "8h",
                            "12h",
                            "24h",
                            "48h",
                            "72h",
                        ],
                        entity_category=EntityCategory.CONFIG,
                    ),
                ),
                TuyaBLESelectMapping(
                    dp_id=12,
                    description=SelectEntityDescription(
                        key="work_state",
                        icon="mdi:sprinkler",
                        options=["off", "manual", "auto"],
                        entity_category=EntityCategory.CONFIG,
                    ),
                ),
            ],
        },
    ),
}


def get_mapping_by_device(device: TuyaBLEDevice) -> list[TuyaBLESelectMapping]:
    """Get the select mappings for a device."""
    category = mapping.get(device.category)
    if category is not None and category.products is not None:
        product_mapping = category.products.get(device.product_id)
        if product_mapping is not None:
            return product_mapping
        if category.mapping is not None:
            return category.mapping
        return []
    return []


class TuyaBLESelect(TuyaBLEEntity, SelectEntity):
    """Representation of a Tuya BLE select."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: TuyaBLECoordinator,
        device: TuyaBLEDevice,
        product: TuyaBLEProductInfo,
        select_mapping: TuyaBLESelectMapping,
    ) -> None:
        super().__init__(hass, coordinator, device, product, select_mapping.description)
        self._mapping = select_mapping
        self._attr_options = select_mapping.description.options or []

    @property
    def current_option(self) -> str | None:
        """Return the selected entity option to represent the entity state."""
        # Raw value
        value: str | None = None
        datapoint = self.device.datapoints[self._mapping.dp_id]
        if datapoint:
            value = str(datapoint.value)
            if (
                isinstance(datapoint.value, int)
                and datapoint.value >= 0
                and datapoint.value < len(self._attr_options)
            ):
                return self._attr_options[datapoint.value]
            return value
        return None

    def select_option(self, option: str) -> None:
        """Change the selected option."""
        if option in self._attr_options:
            int_value = self._attr_options.index(option)
            datapoint = self.device.datapoints.get_or_create(
                self._mapping.dp_id,
                TuyaBLEDataPointType.DT_ENUM,
                int_value,
            )
            self.hass.create_task(datapoint.set_value(int_value))


async def async_setup_entry(  # noqa: S7503
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tuya BLE select entities for the config entry."""
    data: TuyaBLEData = hass.data[DOMAIN][entry.entry_id]
    mappings = get_mapping_by_device(data.device)
    entities: list[TuyaBLESelect] = []
    for select_mapping in mappings:
        if select_mapping.force_add or data.device.datapoints.has_id(
            select_mapping.dp_id, select_mapping.dp_type
        ):
            entities.append(
                TuyaBLESelect(
                    hass,
                    data.coordinator,
                    data.device,
                    data.product,
                    select_mapping,
                )
            )
    async_add_entities(entities)
