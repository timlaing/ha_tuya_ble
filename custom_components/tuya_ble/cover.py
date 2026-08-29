"""Cover platform for Tuya BLE blinds, curtains, and motors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.cover import (
    ATTR_POSITION,
    ATTR_TILT_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityDescription,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
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


@dataclass
class TuyaBLECoverMapping:
    """Mapping of Tuya BLE data points to a Home Assistant cover entity."""

    description: CoverEntityDescription
    state_dp_id: int = 0
    position_set_dp_id: int = 0
    position_dp_id: int = 0
    tilt_dp_id: int = 0
    battery_dp_id: int = 0
    work_state_dp_id: int = 0
    speed_dp_id: int = 0


@dataclass
class TuyaBLECategoryCoverMapping:
    """Container for product-specific and default cover mappings."""

    products: dict[str, list[TuyaBLECoverMapping]] | None = None
    mapping: list[TuyaBLECoverMapping] | None = None


def _cover_description(desc: EntityDescriptor) -> CoverEntityDescription:
    """Build a CoverEntityDescription from a registry descriptor."""
    kwargs: dict[str, object] = {
        "key": desc.translation_key or "cover",
    }
    if desc.icon is not None:
        kwargs["icon"] = desc.icon
    return CoverEntityDescription(**kwargs)  # type: ignore[arg-type]


def _build_cover_mapping(desc: EntityDescriptor) -> TuyaBLECoverMapping:
    """Construct a cover mapping from a registry descriptor."""
    extra = desc.extra
    kwargs: dict[str, Any] = {"description": _cover_description(desc)}
    for field_name in (
        "state_dp_id",
        "position_set_dp_id",
        "position_dp_id",
        "tilt_dp_id",
        "battery_dp_id",
        "work_state_dp_id",
        "speed_dp_id",
    ):
        if field_name in extra:
            kwargs[field_name] = extra[field_name]
    return TuyaBLECoverMapping(**kwargs)


def _build_mapping() -> dict[str, TuyaBLECategoryCoverMapping]:
    """Build the cover mappings dict from the device registry."""
    registry = get_registry()
    result: dict[str, TuyaBLECategoryCoverMapping] = {}
    for device_entities in registry.products.values():
        descriptors = device_entities.get("cover")
        if not descriptors:
            continue
        category_mapping = result.setdefault(
            device_entities.category,
            TuyaBLECategoryCoverMapping(products={}),
        )
        assert category_mapping.products is not None
        category_mapping.products[device_entities.product_id] = [
            _build_cover_mapping(desc) for desc in descriptors
        ]
    for category, defaults in registry.category_defaults.items():
        default_descriptors = defaults.get("cover")
        if not default_descriptors:
            continue
        category_mapping = result.setdefault(
            category, TuyaBLECategoryCoverMapping(products={})
        )
        category_mapping.mapping = [
            _build_cover_mapping(desc) for desc in default_descriptors
        ]
    return result


mapping: dict[str, TuyaBLECategoryCoverMapping] = _build_mapping()


def get_mapping_by_device(device: TuyaBLEDevice) -> list[TuyaBLECoverMapping]:
    """Get the cover mappings for a device."""
    category = mapping.get(device.category)
    if category is not None and category.products is not None:
        product_mapping = category.products.get(device.product_id)
        if product_mapping is not None:
            return product_mapping
        if category.mapping is not None:
            return category.mapping
        return []
    return []


class TuyaBLECover(TuyaBLEEntity, CoverEntity):
    """Representation of a Tuya BLE Cover."""

    _attr_is_closed = False
    _attr_current_cover_position = 0
    _attr_current_cover_tilt_position = 0
    _attr_device_class = CoverDeviceClass.SHADE

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: TuyaBLECoordinator,
        device: TuyaBLEDevice,
        product: TuyaBLEProductInfo,
        cover_mapping: TuyaBLECoverMapping,
    ) -> None:
        super().__init__(hass, coordinator, device, product, cover_mapping.description)
        self._mapping = cover_mapping

    @property
    def supported_features(self) -> CoverEntityFeature:
        """Return the supported features of the device."""
        result = (
            CoverEntityFeature.CLOSE
            | CoverEntityFeature.OPEN
            | CoverEntityFeature.SET_POSITION
            | CoverEntityFeature.STOP
        )
        if self._mapping.tilt_dp_id != 0:
            result |= CoverEntityFeature.SET_TILT_POSITION
        return result

    @callback
    def _handle_coordinator_update(self) -> None:
        """Read state, position, and tilt datapoints then write HA state."""
        self._update_state_from_datapoints()
        self.async_write_ha_state()

    @staticmethod
    def _state_from_value(value: int) -> tuple[bool, bool]:
        """Map a state datapoint value to (is_opening, is_closing)."""
        if value == 0:
            return (True, False)
        if value == 2:
            return (False, True)
        return (False, False)

    def _update_state_from_datapoints(self) -> None:
        """Read current datapoint values and update entity attributes."""
        if self._mapping.state_dp_id != 0:
            datapoint = self.device.datapoints[self._mapping.state_dp_id]
            if datapoint is not None:
                self._attr_is_opening, self._attr_is_closing = self._state_from_value(
                    int(datapoint.value)
                )

        if self._mapping.position_dp_id != 0:
            self._update_position_from_datapoint()

        if self._mapping.tilt_dp_id != 0:
            datapoint = self.device.datapoints[self._mapping.tilt_dp_id]
            if datapoint is not None:
                self._attr_current_cover_tilt_position = int(
                    (int(datapoint.value) - 1) / 9 * 100
                )

    def _update_position_from_datapoint(self) -> None:
        """Update position and derived state from the position datapoint."""
        datapoint = self.device.datapoints[self._mapping.position_dp_id]
        if datapoint is None:
            return
        self._attr_current_cover_position = 100 - int(datapoint.value)
        self._attr_is_closed = self._attr_current_cover_position == 0
        if self._attr_is_closed:
            self._attr_is_closing = False
        if self._attr_current_cover_position == 100:
            self._attr_is_opening = False

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the cover."""
        await self._update_cover_state(0)

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the cover."""
        await self._update_cover_state(2)

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the cover."""
        await self._update_cover_state(1)

    def open_cover(self, **kwargs: Any) -> None:
        """Open the cover."""

    def close_cover(self, **kwargs: Any) -> None:
        """Close the cover."""

    def stop_cover(self, **kwargs: Any) -> None:
        """Stop the cover."""

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Set cover position."""
        position = 100 - kwargs[ATTR_POSITION]
        if self._mapping.position_set_dp_id != 0:
            datapoint = self.device.datapoints.get_or_create(
                self._mapping.position_set_dp_id,
                TuyaBLEDataPointType.DT_VALUE,
                position,
            )
            await datapoint.set_value(position)

    async def async_open_cover_tilt(self, **kwargs: Any) -> None:
        """Open the cover tilt."""
        if self._mapping.tilt_dp_id != 0:
            datapoint = self.device.datapoints.get_or_create(
                self._mapping.tilt_dp_id,
                TuyaBLEDataPointType.DT_VALUE,
                10,
            )
            await datapoint.set_value(10)

    async def async_close_cover_tilt(self, **kwargs: Any) -> None:
        """Close the cover tilt."""
        if self._mapping.tilt_dp_id != 0:
            datapoint = self.device.datapoints.get_or_create(
                self._mapping.tilt_dp_id,
                TuyaBLEDataPointType.DT_VALUE,
                1,
            )
            await datapoint.set_value(1)

    async def async_set_cover_tilt_position(self, **kwargs: Any) -> None:
        """Set cover tilt position."""
        tilt_position = kwargs[ATTR_TILT_POSITION]
        new_tilt_position = round(tilt_position / 100 * 9 + 1)
        if self._mapping.tilt_dp_id != 0:
            datapoint = self.device.datapoints.get_or_create(
                self._mapping.tilt_dp_id,
                TuyaBLEDataPointType.DT_VALUE,
                new_tilt_position,
            )
            await datapoint.set_value(new_tilt_position)

    async def _update_cover_state(self, state: int) -> None:
        """Send a state command (open/close/stop) to the device."""
        if self._mapping.state_dp_id != 0:
            datapoint = self.device.datapoints.get_or_create(
                self._mapping.state_dp_id,
                TuyaBLEDataPointType.DT_VALUE,
                state,
            )
            self.hass.create_task(datapoint.set_value(state))
            self._update_ha_state_for_cover_state(state)

    def _update_ha_state_for_cover_state(self, state: int) -> None:
        """Force-update HA state after sending a command."""
        self._apply_cover_state(state)
        self.async_write_ha_state()

    def _apply_cover_state(self, state: int) -> None:
        """Apply cover state to entity attributes without writing HA state."""
        self._attr_is_closed = False
        self._attr_is_closing = False
        self._attr_is_opening = False

        if self._attr_current_cover_position == 0:
            self._attr_is_closed = True

        if state == 0 and self._attr_current_cover_position != 100:
            self._attr_is_opening = True
        elif state == 2 and self._attr_current_cover_position != 0:
            self._attr_is_closing = True


async def async_setup_entry(  # noqa: S7503
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Tuya BLE covers."""
    data: TuyaBLEData = hass.data[DOMAIN][entry.entry_id]
    mappings = get_mapping_by_device(data.device)
    entities: list[TuyaBLECover] = []
    for cover_mapping in mappings:
        entities.append(
            TuyaBLECover(
                hass,
                data.coordinator,
                data.device,
                data.product,
                cover_mapping,
            )
        )
    async_add_entities(entities)
