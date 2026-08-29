"""Number platform for Tuya BLE devices."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
)
from homeassistant.components.number.const import NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .device_descriptors.handlers.fingerbot.mode import in_push_mode
from .device_registry import EntityDescriptor, get_registry
from .devices import TuyaBLECoordinator, TuyaBLEData, TuyaBLEEntity, TuyaBLEProductInfo
from .tuya_ble import TuyaBLEDataPointType, TuyaBLEDevice

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


@dataclass
class TuyaBLEHoldTimeMapping(TuyaBLENumberMapping):
    """Number mapping for fingerbot hold time with push-mode availability."""

    is_available: TuyaBLENumberIsAvailable = in_push_mode


@dataclass
class TuyaBLECategoryNumberMapping:
    """Data class grouping number entity mappings by product or category."""

    products: dict[str, list[TuyaBLENumberMapping]] | None = None
    mapping: list[TuyaBLENumberMapping] | None = None


def _number_description(desc: EntityDescriptor) -> NumberEntityDescription:
    """Build a NumberEntityDescription from a registry descriptor."""
    kwargs: dict[str, object] = {
        "key": desc.translation_key or str(desc.dp_id),
    }
    if desc.icon is not None:
        kwargs["icon"] = desc.icon
    if desc.device_class is not None:
        kwargs["device_class"] = desc.device_class
    if desc.unit is not None:
        kwargs["native_unit_of_measurement"] = desc.unit
    if desc.entity_category is not None:
        kwargs["entity_category"] = desc.entity_category
    if desc.min_value is not None:
        kwargs["native_min_value"] = desc.min_value
    if desc.max_value is not None:
        kwargs["native_max_value"] = desc.max_value
    if desc.step is not None:
        kwargs["native_step"] = desc.step
    if desc.name is not None:
        kwargs["name"] = desc.name
    return NumberEntityDescription(**kwargs)  # type: ignore[arg-type]


def _number_mode(mode: str | None) -> NumberMode:
    """Resolve a YAML mode string to an HA NumberMode."""
    if mode == "slider":
        return NumberMode.SLIDER
    return NumberMode.BOX


def _build_number_mapping(desc: EntityDescriptor) -> TuyaBLENumberMapping:
    """Construct a number mapping from a registry descriptor."""
    kwargs: dict[str, Any] = {
        "dp_id": desc.dp_id,
        "description": _number_description(desc),
        "force_add": desc.force_add,
        "coefficient": desc.coefficient,
        "mode": _number_mode(desc.mode),
    }
    if (is_available := desc.resolved_handler("when")) is not None:
        kwargs["is_available"] = is_available
    if (getter := desc.resolved_handler("read")) is not None:
        kwargs["getter"] = getter
    if (setter := desc.resolved_handler("write")) is not None:
        kwargs["setter"] = setter
    if desc.dp_type is not None:
        kwargs["dp_type"] = TuyaBLEDataPointType(desc.dp_type)
    return TuyaBLENumberMapping(**kwargs)


def _build_mapping() -> dict[str, TuyaBLECategoryNumberMapping]:
    """Build the number mappings dict from the device registry."""
    result: dict[str, TuyaBLECategoryNumberMapping] = {}
    for device_entities in get_registry().products.values():
        descriptors = device_entities.get("number")
        if not descriptors:
            continue
        category_mapping = result.setdefault(
            device_entities.category,
            TuyaBLECategoryNumberMapping(products={}),
        )
        assert category_mapping.products is not None
        category_mapping.products[device_entities.product_id] = [
            _build_number_mapping(desc) for desc in descriptors
        ]
    return result


mapping: dict[str, TuyaBLECategoryNumberMapping] = _build_mapping()


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
