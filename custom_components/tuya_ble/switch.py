"""Switch platform for Tuya BLE devices (fingerbots, water valves, locks, TRVs)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from homeassistant.components.switch import (
    SwitchEntity,
    SwitchEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .device_descriptors.handlers.fingerbot.mode import in_switch_mode
from .device_descriptors.handlers.water_valve import is_water_valve_in_switch_mode
from .device_registry import EntityDescriptor, get_registry
from .devices import TuyaBLECoordinator, TuyaBLEData, TuyaBLEEntity, TuyaBLEProductInfo
from .tuya_ble import TuyaBLEDataPoint, TuyaBLEDataPointType, TuyaBLEDevice

TuyaBLESwitchGetter = (
    Callable[["TuyaBLESwitch", TuyaBLEProductInfo], bool | None] | None
)


TuyaBLESwitchIsAvailable = Callable[["TuyaBLESwitch", TuyaBLEProductInfo], bool] | None


TuyaBLESwitchSetter = Callable[["TuyaBLESwitch", TuyaBLEProductInfo, bool], None] | None


@dataclass
class TuyaBLESwitchMapping:
    """Mapping of a Tuya data point to a switch entity."""

    dp_id: int
    description: SwitchEntityDescription
    force_add: bool = True
    dp_type: TuyaBLEDataPointType | None = None
    bitmap_mask: bytes | None = None
    is_available: TuyaBLESwitchIsAvailable = None
    getter: TuyaBLESwitchGetter = None
    setter: TuyaBLESwitchSetter = None


@dataclass
class TuyaBLEFingerbotSwitchMapping(TuyaBLESwitchMapping):
    """Switch mapping for fingerbot devices."""

    description: SwitchEntityDescription = field(
        default_factory=lambda: SwitchEntityDescription(
            key="switch",
        )
    )
    is_available: TuyaBLESwitchIsAvailable = in_switch_mode


@dataclass
class TuyaBLEReversePositionsMapping(TuyaBLESwitchMapping):
    """Switch mapping for fingerbot reverse positions."""

    description: SwitchEntityDescription = field(
        default_factory=lambda: SwitchEntityDescription(
            key="reverse_positions",
            icon="mdi:arrow-up-down-bold",
            entity_category=EntityCategory.CONFIG,
        )
    )
    is_available: TuyaBLESwitchIsAvailable = in_switch_mode


@dataclass
class TuyaBLEWaterValveSwitchMapping(TuyaBLESwitchMapping):
    """Switch mapping for water valve devices."""

    description: SwitchEntityDescription = field(
        default_factory=lambda: SwitchEntityDescription(
            key="water_valve",
        )
    )
    is_available: TuyaBLESwitchIsAvailable = is_water_valve_in_switch_mode


@dataclass
class TuyaLockMotorStateMapping(TuyaBLESwitchMapping):
    """Switch mapping for lock motor state."""

    description: SwitchEntityDescription = field(
        default_factory=lambda: SwitchEntityDescription(
            key="lock_motor_state",
        )
    )


@dataclass
class TuyaBLEWaterValveWeatherSwitchMapping(TuyaBLESwitchMapping):
    """Switch mapping for water valve weather switch."""

    description: SwitchEntityDescription = field(
        default_factory=lambda: SwitchEntityDescription(
            key="weather_switch",
            icon="mdi:cloud-question",
            name="Weather Switch",
        )
    )


@dataclass
class TuyaBLECategorySwitchMapping:
    """Mapping of product IDs to switch mappings within a device category."""

    products: dict[str, list[TuyaBLESwitchMapping]] | None = None
    mapping: list[TuyaBLESwitchMapping] | None = None


_KIND_CLASSES: dict[str, type[TuyaBLESwitchMapping]] = {
    "TuyaBLEFingerbotSwitchMapping": TuyaBLEFingerbotSwitchMapping,
    "TuyaBLEReversePositionsMapping": TuyaBLEReversePositionsMapping,
    "TuyaLockMotorStateMapping": TuyaLockMotorStateMapping,
    "TuyaBLEWaterValveSwitchMapping": TuyaBLEWaterValveSwitchMapping,
    "TuyaBLEWaterValveWeatherSwitchMapping": TuyaBLEWaterValveWeatherSwitchMapping,
}


def _switch_description(desc: EntityDescriptor) -> SwitchEntityDescription:
    """Build a SwitchEntityDescription from a registry descriptor."""
    return SwitchEntityDescription(
        key=desc.translation_key or str(desc.dp_id),
        name=desc.name,
        icon=desc.icon,
        entity_category=(
            EntityCategory(desc.entity_category)
            if desc.entity_category is not None
            else None
        ),
        entity_registry_enabled_default=desc.enabled_by_default is not False,
    )


def _build_switch_mapping(desc: EntityDescriptor) -> TuyaBLESwitchMapping:
    """Construct a switch mapping from a registry descriptor."""
    kind = desc.kind
    cls = (
        _KIND_CLASSES.get(kind, TuyaBLESwitchMapping)
        if kind is not None
        else TuyaBLESwitchMapping
    )
    kwargs: dict[str, Any] = {
        "dp_id": desc.dp_id,
        "description": _switch_description(desc),
        "force_add": desc.force_add,
    }
    if (bitmap_mask := desc.extra.get("bitmap_mask")) is not None:
        kwargs["bitmap_mask"] = bitmap_mask
    if (is_available := desc.resolved_handler("when")) is not None:
        kwargs["is_available"] = is_available
    if (getter := desc.resolved_handler("read")) is not None:
        kwargs["getter"] = getter
    if (setter := desc.resolved_handler("write")) is not None:
        kwargs["setter"] = setter
    if desc.dp_type is not None:
        kwargs["dp_type"] = TuyaBLEDataPointType(desc.dp_type)
    return cls(**kwargs)


def _build_mapping() -> dict[str, TuyaBLECategorySwitchMapping]:
    """Build the switch mappings dict from the device registry."""
    result: dict[str, TuyaBLECategorySwitchMapping] = {}
    for device_entities in get_registry().products.values():
        descriptors = device_entities.get("switch")
        if not descriptors:
            continue
        category_mapping = result.setdefault(
            device_entities.category,
            TuyaBLECategorySwitchMapping(products={}),
        )
        assert category_mapping.products is not None
        category_mapping.products[device_entities.product_id] = [
            _build_switch_mapping(desc) for desc in descriptors
        ]
    return result


mapping: dict[str, TuyaBLECategorySwitchMapping] = _build_mapping()


def get_mapping_by_device(device: TuyaBLEDevice) -> list[TuyaBLESwitchMapping]:
    """Return the switch mappings for a given device."""
    category = mapping.get(device.category)
    if category is not None and category.products is not None:
        product_mapping = category.products.get(device.product_id)
        if product_mapping is not None:
            return product_mapping
        if category.mapping is not None:
            return category.mapping
        return []
    return []


class TuyaBLESwitch(TuyaBLEEntity, SwitchEntity):
    """Representation of a Tuya BLE Switch."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: TuyaBLECoordinator,
        device: TuyaBLEDevice,
        product: TuyaBLEProductInfo,
        switch_mapping: TuyaBLESwitchMapping,
    ) -> None:
        super().__init__(hass, coordinator, device, product, switch_mapping.description)
        self._mapping = switch_mapping

    @property
    def is_on(self) -> bool:
        """Return true if switch is on."""

        if self._mapping.getter is not None:
            return self._mapping.getter(self, self._product) or False

        datapoint = self.device.datapoints[self._mapping.dp_id]
        if not datapoint:
            return False
        if (
            datapoint.dp_type
            in [TuyaBLEDataPointType.DT_RAW, TuyaBLEDataPointType.DT_BITMAP]
            and self._mapping.bitmap_mask
        ):
            return self._read_bitmap_on(datapoint)
        return bool(datapoint.value)

    def _read_bitmap_on(self, datapoint: TuyaBLEDataPoint) -> bool:
        """Return True if any masked bit is set in a bitmap datapoint."""
        bitmap_mask = self._mapping.bitmap_mask
        if not isinstance(datapoint.value, (bytes, bytearray)) or bitmap_mask is None:
            return False
        bitmap_value = bytes(datapoint.value)
        return any((v & m) != 0 for v, m in zip(bitmap_value, bitmap_mask, strict=True))

    def _read_bitmap(self, datapoint: TuyaBLEDataPoint) -> bytes:
        """Return the current bitmap value from a datapoint."""
        return (
            bytes(datapoint.value)
            if isinstance(datapoint.value, (bytes, bytearray))
            else b""
        )

    def _write_bitmap(self, transform: Callable[[int, int], int]) -> None:
        """Write the bitmap datapoint after applying a transform to each byte."""
        bitmap_mask = self._mapping.bitmap_mask
        if bitmap_mask is None:
            return
        datapoint = self.device.datapoints.get_or_create(
            self._mapping.dp_id,
            TuyaBLEDataPointType.DT_BITMAP,
            bitmap_mask,
        )
        bitmap_value = self._read_bitmap(datapoint)
        new_value = bytes(
            transform(v, m) for (v, m) in zip(bitmap_value, bitmap_mask, strict=True)
        )
        self.hass.create_task(datapoint.set_value(new_value))

    def turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        if self._mapping.setter:
            self._mapping.setter(self, self._product, True)
            return

        if self._mapping.bitmap_mask:
            self._write_bitmap(lambda v, m: v | m)
            return
        datapoint = self.device.datapoints.get_or_create(
            self._mapping.dp_id,
            TuyaBLEDataPointType.DT_BOOL,
            True,
        )
        self.hass.create_task(datapoint.set_value(True))

    def turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        if self._mapping.setter:
            self._mapping.setter(self, self._product, False)
            return

        if self._mapping.bitmap_mask:
            self._write_bitmap(lambda v, m: v & ~m)
            return
        datapoint = self.device.datapoints.get_or_create(
            self._mapping.dp_id,
            TuyaBLEDataPointType.DT_BOOL,
            False,
        )
        self.hass.create_task(datapoint.set_value(False))

    @property
    def available(self) -> bool:
        """True when coordinator is connected and the availability predicate passes."""
        result = super().available
        if result and self._mapping.is_available:
            result = self._mapping.is_available(self, self._product)
        return result


async def async_setup_entry(  # noqa: S7503
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tuya BLE switch entities for the config entry."""
    data: TuyaBLEData = hass.data[DOMAIN][entry.entry_id]
    mappings = get_mapping_by_device(data.device)
    entities: list[TuyaBLESwitch] = []
    for switch_mapping in mappings:
        if switch_mapping.force_add or data.device.datapoints.has_id(
            switch_mapping.dp_id, switch_mapping.dp_type
        ):
            entities.append(
                TuyaBLESwitch(
                    hass,
                    data.coordinator,
                    data.device,
                    data.product,
                    switch_mapping,
                )
            )
    async_add_entities(entities)
