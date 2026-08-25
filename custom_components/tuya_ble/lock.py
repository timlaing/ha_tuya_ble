"""The Tuya BLE integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.lock import (
    LockEntity,
    LockEntityFeature,
    LockEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .devices import TuyaBLEData, TuyaBLEEntity, TuyaBLEProductInfo
from .tuya_ble import TuyaBLEDataPointType, TuyaBLEDevice

_LOGGER = __import__("logging").getLogger(__name__)


@dataclass
class TuyaBLELockMapping:
    """Model a DP, description and default values"""

    description: LockEntityDescription

    lock_dp_id: int = 0
    lock_motor_state_dp_id: int = 0
    manual_lock_dp_id: int = 0


@dataclass
class TuyaBLECategoryLockMapping:
    """Models a dict of products and their mappings"""

    products: dict[str, list[TuyaBLELockMapping]] | None = None
    mapping: list[TuyaBLELockMapping] | None = None


mapping: dict[str, TuyaBLECategoryLockMapping] = {
    "ms": TuyaBLECategoryLockMapping(
        products={
            "gumrixyt": [
                TuyaBLELockMapping(
                    description=LockEntityDescription(key="lock"),
                    lock_dp_id=1,
                    lock_motor_state_dp_id=1,
                    manual_lock_dp_id=2,
                )
            ],
            "uamrw6h3": [
                TuyaBLELockMapping(
                    description=LockEntityDescription(key="lock"),
                    lock_dp_id=1,
                    lock_motor_state_dp_id=1,
                    manual_lock_dp_id=2,
                )
            ],
            "sidhzylo": [
                TuyaBLELockMapping(
                    description=LockEntityDescription(key="lock"),
                    lock_dp_id=1,
                    lock_motor_state_dp_id=1,
                    manual_lock_dp_id=2,
                )
            ],
            "mqc2hevy": [
                TuyaBLELockMapping(
                    description=LockEntityDescription(key="lock"),
                    lock_dp_id=1,
                    lock_motor_state_dp_id=1,
                    manual_lock_dp_id=2,
                )
            ],
            "a6nttc41": [
                TuyaBLELockMapping(
                    description=LockEntityDescription(key="lock"),
                    lock_dp_id=1,
                    lock_motor_state_dp_id=1,
                    manual_lock_dp_id=2,
                )
            ],
            "okkyfgfs": [
                TuyaBLELockMapping(
                    description=LockEntityDescription(key="lock"),
                    lock_dp_id=1,
                    lock_motor_state_dp_id=1,
                    manual_lock_dp_id=2,
                )
            ],
            "k53ok3u9": [
                TuyaBLELockMapping(
                    description=LockEntityDescription(key="lock"),
                    lock_dp_id=1,
                    lock_motor_state_dp_id=1,
                    manual_lock_dp_id=2,
                )
            ],
            "bvclwu9b": [
                TuyaBLELockMapping(
                    description=LockEntityDescription(key="lock"),
                    lock_dp_id=1,
                    lock_motor_state_dp_id=1,
                    manual_lock_dp_id=2,
                )
            ],
            "isk2p555": [
                TuyaBLELockMapping(
                    description=LockEntityDescription(key="lock"),
                    lock_dp_id=1,
                    lock_motor_state_dp_id=1,
                    manual_lock_dp_id=2,
                )
            ],
            "stugc8dl": [
                TuyaBLELockMapping(
                    description=LockEntityDescription(key="lock"),
                    lock_dp_id=1,
                    lock_motor_state_dp_id=1,
                    manual_lock_dp_id=2,
                )
            ],
            "xicdxood": [
                TuyaBLELockMapping(
                    description=LockEntityDescription(key="lock"),
                    lock_dp_id=1,
                    lock_motor_state_dp_id=1,
                    manual_lock_dp_id=2,
                )
            ],
            "rlyxv7pe": [
                TuyaBLELockMapping(
                    description=LockEntityDescription(key="lock"),
                    lock_dp_id=1,
                    lock_motor_state_dp_id=1,
                    manual_lock_dp_id=2,
                )
            ],
            "oyqux5vv": [
                TuyaBLELockMapping(
                    description=LockEntityDescription(key="lock"),
                    lock_dp_id=1,
                    lock_motor_state_dp_id=1,
                    manual_lock_dp_id=2,
                )
            ],
            "ebd5e0uauqx0vfsp": [
                TuyaBLELockMapping(
                    description=LockEntityDescription(key="lock"),
                    lock_dp_id=1,
                    lock_motor_state_dp_id=1,
                    manual_lock_dp_id=2,
                )
            ],
            "ajk32biq": [
                TuyaBLELockMapping(
                    description=LockEntityDescription(key="lock"),
                    lock_dp_id=1,
                    lock_motor_state_dp_id=1,
                    manual_lock_dp_id=2,
                )
            ],
            "z7lj676i": [
                TuyaBLELockMapping(
                    description=LockEntityDescription(key="lock"),
                    lock_dp_id=1,
                    lock_motor_state_dp_id=1,
                    manual_lock_dp_id=2,
                )
            ],
            "hs21i377": [
                TuyaBLELockMapping(
                    description=LockEntityDescription(key="lock"),
                    lock_dp_id=1,
                    lock_motor_state_dp_id=1,
                    manual_lock_dp_id=2,
                )
            ],
        },
    ),
}


def get_mapping_by_device(device: TuyaBLEDevice) -> list[TuyaBLECategoryLockMapping]:
    """For a given device, work out the category lock mapping"""
    category = mapping.get(device.category)
    if category is not None and category.products is not None:
        product_mapping = category.products.get(device.product_id)
        if product_mapping is not None:
            return product_mapping
        if category.mapping is not None:
            return category.mapping

    return []


class TuyaBLELock(TuyaBLEEntity, LockEntity):
    """Representation of a Tuya BLE Lock."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: DataUpdateCoordinator,
        device: TuyaBLEDevice,
        product: TuyaBLEProductInfo,
        mapping: TuyaBLELockMapping,
    ) -> None:
        super().__init__(hass, coordinator, device, product, mapping.description)
        self._mapping = mapping
        self._attr_supported_features = LockEntityFeature.OPEN

    @property
    def is_locked(self) -> bool | None:
        """Return true if lock is locked."""
        if motor_state := self._device.datapoints.get_or_create(
            self._mapping.lock_motor_state_dp_id,
            TuyaBLEDataPointType.DT_BOOL,
            False,
        ):
            return not motor_state.value
        return None

    async def async_lock(self, **kwargs: Any) -> None:
        """Lock the lock."""
        if manual_lock := self._device.datapoints.get_or_create(
            self._mapping.manual_lock_dp_id,
            TuyaBLEDataPointType.DT_BOOL,
            True,
        ):
            await manual_lock.set_value(True)

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock the lock."""
        if manual_lock := self._device.datapoints.get_or_create(
            self._mapping.manual_lock_dp_id,
            TuyaBLEDataPointType.DT_BOOL,
            False,
        ):
            await manual_lock.set_value(False)

    async def async_open(self, **kwargs: Any) -> None:
        """Open the covering."""
        if manual_lock := self._device.datapoints.get_or_create(
            self._mapping.manual_lock_dp_id,
            TuyaBLEDataPointType.DT_BOOL,
            False,
        ):
            await manual_lock.set_value(False)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Tuya BLE sensors."""
    data: TuyaBLEData = hass.data[DOMAIN][entry.entry_id]
    mappings = get_mapping_by_device(data.device)
    entities: list[TuyaBLELock] = []
    for mapping in mappings:
        entities.append(
            TuyaBLELock(
                hass,
                data.coordinator,
                data.device,
                data.product,
                mapping,
            )
        )
    async_add_entities(entities)
