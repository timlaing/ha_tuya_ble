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
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .device_registry import EntityDescriptor, get_registry
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


def _binary_sensor_description(
    desc: EntityDescriptor,
) -> BinarySensorEntityDescription:
    """Build a BinarySensorEntityDescription from a descriptor."""
    return BinarySensorEntityDescription(
        key=desc.translation_key or str(desc.dp_id),
        icon=desc.icon,
        device_class=(
            BinarySensorDeviceClass(desc.device_class)
            if desc.device_class is not None
            else None
        ),
        entity_category=desc.resolved_entity_category(),
    )


def _build_binary_sensor_mapping(
    desc: EntityDescriptor,
) -> TuyaBLEBinarySensorMapping:
    """Construct a binary sensor mapping from a registry descriptor."""
    return TuyaBLEBinarySensorMapping(
        dp_id=desc.dp_id,
        description=_binary_sensor_description(desc),
        force_add=desc.force_add,
        dp_type=(
            TuyaBLEDataPointType(desc.dp_type) if desc.dp_type is not None else None
        ),
        getter=desc.resolved_handler("read"),
        is_available=desc.resolved_handler("when"),
    )


def _build_mapping() -> dict[str, TuyaBLECategoryBinarySensorMapping]:
    """Build the binary sensor mappings dict from the device registry."""
    result: dict[str, TuyaBLECategoryBinarySensorMapping] = {}
    for device_entities in get_registry().products.values():
        descriptors = device_entities.get("binary_sensor")
        if not descriptors:
            continue
        category_mapping = result.setdefault(
            device_entities.category,
            TuyaBLECategoryBinarySensorMapping(products={}),
        )
        assert category_mapping.products is not None
        category_mapping.products[device_entities.product_id] = [
            _build_binary_sensor_mapping(desc) for desc in descriptors
        ]
    return result


mapping: dict[str, TuyaBLECategoryBinarySensorMapping] = _build_mapping()


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
