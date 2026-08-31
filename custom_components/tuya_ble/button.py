"""Button platform for Tuya BLE devices (fingerbot push triggers)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from homeassistant.components.button import (
    ButtonEntity,
    ButtonEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .device_descriptors.handlers.fingerbot.mode import in_push_mode
from .device_registry import EntityDescriptor, get_registry
from .devices import TuyaBLECoordinator, TuyaBLEData, TuyaBLEEntity, TuyaBLEProductInfo
from .tuya_ble import TuyaBLEDataPointType, TuyaBLEDevice

TuyaBLEButtonIsAvailable = Callable[["TuyaBLEButton", TuyaBLEProductInfo], bool] | None


@dataclass
class TuyaBLEButtonMapping:
    """Mapping of a Tuya BLE data point to a Home Assistant button entity."""

    dp_id: int
    description: ButtonEntityDescription
    force_add: bool = True
    dp_type: TuyaBLEDataPointType | None = None
    is_available: TuyaBLEButtonIsAvailable = None


@dataclass
class TuyaBLEFingerbotModeMapping(TuyaBLEButtonMapping):
    """Button mapping for triggering fingerbot push mode."""

    description: ButtonEntityDescription = field(
        default_factory=lambda: ButtonEntityDescription(
            key="push",
        )
    )
    is_available: TuyaBLEButtonIsAvailable = in_push_mode


@dataclass
class TuyaBLECategoryButtonMapping:
    """Container for product-specific and default button mappings."""

    products: dict[str, list[TuyaBLEButtonMapping]] | None = None
    mapping: list[TuyaBLEButtonMapping] | None = None


_KIND_CLASSES: dict[str, type[TuyaBLEButtonMapping]] = {
    "fingerbot_mode": TuyaBLEFingerbotModeMapping,
}


def _button_description(desc: EntityDescriptor) -> ButtonEntityDescription:
    """Build a ButtonEntityDescription from a descriptor."""
    return ButtonEntityDescription(
        key=desc.translation_key or str(desc.dp_id),
        icon=desc.icon,
        entity_category=desc.resolved_entity_category(),
    )


def _build_button_mapping(desc: EntityDescriptor) -> TuyaBLEButtonMapping:
    """Construct a button mapping from a registry descriptor."""
    cls = _KIND_CLASSES.get(desc.kind or "", TuyaBLEButtonMapping)
    return cls(
        dp_id=desc.dp_id,
        description=_button_description(desc),
        force_add=desc.force_add,
        dp_type=(
            TuyaBLEDataPointType(desc.dp_type) if desc.dp_type is not None else None
        ),
        is_available=desc.resolved_handler("when"),
    )


def _build_mapping() -> dict[str, TuyaBLECategoryButtonMapping]:
    """Build the button mappings dict from the device registry."""
    result: dict[str, TuyaBLECategoryButtonMapping] = {}
    for device_entities in get_registry().products.values():
        descriptors = device_entities.get("button")
        if not descriptors:
            continue
        category_mapping = result.setdefault(
            device_entities.category,
            TuyaBLECategoryButtonMapping(products={}),
        )
        assert category_mapping.products is not None
        category_mapping.products[device_entities.product_id] = [
            _build_button_mapping(desc) for desc in descriptors
        ]
    return result


mapping: dict[str, TuyaBLECategoryButtonMapping] = _build_mapping()


def get_mapping_by_device(device: TuyaBLEDevice) -> list[TuyaBLEButtonMapping]:
    """Get the button mappings for a device."""
    category = mapping.get(device.category)
    if category is not None and category.products is not None:
        product_mapping = category.products.get(device.product_id)
        if product_mapping is not None:
            return product_mapping
        if category.mapping is not None:
            return category.mapping
        return []
    return []


class TuyaBLEButton(TuyaBLEEntity, ButtonEntity):
    """Representation of a Tuya BLE Button."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: TuyaBLECoordinator,
        device: TuyaBLEDevice,
        product: TuyaBLEProductInfo,
        button_mapping: TuyaBLEButtonMapping,
    ) -> None:
        super().__init__(hass, coordinator, device, product, button_mapping.description)
        self._mapping = button_mapping

    def press(self) -> None:
        """Toggle the mapped datapoint's boolean value on the device."""
        datapoint = self.device.datapoints.get_or_create(
            self._mapping.dp_id,
            TuyaBLEDataPointType.DT_BOOL,
            False,
        )
        self.hass.create_task(datapoint.set_value(not bool(datapoint.value)))

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
    """Set up Tuya BLE button entities for the config entry."""
    data: TuyaBLEData = hass.data[DOMAIN][entry.entry_id]
    mappings = get_mapping_by_device(data.device)
    entities: list[TuyaBLEButton] = []
    for button_mapping in mappings:
        if button_mapping.force_add or data.device.datapoints.has_id(
            button_mapping.dp_id, button_mapping.dp_type
        ):
            entities.append(
                TuyaBLEButton(
                    hass,
                    data.coordinator,
                    data.device,
                    data.product,
                    button_mapping,
                )
            )
    async_add_entities(entities)
