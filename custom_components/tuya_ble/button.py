"""Button platform for Tuya BLE devices (fingerbot push triggers)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import cast

from homeassistant.components.button import (
    ButtonEntity,
    ButtonEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .devices import TuyaBLECoordinator, TuyaBLEData, TuyaBLEEntity, TuyaBLEProductInfo
from .fingerbot import is_fingerbot_in_push_mode
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
    is_available: TuyaBLEButtonIsAvailable = is_fingerbot_in_push_mode


@dataclass
class TuyaBLECategoryButtonMapping:
    """Container for product-specific and default button mappings."""

    products: dict[str, list[TuyaBLEButtonMapping]] | None = None
    mapping: list[TuyaBLEButtonMapping] | None = None


mapping: dict[str, TuyaBLECategoryButtonMapping] = {
    "szjqr": TuyaBLECategoryButtonMapping(
        products={
            k: [
                cast(TuyaBLEButtonMapping, TuyaBLEFingerbotModeMapping(dp_id=1)),
            ]
            for k in ["3yqdo5yt", "xhf790if"]
        }
        | {
            k: [
                cast(TuyaBLEButtonMapping, TuyaBLEFingerbotModeMapping(dp_id=2)),
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
                cast(TuyaBLEButtonMapping, TuyaBLEFingerbotModeMapping(dp_id=2)),
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
    "znhsb": TuyaBLECategoryButtonMapping(
        products={
            "cdlandip":  # Smart water bottle
            [
                TuyaBLEButtonMapping(
                    dp_id=109,
                    description=ButtonEntityDescription(
                        key="bright_lid_screen",
                    ),
                ),
            ],
        },
    ),
}


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
