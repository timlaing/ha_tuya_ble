"""Valve platform for Tuya BLE water valves and irrigation controllers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.valve.const import ValveDeviceClass, ValveEntityFeature
from homeassistant.components.valve.entity import ValveEntity, ValveEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .devices import (
    TuyaBLECoordinator,
    TuyaBLEData,
    TuyaBLEEntity,
    TuyaBLEProductInfo,
)
from .tuya_ble import TuyaBLEDataPointType, TuyaBLEDevice

TuyaBLEValveGetter = Callable[["TuyaBLEValve", TuyaBLEProductInfo], bool | None] | None


TuyaBLEValveIsAvailable = Callable[["TuyaBLEValve", TuyaBLEProductInfo], bool] | None


TuyaBLEValveSetter = Callable[["TuyaBLEValve", TuyaBLEProductInfo, bool], None] | None


@dataclass
class TuyaBLEValveMapping:
    """Mapping of a Tuya BLE data point to a Home Assistant valve entity."""

    dp_id: int
    description: ValveEntityDescription
    force_add: bool = True
    dp_type: TuyaBLEDataPointType | None = None
    is_available: TuyaBLEValveIsAvailable = None
    getter: TuyaBLEValveGetter = None
    setter: TuyaBLEValveSetter = None


@dataclass
class TuyaBLECategoryValveMapping:
    """Container for product-specific and default valve mappings."""

    products: dict[str, list[TuyaBLEValveMapping]] | None = None
    mapping: list[TuyaBLEValveMapping] | None = None


# pylint: disable=unexpected-keyword-arg
# ValveEntityDescription accepts key and entity_registry_enabled_default,
# but pylint cannot resolve the constructor through the FrozenOrThawed wrapper.
mapping: dict[str, TuyaBLECategoryValveMapping] = {
    "ggq": TuyaBLECategoryValveMapping(
        products={
            **{
                k: [
                    TuyaBLEValveMapping(
                        dp_id=1,
                        description=ValveEntityDescription(
                            key="water_valve",
                            device_class=ValveDeviceClass.WATER,
                            entity_registry_enabled_default=True,
                        ),
                    )
                ]
                for k in ["6pahkcau", "hfgdqhho"]
            },
            **{
                k: [
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
                ]
                for k in ["qycalacn", "fnlw6npo", "jjqi2syk"]
            },
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
        },
    ),
    "sfkzq": TuyaBLECategoryValveMapping(
        products={
            **{
                k: [
                    TuyaBLEValveMapping(
                        dp_id=1,
                        description=ValveEntityDescription(
                            key="valve",
                            device_class=ValveDeviceClass.WATER,
                            entity_registry_enabled_default=True,
                        ),
                    )
                ]
                for k in [
                    "nxquc5lb",
                    "c8800fd30884068f",
                    "so5ybnw9",
                    "16wgjvck",
                    "0axr5s0b",
                    "svhikeyq",
                    "46zia2nz",
                    "1fcnd8xk",
                    "ldcdnigc",
                ]
            },
        },
    ),
}


def get_mapping_by_device(device: TuyaBLEDevice) -> list[TuyaBLEValveMapping]:
    """Get the valve mappings for a device."""
    category = mapping.get(device.category)
    if category is not None and category.products is not None:
        product_mapping = category.products.get(device.product_id)
        if product_mapping is not None:
            return product_mapping
        if category.mapping is not None:
            return category.mapping
        return []
    return []


class TuyaBLEValve(TuyaBLEEntity, ValveEntity):
    """Representation of a Tuya BLE Valve."""

    _attr_supported_features = ValveEntityFeature.OPEN | ValveEntityFeature.CLOSE

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: TuyaBLECoordinator,
        device: TuyaBLEDevice,
        product: TuyaBLEProductInfo,
        valve_mapping: TuyaBLEValveMapping,
    ) -> None:
        super().__init__(hass, coordinator, device, product, valve_mapping.description)
        self._mapping = valve_mapping

    @property
    def is_closed(self) -> bool | None:
        """Return true if the valve is closed."""
        if self._mapping.getter is not None:
            return self._mapping.getter(self, self._product)

        datapoint = self.device.datapoints[self._mapping.dp_id]
        if datapoint:
            return not bool(datapoint.value)
        return None

    def open_valve(self) -> None:
        """Open the valve."""
        if self._mapping.setter:
            self._mapping.setter(self, self._product, True)
            return

        datapoint = self.device.datapoints.get_or_create(
            self._mapping.dp_id,
            TuyaBLEDataPointType.DT_BOOL,
            True,
        )
        self.hass.create_task(datapoint.set_value(True))

    def close_valve(self) -> None:
        """Close the valve."""
        if self._mapping.setter:
            self._mapping.setter(self, self._product, False)
            return

        datapoint = self.device.datapoints.get_or_create(
            self._mapping.dp_id,
            TuyaBLEDataPointType.DT_BOOL,
            False,
        )
        self.hass.create_task(datapoint.set_value(False))

    def stop_valve(self) -> None:
        """Stop the valve (close it)."""
        self.close_valve()

    def set_valve_position(self, position: int) -> None:
        """Set the valve position. Not supported by this device."""

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
    """Set up the Tuya BLE valves."""
    data: TuyaBLEData = hass.data[DOMAIN][entry.entry_id]
    mappings = get_mapping_by_device(data.device)
    entities: list[TuyaBLEValve] = []
    for valve_mapping in mappings:
        if valve_mapping.force_add or data.device.datapoints.has_id(
            valve_mapping.dp_id, valve_mapping.dp_type
        ):
            entities.append(
                TuyaBLEValve(
                    hass,
                    data.coordinator,
                    data.device,
                    data.product,
                    valve_mapping,
                )
            )
    async_add_entities(entities)
