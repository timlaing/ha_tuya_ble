"""The Tuya BLE integration."""
from __future__ import annotations

from dataclasses import dataclass, field

import logging
from typing import Any, Callable

from homeassistant.components.valve import (
    ValveDeviceClass,
    ValveEntity,
    ValveEntityDescription,
    ValveEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN
from .devices import TuyaBLEData, TuyaBLEEntity, TuyaBLEProductInfo
from .tuya_ble import TuyaBLEDataPointType, TuyaBLEDevice

_LOGGER = logging.getLogger(__name__)


TuyaBLEValveGetter = (
    Callable[["TuyaBLEValve", TuyaBLEProductInfo], bool | None] | None
)


TuyaBLEValveIsAvailable = (
    Callable[["TuyaBLEValve", TuyaBLEProductInfo], bool] | None
)


TuyaBLEValveSetter = (
    Callable[["TuyaBLEValve", TuyaBLEProductInfo, bool], None] | None
)


@dataclass
class TuyaBLEValveMapping:
    dp_id: int
    description: ValveEntityDescription
    force_add: bool = True
    dp_type: TuyaBLEDataPointType | None = None
    is_available: TuyaBLEValveIsAvailable = None
    getter: TuyaBLEValveGetter = None
    setter: TuyaBLEValveSetter = None


@dataclass
class TuyaBLECategoryValveMapping:
    products: dict[str, list[TuyaBLEValveMapping]] | None = None
    mapping: list[TuyaBLEValveMapping] | None = None


mapping: dict[str, TuyaBLECategoryValveMapping] = {
    "ggq": TuyaBLECategoryValveMapping(
        products={
            "6pahkcau": [  # Irrigation computer
                TuyaBLEValveMapping(
                    dp_id=1,
                    description=ValveEntityDescription(
                        key="water_valve",
                        device_class=ValveDeviceClass.WATER,
                        entity_registry_enabled_default=True,
                    ),
                ),
            ],
        },
    ),
    "sfkzq": TuyaBLECategoryValveMapping(
        products={
            "fdrbxxbg": [  # Diivoo WT-05 dual water timer
                TuyaBLEValveMapping(
                    dp_id=105,
                    description=ValveEntityDescription(
                        key="valve_zone1",
                        device_class=ValveDeviceClass.WATER,
                        entity_registry_enabled_default=True,
                    ),
                ),
                TuyaBLEValveMapping(
                    dp_id=104,
                    description=ValveEntityDescription(
                        key="valve_zone2",
                        device_class=ValveDeviceClass.WATER,
                        entity_registry_enabled_default=True,
                    ),
                ),
            ],
            "nxquc5lb": [  # SOP10 water timer
                TuyaBLEValveMapping(
                    dp_id=1,
                    description=ValveEntityDescription(
                        key="valve",
                        device_class=ValveDeviceClass.WATER,
                        entity_registry_enabled_default=True,
                    ),
                ),
            ],
        },
    ),
}


def get_mapping_by_device(device: TuyaBLEDevice) -> list[TuyaBLEValveMapping]:
    category = mapping.get(device.category)
    if category is not None and category.products is not None:
        product_mapping = category.products.get(device.product_id)
        if product_mapping is not None:
            return product_mapping
        if category.mapping is not None:
            return category.mapping
        else:
            return []
    else:
        return []


class TuyaBLEValve(TuyaBLEEntity, ValveEntity):
    """Representation of a Tuya BLE Valve."""

    _attr_supported_features = ValveEntityFeature.OPEN | ValveEntityFeature.CLOSE

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: DataUpdateCoordinator,
        device: TuyaBLEDevice,
        product: TuyaBLEProductInfo,
        mapping: TuyaBLEValveMapping,
    ) -> None:
        super().__init__(hass, coordinator, device, product, mapping.description)
        self._mapping = mapping

    @property
    def is_closed(self) -> bool | None:
        """Return true if the valve is closed."""
        if self._mapping.getter:
            return self._mapping.getter(self, self._product)

        datapoint = self._device.datapoints[self._mapping.dp_id]
        if datapoint:
            return not bool(datapoint.value)
        return None

    def open_valve(self) -> None:
        """Open the valve."""
        if self._mapping.setter:
            return self._mapping.setter(self, self._product, True)

        datapoint = self._device.datapoints.get_or_create(
            self._mapping.dp_id,
            TuyaBLEDataPointType.DT_BOOL,
            True,
        )
        if datapoint:
            self._hass.create_task(datapoint.set_value(True))

    def close_valve(self) -> None:
        """Close the valve."""
        if self._mapping.setter:
            return self._mapping.setter(self, self._product, False)

        datapoint = self._device.datapoints.get_or_create(
            self._mapping.dp_id,
            TuyaBLEDataPointType.DT_BOOL,
            False,
        )
        if datapoint:
            self._hass.create_task(datapoint.set_value(False))

    def stop_valve(self) -> None:
        """Stop the valve (close it)."""
        self.close_valve()

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        result = super().available
        if result and self._mapping.is_available:
            result = self._mapping.is_available(self, self._product)
        return result


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Tuya BLE valves."""
    data: TuyaBLEData = hass.data[DOMAIN][entry.entry_id]
    mappings = get_mapping_by_device(data.device)
    entities: list[TuyaBLEValve] = []
    for mapping in mappings:
        if mapping.force_add or data.device.datapoints.has_id(
            mapping.dp_id, mapping.dp_type
        ):
            entities.append(
                TuyaBLEValve(
                    hass,
                    data.coordinator,
                    data.device,
                    data.product,
                    mapping,
                )
            )
    async_add_entities(entities)
