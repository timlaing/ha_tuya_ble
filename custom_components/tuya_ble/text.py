"""Text platform for Tuya BLE devices (fingerbot program sequences)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from struct import pack, unpack

from homeassistant.components.text import (
    TextEntity,
    TextEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
)
from .devices import TuyaBLECoordinator, TuyaBLEData, TuyaBLEEntity, TuyaBLEProductInfo
from .fingerbot import is_fingerbot_in_program_mode
from .tuya_ble import TuyaBLEDataPointType, TuyaBLEDevice

SIGNAL_STRENGTH_DP_ID = -1

TuyaBLETextGetter = Callable[["TuyaBLEText", TuyaBLEProductInfo], str | None] | None


TuyaBLETextIsAvailable = Callable[["TuyaBLEText", TuyaBLEProductInfo], bool] | None


TuyaBLETextSetter = Callable[["TuyaBLEText", TuyaBLEProductInfo, str], None] | None


def get_fingerbot_program(
    self: TuyaBLEText,
    product: TuyaBLEProductInfo,
) -> str | None:
    """Get the fingerbot program as a formatted string."""
    result: str | None = None
    if product.fingerbot and product.fingerbot.program:
        datapoint = self.device.datapoints[product.fingerbot.program]
        if datapoint and isinstance(datapoint.value, bytes):
            result = ""
            step_count: int = datapoint.value[3]
            for step in range(step_count):
                result += _format_program_step(datapoint.value, step)
    return result


def _format_program_step(program_bytes: bytes, step: int) -> str:
    """Format a single program step into its string representation."""
    step_pos = 4 + step * 3
    step_data = program_bytes[step_pos : step_pos + 3]
    position, delay = unpack(">BH", step_data)
    delay = min(delay, 9999)
    return (
        (";" if step > 0 else "")
        + str(position)
        + (("/" + str(delay)) if delay > 0 else "")
    )


def set_fingerbot_program(
    self: TuyaBLEText,
    product: TuyaBLEProductInfo,
    value: str,
) -> None:
    """Set the fingerbot program from a formatted string."""
    if product.fingerbot and product.fingerbot.program:
        datapoint = self.device.datapoints[product.fingerbot.program]
        if datapoint and isinstance(datapoint.value, bytes):
            new_value = bytearray(datapoint.value[0:3])
            steps = value.split(";")
            new_value += int.to_bytes(len(steps), 1, "big")
            for step in steps:
                step_values = step.split("/")
                position = int(step_values[0])
                delay = int(step_values[1]) if len(step_values) > 1 else 0
                new_value += pack(">BH", position, delay)
            self.hass.create_task(datapoint.set_value(bytes(new_value)))


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


mapping: dict[str, TuyaBLECategoryTextMapping] = {
    "szjqr": TuyaBLECategoryTextMapping(
        products={
            k: [
                TuyaBLETextMapping(
                    dp_id=121,
                    description=TextEntityDescription(
                        key="program",
                        icon="mdi:repeat",
                        pattern=(
                            r"^((\d{1,2}|100)(\/\d{1,2})?)"
                            r"(;((\d{1,2}|100)(\/\d{1,2})?))+$"
                        ),
                        entity_category=EntityCategory.CONFIG,
                    ),
                    is_available=is_fingerbot_in_program_mode,
                    getter=get_fingerbot_program,
                    setter=set_fingerbot_program,
                ),
            ]
            for k in [
                "blliqpsj",
                "ndvkgsrm",
                "yiihr7zh",
                "neq16kgd",
            ]
        },
    ),
}


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
