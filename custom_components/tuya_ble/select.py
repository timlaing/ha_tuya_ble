"""Select platform for Tuya BLE devices (temperature units, fingerbot modes)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict, cast

from homeassistant.components.select import (
    SelectEntity,
    SelectEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    FINGERBOT_MODE_PROGRAM,
    FINGERBOT_MODE_PUSH,
    FINGERBOT_MODE_SWITCH,
)
from .device_registry import EntityDescriptor, get_registry
from .devices import TuyaBLECoordinator, TuyaBLEData, TuyaBLEEntity, TuyaBLEProductInfo
from .tuya_ble import TuyaBLEDataPointType, TuyaBLEDevice


@dataclass
class TuyaBLESelectMapping:
    """Mapping of a Tuya BLE data point to a Home Assistant select entity."""

    dp_id: int
    description: SelectEntityDescription
    force_add: bool = True
    dp_type: TuyaBLEDataPointType | None = None
    values: list[str] | None = None


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


class _SelectDescriptionKwargs(TypedDict, total=False):
    """Typed kwargs for SelectEntityDescription construction."""

    key: str
    name: str | None
    icon: str | None
    entity_category: EntityCategory | None
    options: list[str] | None
    entity_registry_enabled_default: bool


def _select_description(desc: EntityDescriptor) -> SelectEntityDescription:
    """Build a SelectEntityDescription from a descriptor."""
    description_class: type[SelectEntityDescription] = SelectEntityDescription
    if desc.translation_key == "temperature_unit":
        description_class = TemperatureUnitDescription
    kwargs: _SelectDescriptionKwargs = {
        "key": desc.translation_key or str(desc.dp_id),
        "name": desc.name,
    }
    if desc.icon is not None:
        kwargs["icon"] = desc.icon
    if desc.entity_category is not None:
        kwargs["entity_category"] = desc.resolved_entity_category()
    if desc.options is not None:
        kwargs["options"] = desc.options
    if desc.enabled_by_default is False:
        kwargs["entity_registry_enabled_default"] = False
    return description_class(**kwargs)


def _build_select_mapping(desc: EntityDescriptor) -> TuyaBLESelectMapping:
    """Construct a select mapping from a registry descriptor."""
    if desc.kind == "fingerbot_mode":
        return cast(TuyaBLESelectMapping, TuyaBLEFingerbotModeMapping(dp_id=desc.dp_id))
    return TuyaBLESelectMapping(
        dp_id=desc.dp_id,
        description=_select_description(desc),
        force_add=desc.force_add,
        dp_type=(
            TuyaBLEDataPointType(desc.dp_type) if desc.dp_type is not None else None
        ),
        values=desc.values,
    )


def _build_mapping() -> dict[str, TuyaBLECategorySelectMapping]:
    """Build the select mappings dict from the device registry."""
    result: dict[str, TuyaBLECategorySelectMapping] = {}
    for device_entities in get_registry().products.values():
        descriptors = device_entities.get("select")
        if not descriptors:
            continue
        category_mapping = result.setdefault(
            device_entities.category,
            TuyaBLECategorySelectMapping(products={}),
        )
        assert category_mapping.products is not None
        category_mapping.products[device_entities.product_id] = [
            _build_select_mapping(desc) for desc in descriptors
        ]
    return result


mapping: dict[str, TuyaBLECategorySelectMapping] = _build_mapping()


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
            if self._mapping.values:
                for index, mapped_value in enumerate(self._mapping.values):
                    if mapped_value == value and index < len(self._attr_options):
                        return self._attr_options[index]
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
            if self._mapping.values:
                if int_value >= len(self._mapping.values):
                    return
                option_value = self._mapping.values[int_value]
                datapoint = self.device.datapoints.get_or_create(
                    self._mapping.dp_id,
                    self._mapping.dp_type or TuyaBLEDataPointType.DT_STRING,
                    option_value,
                )
                self.hass.create_task(datapoint.set_value(option_value))
            else:
                datapoint = self.device.datapoints.get_or_create(
                    self._mapping.dp_id,
                    self._mapping.dp_type or TuyaBLEDataPointType.DT_ENUM,
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
