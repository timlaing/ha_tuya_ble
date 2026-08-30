"""Binary sensor platform for Tuya BLE devices."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
)
from .devices import TuyaBLECoordinator, TuyaBLEData, TuyaBLEEntity, TuyaBLEProductInfo
from .tuya_ble import TuyaBLEDataPointType, TuyaBLEDevice

SIGNAL_STRENGTH_DP_ID = -1


TuyaBLEBinarySensorIsAvailable = (
    Callable[["TuyaBLEBinarySensor", TuyaBLEProductInfo], bool] | None
)


@dataclass
class TuyaBLEBinarySensorMapping:
    """Mapping of a Tuya BLE data point to a Home Assistant binary sensor entity."""

    dp_id: int
    description: BinarySensorEntityDescription
    force_add: bool = True
    dp_type: TuyaBLEDataPointType | None = None
    getter: Callable[[TuyaBLEBinarySensor], None] | None = None
    is_available: TuyaBLEBinarySensorIsAvailable = None


@dataclass
class TuyaBLECategoryBinarySensorMapping:
    """Container for product-specific and default binary sensor mappings."""

    products: dict[str, list[TuyaBLEBinarySensorMapping]] | None = None
    mapping: list[TuyaBLEBinarySensorMapping] | None = None


mapping: dict[str, TuyaBLECategoryBinarySensorMapping] = {
    "wk": TuyaBLECategoryBinarySensorMapping(
        products={
            "drlajpqc": [  # Thermostatic Radiator Valve
                TuyaBLEBinarySensorMapping(
                    dp_id=105,
                    description=BinarySensorEntityDescription(
                        key="battery",
                        device_class=BinarySensorDeviceClass.BATTERY,
                        entity_category=EntityCategory.DIAGNOSTIC,
                    ),
                ),
            ],
        },
    ),
    "sfkzq": TuyaBLECategoryBinarySensorMapping(
        products={
            **{
                k: [
                    TuyaBLEBinarySensorMapping(
                        dp_id=4,
                        description=BinarySensorEntityDescription(
                            key="fault_code",
                            device_class=BinarySensorDeviceClass.PROBLEM,
                            entity_category=EntityCategory.DIAGNOSTIC,
                        ),
                    )
                ]
                for k in [
                    "nxquc5lb",
                    "c8800fd30884068f",
                    "so5ybnw9",
                ]  # Smart water timer - SOP10 / Water timer valve
            },
        },
    ),
}


def get_mapping_by_device(device: TuyaBLEDevice) -> list[TuyaBLEBinarySensorMapping]:
    """Get the binary sensor mappings for a device."""
    category = mapping.get(device.category)
    if category is not None and category.products is not None:
        product_mapping = category.products.get(device.product_id)
        if product_mapping is not None:
            return product_mapping
        if category.mapping is not None:
            return category.mapping
        return []
    return []


class TuyaBLEBinarySensor(TuyaBLEEntity, BinarySensorEntity):
    """Representation of a Tuya BLE binary sensor."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: TuyaBLECoordinator,
        device: TuyaBLEDevice,
        product: TuyaBLEProductInfo,
        binary_sensor_mapping: TuyaBLEBinarySensorMapping,
    ) -> None:
        super().__init__(
            hass, coordinator, device, product, binary_sensor_mapping.description
        )
        self._mapping = binary_sensor_mapping

    @callback
    def _handle_coordinator_update(self) -> None:
        """Read the mapped datapoint and update the binary state."""
        if self._mapping.getter is not None:
            self._mapping.getter(self)
        else:
            datapoint = self.device.datapoints[self._mapping.dp_id]
            if datapoint:
                self._attr_is_on = bool(datapoint.value)
        self.async_write_ha_state()

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
    """Set up Tuya BLE binary sensor entities for the config entry."""
    data: TuyaBLEData = hass.data[DOMAIN][entry.entry_id]
    mappings = get_mapping_by_device(data.device)
    entities: list[TuyaBLEBinarySensor] = []
    for binary_sensor_mapping in mappings:
        if binary_sensor_mapping.force_add or data.device.datapoints.has_id(
            binary_sensor_mapping.dp_id, binary_sensor_mapping.dp_type
        ):
            entities.append(
                TuyaBLEBinarySensor(
                    hass,
                    data.coordinator,
                    data.device,
                    data.product,
                    binary_sensor_mapping,
                )
            )
    async_add_entities(entities)
