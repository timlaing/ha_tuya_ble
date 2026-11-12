"""Switch platform for Tuya BLE devices (fingerbots, water valves, locks, TRVs)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import logging
from typing import Any

from homeassistant.components.switch import (
    SwitchEntity,
    SwitchEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later

from .const import DOMAIN
from .device_registry import EntityDescriptor, get_registry
from .devices import TuyaBLECoordinator, TuyaBLEData, TuyaBLEEntity, TuyaBLEProductInfo
from .tuya_ble import TuyaBLEDataPoint, TuyaBLEDataPointType, TuyaBLEDevice

_LOGGER = logging.getLogger(__name__)

TuyaBLESwitchGetter = (
    Callable[["TuyaBLESwitch", TuyaBLEProductInfo], bool | None] | None
)


TuyaBLESwitchIsAvailable = Callable[["TuyaBLESwitch", TuyaBLEProductInfo], bool] | None


TuyaBLESwitchSetter = Callable[["TuyaBLESwitch", TuyaBLEProductInfo, bool], None] | None
TuyaBLESwitchPulse = Callable[[TuyaBLEEntity, int], float | None]


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
    pulse_duration: TuyaBLESwitchPulse | None = None


@dataclass
class TuyaBLECategorySwitchMapping:
    """Mapping of product IDs to switch mappings within a device category."""

    products: dict[str, list[TuyaBLESwitchMapping]] | None = None
    mapping: list[TuyaBLESwitchMapping] | None = None


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
    if (pulse_duration := desc.resolved_handler("pulse")) is not None:
        kwargs["pulse_duration"] = pulse_duration
    if desc.dp_type is not None:
        kwargs["dp_type"] = TuyaBLEDataPointType(desc.dp_type)
    return TuyaBLESwitchMapping(**kwargs)


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
        bitmap_value = self._zip_bitmap(bytes(datapoint.value), bitmap_mask)
        return any((v & m) != 0 for v, m in bitmap_value)

    def _zip_bitmap(
        self, bitmap_value: bytes, bitmap_mask: bytes
    ) -> list[tuple[int, int]]:
        """Pair the payload bytes with the mask bytes, warning on a length mismatch.

        A device can report a different bitmap width than the descriptor mask,
        notably when two switches share one datapoint with one-byte masks. Pairing
        the shorter of the two keeps the entity readable and writable instead of
        raising from an is_on property or a turn_on/turn_off call.
        """
        if len(bitmap_value) != len(bitmap_mask):
            _LOGGER.warning(
                "%s: Bitmap length mismatch for %s, payload %d byte(s) vs mask "
                "%d byte(s); pairing %d byte(s)",
                self.device.address,
                self.entity_description.key,
                len(bitmap_value),
                len(bitmap_mask),
                min(len(bitmap_value), len(bitmap_mask)),
            )
        return list(zip(bitmap_value, bitmap_mask, strict=False))

    def _read_bitmap(self, datapoint: TuyaBLEDataPoint) -> bytes:
        """Return the current bitmap value from a datapoint."""
        return (
            bytes(datapoint.value)
            if isinstance(datapoint.value, (bytes, bytearray))
            else b""
        )

    def _write_bitmap(self, transform: Callable[[int, int], int]) -> None:
        """Write the bitmap datapoint after applying a transform to each byte.

        Bytes past the mask width are carried over untouched: a datapoint wider than
        this entity's mask also carries the bits of any sibling sharing it, and
        writing only the masked prefix would reset them on the device.
        """
        bitmap_mask = self._mapping.bitmap_mask
        if bitmap_mask is None:
            return
        datapoint = self.device.datapoints.get_or_create(
            self._mapping.dp_id,
            TuyaBLEDataPointType.DT_BITMAP,
            bitmap_mask,
        )
        current = self._read_bitmap(datapoint)
        bitmap_value = self._zip_bitmap(current, bitmap_mask)
        new_value = (
            bytes(transform(v, m) for (v, m) in bitmap_value)
            + current[len(bitmap_mask) :]
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


class TuyaBLEPulseSwitch(TuyaBLESwitch):
    """Control a channel with either a persistent switch or a timed press."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: TuyaBLECoordinator,
        device: TuyaBLEDevice,
        product: TuyaBLEProductInfo,
        switch_mapping: TuyaBLESwitchMapping,
    ) -> None:
        super().__init__(hass, coordinator, device, product, switch_mapping)
        self._pulse_pending = False
        self._cancel_reset: Callable[[], None] | None = None

    def _pulse_duration(self) -> float | None:
        pulse_duration = self._mapping.pulse_duration
        assert pulse_duration is not None
        return pulse_duration(self, self._mapping.dp_id)

    @property
    def is_on(self) -> bool:
        """Read the reported switch state or the active press timer."""
        if self._pulse_duration() is not None:
            return self._cancel_reset is not None
        return super().is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Set the channel on or send one press and start its display timer."""
        if not self.available:
            return
        duration = self._pulse_duration()
        if duration is not None and (self._pulse_pending or self.is_on):
            return
        datapoint = self.device.datapoints.get_or_create(
            self._mapping.dp_id, TuyaBLEDataPointType.DT_BOOL, False
        )
        if duration is None:
            await datapoint.set_value(True)
            return

        self._pulse_pending = True
        try:
            # The device treats a change in either direction as a press.
            await datapoint.set_value(not bool(datapoint.value))
            if (
                self._pulse_pending
                and self.available
                and self._pulse_duration() is not None
            ):
                self._cancel_reset = async_call_later(
                    self.hass, duration, self._reset_pulse
                )
                self.async_write_ha_state()
        finally:
            self._pulse_pending = False

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off a persistent switch. A press runs for its set duration."""
        if self.available and self._pulse_duration() is None:
            datapoint = self.device.datapoints.get_or_create(
                self._mapping.dp_id, TuyaBLEDataPointType.DT_BOOL, False
            )
            await datapoint.set_value(False)

    @callback
    def _reset_pulse(self, _now: datetime) -> None:
        self._cancel_reset = None
        self.async_write_ha_state()

    @callback
    def _clear_pulse(self) -> None:
        if self._cancel_reset is not None:
            self._cancel_reset()
            self._cancel_reset = None

    @callback
    def _handle_coordinator_update(self) -> None:
        if not self.available or self._pulse_duration() is None:
            self._clear_pulse()
        super()._handle_coordinator_update()

    async def async_will_remove_from_hass(self) -> None:
        """Cancel the local reset when the entity is unloaded."""
        self._pulse_pending = False
        self._clear_pulse()
        await super().async_will_remove_from_hass()


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
            entity_class = (
                TuyaBLEPulseSwitch if switch_mapping.pulse_duration else TuyaBLESwitch
            )
            entities.append(
                entity_class(
                    hass,
                    data.coordinator,
                    data.device,
                    data.product,
                    switch_mapping,
                )
            )
    async_add_entities(entities)
