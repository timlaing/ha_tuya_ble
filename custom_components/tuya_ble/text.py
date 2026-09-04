"""Text platform for Tuya BLE devices (fingerbot program sequences)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.text import (
    TextEntity,
    TextEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .device_registry import EntityDescriptor, get_registry
from .devices import TuyaBLECoordinator, TuyaBLEData, TuyaBLEEntity, TuyaBLEProductInfo
from .tuya_ble import TuyaBLEDataPointType, TuyaBLEDevice

SIGNAL_STRENGTH_DP_ID = -1

TuyaBLETextGetter = Callable[["TuyaBLEText", TuyaBLEProductInfo], str | None] | None


TuyaBLETextIsAvailable = Callable[["TuyaBLEText", TuyaBLEProductInfo], bool] | None


TuyaBLETextSetter = Callable[["TuyaBLEText", TuyaBLEProductInfo, str], None] | None


@dataclass
class TuyaBLETextMapping:
    """Mapping of a Tuya BLE data point to a Home Assistant text entity."""

    dp_id: int
    description: TextEntityDescription
    force_add: bool = True
    dp_type: TuyaBLEDataPointType | None = None
    default_value: str | None = None
    is_available: TuyaBLETextIsAvailable = None
    getter: TuyaBLETextGetter = None
    setter: TuyaBLETextSetter = None


@dataclass
class TuyaBLECategoryTextMapping:
    """Container for product-specific and default text mappings."""

    products: dict[str, list[TuyaBLETextMapping]] | None = None
    mapping: list[TuyaBLETextMapping] | None = None


def _text_description(desc: EntityDescriptor) -> TextEntityDescription:
    """Build a TextEntityDescription from a descriptor."""
    return TextEntityDescription(
        key=desc.translation_key or str(desc.dp_id),
        name=desc.name,
        icon=desc.icon,
        pattern=desc.pattern,
        entity_category=desc.resolved_entity_category(),
    )


def _build_text_mapping(desc: EntityDescriptor) -> TuyaBLETextMapping:
    """Construct a text mapping from a registry descriptor."""
    return TuyaBLETextMapping(
        dp_id=desc.dp_id,
        description=_text_description(desc),
        force_add=desc.force_add,
        dp_type=(
            TuyaBLEDataPointType(desc.dp_type) if desc.dp_type is not None else None
        ),
        default_value=None,
        is_available=desc.resolved_handler("when"),
        getter=desc.resolved_handler("read"),
        setter=desc.resolved_handler("write"),
    )


def _build_mapping() -> dict[str, TuyaBLECategoryTextMapping]:
    """Build the text mappings dict from the device registry."""
    result: dict[str, TuyaBLECategoryTextMapping] = {}
    for device_entities in get_registry().products.values():
        descriptors = device_entities.get("text")
        if not descriptors:
            continue
        category_mapping = result.setdefault(
            device_entities.category,
            TuyaBLECategoryTextMapping(products={}),
        )
        assert category_mapping.products is not None
        category_mapping.products[device_entities.product_id] = [
            _build_text_mapping(desc) for desc in descriptors
        ]
    return result


mapping: dict[str, TuyaBLECategoryTextMapping] = _build_mapping()


def get_mapping_by_device(device: TuyaBLEDevice) -> list[TuyaBLETextMapping]:
    """Get the text mappings for a device."""
    category = mapping.get(device.category)
    if category is not None and category.products is not None:
        product_mapping = category.products.get(device.product_id)
        if product_mapping is not None:
            return product_mapping
        if category.mapping is not None:
            return category.mapping
        return []
    return []


class TuyaBLEText(TuyaBLEEntity, TextEntity):
    """Representation of a Tuya BLE text entity."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: TuyaBLECoordinator,
        device: TuyaBLEDevice,
        product: TuyaBLEProductInfo,
        text_mapping: TuyaBLETextMapping,
    ) -> None:
        super().__init__(hass, coordinator, device, product, text_mapping.description)
        self._mapping = text_mapping

    @property
    def available(self) -> bool:
        """True when coordinator is connected and the availability predicate passes."""
        result = super().available
        if result and self._mapping.is_available:
            result = self._mapping.is_available(self, self._product)
        return result

    @property
    def native_value(self) -> str | None:
        """Return the value reported by the text."""
        if self._mapping.getter is not None:
            return self._mapping.getter(self, self._product)

        datapoint = self.device.datapoints[self._mapping.dp_id]
        if datapoint:
            return str(datapoint.value)

        return self._mapping.default_value

    def set_value(self, value: str) -> None:
        """Send the new string value to the device."""
        if self._mapping.setter:
            self._mapping.setter(self, self._product, value)
            return
        datapoint = self.device.datapoints.get_or_create(
            self._mapping.dp_id,
            TuyaBLEDataPointType.DT_STRING,
            value,
        )
        self.hass.create_task(datapoint.set_value(value))


async def async_setup_entry(  # noqa: S7503
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tuya BLE text entities for the config entry."""
    data: TuyaBLEData = hass.data[DOMAIN][entry.entry_id]
    mappings = get_mapping_by_device(data.device)
    entities: list[TuyaBLEText] = []
    for text_mapping in mappings:
        if text_mapping.force_add or data.device.datapoints.has_id(
            text_mapping.dp_id, text_mapping.dp_type
        ):
            entities.append(
                TuyaBLEText(
                    hass,
                    data.coordinator,
                    data.device,
                    data.product,
                    text_mapping,
                )
            )
    async_add_entities(entities)
