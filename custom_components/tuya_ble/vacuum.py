"""Vacuum platform for Tuya BLE cleaning robots.

Experimental: built for the CHYW200.ABIR window cleaning robot, which a
reporter tested in the ha-tuya-ble fork. Only the actions a descriptor declares
a data point for are offered -- start and stop always, plus fan speed when the
descriptor names a mode data point.

Based on the vacuum platform contributed to ha-tuya-ble/ha_tuya_ble by
@buliaksk and @CloCkWeRX (MIT licence).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any

from homeassistant.components.vacuum import (
    StateVacuumEntity,
    StateVacuumEntityDescription,
)
from homeassistant.components.vacuum.const import VacuumActivity, VacuumEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
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

_LOGGER = logging.getLogger(__name__)


@dataclass
class TuyaBLEVacuumMapping:
    """Mapping of Tuya BLE data points to a Home Assistant vacuum entity.

    ``dp_id`` is the bool data point that starts (true) and stops (false) the
    robot. ``status_activities`` maps each status value, in the status data
    point's enum order, to an activity; ``fan_speeds`` lists the modes of
    ``mode_dp_id`` in enum order.
    """

    dp_id: int
    description: StateVacuumEntityDescription
    force_add: bool = True
    dp_type: TuyaBLEDataPointType | None = None
    status_dp_id: int | None = None
    status_activities: dict[str, VacuumActivity] = field(default_factory=dict)
    mode_dp_id: int | None = None
    fan_speeds: list[str] = field(default_factory=list)


@dataclass
class TuyaBLECategoryVacuumMapping:
    """Container for product-specific and default vacuum mappings."""

    products: dict[str, list[TuyaBLEVacuumMapping]] | None = None
    mapping: list[TuyaBLEVacuumMapping] | None = None


def _vacuum_description(desc: EntityDescriptor) -> StateVacuumEntityDescription:
    """Build a StateVacuumEntityDescription from a registry descriptor."""
    return StateVacuumEntityDescription(
        key=desc.translation_key or str(desc.dp_id),
        name=desc.name,
        icon=desc.icon,
        entity_registry_enabled_default=desc.enabled_by_default is not False,
    )


def _build_vacuum_mapping(desc: EntityDescriptor) -> TuyaBLEVacuumMapping:
    """Construct a vacuum mapping from a registry descriptor."""
    extra = desc.extra
    return TuyaBLEVacuumMapping(
        dp_id=desc.dp_id,
        description=_vacuum_description(desc),
        force_add=desc.force_add,
        dp_type=(
            TuyaBLEDataPointType(desc.dp_type) if desc.dp_type is not None else None
        ),
        status_dp_id=extra.get("status_dp_id"),
        status_activities={
            str(value): VacuumActivity(activity)
            for value, activity in extra.get("status_activities", {}).items()
        },
        mode_dp_id=extra.get("mode_dp_id"),
        fan_speeds=list(extra.get("fan_speeds", [])),
    )


def _build_mapping() -> dict[str, TuyaBLECategoryVacuumMapping]:
    """Build the vacuum mappings dict from the device registry."""
    result: dict[str, TuyaBLECategoryVacuumMapping] = {}
    for device_entities in get_registry().products.values():
        descriptors = device_entities.get("vacuum")
        if not descriptors:
            continue
        category_mapping = result.setdefault(
            device_entities.category,
            TuyaBLECategoryVacuumMapping(products={}),
        )
        assert category_mapping.products is not None
        category_mapping.products[device_entities.product_id] = [
            _build_vacuum_mapping(desc) for desc in descriptors
        ]
    return result


mapping: dict[str, TuyaBLECategoryVacuumMapping] = _build_mapping()


def get_mapping_by_device(device: TuyaBLEDevice) -> list[TuyaBLEVacuumMapping]:
    """Get the vacuum mappings for a device."""
    category = mapping.get(device.category)
    if category is not None and category.products is not None:
        product_mapping = category.products.get(device.product_id)
        if product_mapping is not None:
            return product_mapping
        if category.mapping is not None:
            return category.mapping
        return []
    return []


# Pause, return-to-base, locate, spot and segment cleaning and send_command are
# never advertised in supported_features, so Home Assistant never calls them.
class TuyaBLEVacuum(TuyaBLEEntity, StateVacuumEntity):  # pylint: disable=abstract-method
    """Representation of a Tuya BLE cleaning robot."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: TuyaBLECoordinator,
        device: TuyaBLEDevice,
        product: TuyaBLEProductInfo,
        vacuum_mapping: TuyaBLEVacuumMapping,
    ) -> None:
        super().__init__(hass, coordinator, device, product, vacuum_mapping.description)
        self._mapping = vacuum_mapping
        features = (
            VacuumEntityFeature.STATE
            | VacuumEntityFeature.START
            | VacuumEntityFeature.STOP
        )
        if vacuum_mapping.mode_dp_id is not None and vacuum_mapping.fan_speeds:
            features |= VacuumEntityFeature.FAN_SPEED
        self._attr_supported_features = features
        self._attr_fan_speed_list = list(vacuum_mapping.fan_speeds)

    @property
    def activity(self) -> VacuumActivity | None:
        """Return the activity from the status data point, else the start switch."""
        if self._mapping.status_dp_id is not None:
            status = self.device.datapoints[self._mapping.status_dp_id]
            if status is not None:
                return self._status_activity(status.value)
        switch = self.device.datapoints[self._mapping.dp_id]
        if switch is None:
            return None
        return VacuumActivity.CLEANING if switch.value else VacuumActivity.IDLE

    def _status_activity(self, value: Any) -> VacuumActivity | None:
        """Translate a reported status (enum index or string) to an activity."""
        activities = self._mapping.status_activities
        if isinstance(value, int):
            statuses = list(activities)
            if 0 <= value < len(statuses):
                return activities[statuses[value]]
            return None
        return activities.get(str(value))

    @property
    def fan_speed(self) -> str | None:
        """Return the current cleaning mode."""
        if self._mapping.mode_dp_id is None:
            return None
        datapoint = self.device.datapoints[self._mapping.mode_dp_id]
        if datapoint is None:
            return None
        value = datapoint.value
        speeds = self._mapping.fan_speeds
        if isinstance(value, int):
            return speeds[value] if 0 <= value < len(speeds) else None
        return value if isinstance(value, str) and value in speeds else None

    async def async_start(self) -> None:
        """Start cleaning."""
        self.send_dp_value(self._mapping.dp_id, TuyaBLEDataPointType.DT_BOOL, True)

    async def async_stop(self, **kwargs: Any) -> None:
        """Stop cleaning."""
        self.send_dp_value(self._mapping.dp_id, TuyaBLEDataPointType.DT_BOOL, False)

    async def async_set_fan_speed(self, fan_speed: str, **kwargs: Any) -> None:
        """Select the cleaning mode, which does not start cleaning by itself."""
        if fan_speed not in self._mapping.fan_speeds:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_fan_speed",
                translation_placeholders={
                    "fan_speed": fan_speed,
                    "fan_speeds": ", ".join(self._mapping.fan_speeds),
                },
            )
        _LOGGER.debug("%s: setting cleaning mode to %s", self.device.address, fan_speed)
        mode = (
            self.device.datapoints[self._mapping.mode_dp_id]
            if self._mapping.mode_dp_id is not None
            else None
        )
        if mode is not None and mode.dp_type is TuyaBLEDataPointType.DT_STRING:
            # A device that reports the mode as a string expects the name back.
            self.send_dp_value(
                self._mapping.mode_dp_id, TuyaBLEDataPointType.DT_STRING, fan_speed
            )
            return
        self.send_dp_value(
            self._mapping.mode_dp_id,
            TuyaBLEDataPointType.DT_ENUM,
            self._mapping.fan_speeds.index(fan_speed),
        )


async def async_setup_entry(  # noqa: S7503
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tuya BLE vacuum entities for the config entry."""
    data: TuyaBLEData = hass.data[DOMAIN][entry.entry_id]
    mappings = get_mapping_by_device(data.device)
    entities: list[TuyaBLEVacuum] = []
    for vacuum_mapping in mappings:
        if vacuum_mapping.force_add or data.device.datapoints.has_id(
            vacuum_mapping.dp_id, vacuum_mapping.dp_type
        ):
            entities.append(
                TuyaBLEVacuum(
                    hass,
                    data.coordinator,
                    data.device,
                    data.product,
                    vacuum_mapping,
                )
            )
    async_add_entities(entities)
