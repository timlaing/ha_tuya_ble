"""Lock platform for Tuya BLE smart locks."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.lock import (
    LockEntity,
    LockEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .device_registry import EntityDescriptor, get_registry
from .devices import (
    TuyaBLECoordinator,
    TuyaBLEData,
    TuyaBLEEntity,
    TuyaBLEProductInfo,
)
from .tuya_ble import TuyaBLEDataPointType, TuyaBLEDevice

TuyaBLELockGetter = Callable[["TuyaBLELock", TuyaBLEProductInfo], bool | None] | None


TuyaBLELockIsAvailable = Callable[["TuyaBLELock", TuyaBLEProductInfo], bool] | None


@dataclass
class TuyaBLELockMapping:
    """Mapping of a Tuya BLE data point to a Home Assistant lock entity."""

    dp_id: int
    description: LockEntityDescription
    force_add: bool = True
    dp_type: TuyaBLEDataPointType | None = None
    is_available: TuyaBLELockIsAvailable = None
    getter: TuyaBLELockGetter = None
    door_dp_id: int | None = None


@dataclass
class TuyaBLECategoryLockMapping:
    """Container for product-specific and default lock mappings."""

    products: dict[str, list[TuyaBLELockMapping]] | None = None
    mapping: list[TuyaBLELockMapping] | None = None


def _lock_description(desc: EntityDescriptor) -> LockEntityDescription:
    """Build a LockEntityDescription from a descriptor."""
    return LockEntityDescription(key=desc.translation_key or str(desc.dp_id))


def _build_lock_mapping(desc: EntityDescriptor) -> TuyaBLELockMapping:
    """Construct a lock mapping from a registry descriptor."""
    return TuyaBLELockMapping(
        dp_id=desc.dp_id,
        description=_lock_description(desc),
        force_add=desc.force_add,
        dp_type=(
            TuyaBLEDataPointType(desc.dp_type) if desc.dp_type is not None else None
        ),
        is_available=desc.resolved_handler("when"),
        getter=desc.resolved_handler("read"),
        door_dp_id=desc.door_dp_id,
    )


def _build_mapping() -> dict[str, TuyaBLECategoryLockMapping]:
    """Build the lock mappings dict from the device registry."""
    registry = get_registry()
    result: dict[str, TuyaBLECategoryLockMapping] = {}
    for device_entities in registry.products.values():
        descriptors = device_entities.get("lock")
        if not descriptors:
            continue
        category_mapping = result.setdefault(
            device_entities.category,
            TuyaBLECategoryLockMapping(products={}),
        )
        assert category_mapping.products is not None
        category_mapping.products[device_entities.product_id] = [
            _build_lock_mapping(desc) for desc in descriptors
        ]
    for category, platform_defaults in registry.category_defaults.items():
        lock_descriptors = platform_defaults.get("lock")
        if not lock_descriptors:
            continue
        result.setdefault(category, TuyaBLECategoryLockMapping(products={})).mapping = [
            _build_lock_mapping(desc) for desc in lock_descriptors
        ]
    return result


mapping: dict[str, TuyaBLECategoryLockMapping] = _build_mapping()


def get_mapping_by_device(device: TuyaBLEDevice) -> list[TuyaBLELockMapping]:
    """Get the lock mappings for a device."""
    category = mapping.get(device.category)
    if category is not None and category.products is not None:
        product_mapping = category.products.get(device.product_id)
        if product_mapping is not None:
            return product_mapping
        if category.mapping is not None:
            return category.mapping
        return []
    return []


class TuyaBLELock(TuyaBLEEntity, LockEntity):
    """Representation of a Tuya BLE Lock."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: TuyaBLECoordinator,
        device: TuyaBLEDevice,
        product: TuyaBLEProductInfo,
        lock_mapping: TuyaBLELockMapping,
    ) -> None:
        super().__init__(hass, coordinator, device, product, lock_mapping.description)
        self._mapping = lock_mapping

    @property
    def is_locked(self) -> bool | None:
        """Return true if the lock is locked."""
        if self._mapping.getter is not None:
            return self._mapping.getter(self, self._product)

        datapoint = self.device.datapoints[self._mapping.dp_id]
        if datapoint is not None:
            # Motor running = unlocking/locking, motor stopped = locked
            return not bool(datapoint.value)
        return None

    @property
    def is_open(self) -> bool | None:
        """Return true if the door is open."""
        if self._mapping.door_dp_id is not None:
            datapoint = self.device.datapoints[self._mapping.door_dp_id]
            if datapoint is not None:
                return datapoint.value == 1
        return None

    async def async_lock(self, **kwargs: object) -> None:
        """Lock the device."""
        datapoint = self.device.datapoints.get_or_create(
            self._mapping.dp_id,
            TuyaBLEDataPointType.DT_BOOL,
            False,
        )
        await datapoint.set_value(False)

    async def async_unlock(self, **kwargs: object) -> None:
        """Unlock the device."""
        datapoint = self.device.datapoints.get_or_create(
            self._mapping.dp_id,
            TuyaBLEDataPointType.DT_BOOL,
            True,
        )
        await datapoint.set_value(True)

    def lock(self, **kwargs: object) -> None:
        """Lock the device."""

    def open(self, **kwargs: object) -> None:
        """Open the device."""

    def unlock(self, **kwargs: object) -> None:
        """Unlock the device."""

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
    """Set up the Tuya BLE locks."""
    data: TuyaBLEData = hass.data[DOMAIN][entry.entry_id]
    mappings = get_mapping_by_device(data.device)
    entities: list[TuyaBLELock] = []
    for lock_mapping in mappings:
        if lock_mapping.force_add or data.device.datapoints.has_id(
            lock_mapping.dp_id, lock_mapping.dp_type
        ):
            entities.append(
                TuyaBLELock(
                    hass,
                    data.coordinator,
                    data.device,
                    data.product,
                    lock_mapping,
                )
            )
    async_add_entities(entities)
