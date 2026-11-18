"""Lawn mower platform for Tuya BLE robot mowers."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any

from homeassistant.components.lawn_mower import (
    LawnMowerEntity,
    LawnMowerEntityEntityDescription,
)
from homeassistant.components.lawn_mower.const import (
    LawnMowerActivity,
    LawnMowerEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .device_registry import DeviceRegistryError, EntityDescriptor, get_registry
from .devices import TuyaBLECoordinator, TuyaBLEData, TuyaBLEEntity, TuyaBLEProductInfo
from .tuya_ble import TuyaBLEDataPointType, TuyaBLEDevice

_LOGGER = logging.getLogger(__name__)

_COMMAND_FIELDS = ("start_command", "resume_command", "pause_command", "stop_command")


@dataclass
class TuyaBLELawnMowerMapping:
    """Mapping of a status and a command data point to a lawn mower entity.

    ``dp_id`` reports the machine status as an enum index into ``statuses``;
    ``command_dp_id`` accepts the control commands as an enum index into
    ``commands``. A feature is only offered when its command is declared.
    """

    dp_id: int
    description: LawnMowerEntityEntityDescription
    force_add: bool = True
    dp_type: TuyaBLEDataPointType | None = None
    command_dp_id: int | None = None
    statuses: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    activities: dict[str, LawnMowerActivity] = field(default_factory=dict)
    start_command: str | None = None
    resume_command: str | None = None
    pause_command: str | None = None
    stop_command: str | None = None
    dock_commands: list[str] = field(default_factory=list)


@dataclass
class TuyaBLECategoryLawnMowerMapping:
    """Container for product-specific and default lawn mower mappings."""

    products: dict[str, list[TuyaBLELawnMowerMapping]] | None = None
    mapping: list[TuyaBLELawnMowerMapping] | None = None


def _string_list(desc: EntityDescriptor, name: str, value: Any) -> list[str]:
    """Validate that *value* is a list of strings."""
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise DeviceRegistryError(
            f"Lawn mower entity dp_id {desc.dp_id} '{name}' must be a list of strings"
        )
    return value


def _activities(
    desc: EntityDescriptor, statuses: list[str]
) -> dict[str, LawnMowerActivity]:
    """Validate the status → activity table against the declared statuses."""
    raw = desc.extra.get("activities") or {}
    if not isinstance(raw, dict):
        raise DeviceRegistryError(
            f"Lawn mower entity dp_id {desc.dp_id} 'activities' must be a mapping"
        )
    result: dict[str, LawnMowerActivity] = {}
    for status, activity in raw.items():
        if status not in statuses:
            raise DeviceRegistryError(
                f"Lawn mower entity dp_id {desc.dp_id} maps unknown status {status!r}"
            )
        try:
            result[status] = LawnMowerActivity(activity)
        except ValueError as exc:
            raise DeviceRegistryError(
                f"Lawn mower entity dp_id {desc.dp_id} has unknown activity "
                f"{activity!r}"
            ) from exc
    return result


def _command(desc: EntityDescriptor, name: str, commands: list[str]) -> str | None:
    """Validate that a named command is one of the declared commands."""
    command = desc.extra.get(name)
    if command is not None and command not in commands:
        raise DeviceRegistryError(
            f"Lawn mower entity dp_id {desc.dp_id} '{name}' {command!r} is not a "
            "declared command"
        )
    return command


def _build_lawn_mower_mapping(desc: EntityDescriptor) -> TuyaBLELawnMowerMapping:
    """Construct a lawn mower mapping from a registry descriptor."""
    statuses = _string_list(desc, "values", desc.values)
    commands = _string_list(desc, "commands", desc.extra.get("commands"))
    command_dp_id = desc.extra.get("command_dp_id")
    if commands and not isinstance(command_dp_id, int):
        raise DeviceRegistryError(
            f"Lawn mower entity dp_id {desc.dp_id} declares commands without an "
            "integer 'command_dp_id'"
        )
    dock_commands = _string_list(desc, "dock_commands", desc.extra.get("dock_commands"))
    for command in dock_commands:
        if command not in commands:
            raise DeviceRegistryError(
                f"Lawn mower entity dp_id {desc.dp_id} dock command {command!r} is "
                "not a declared command"
            )
    return TuyaBLELawnMowerMapping(
        dp_id=desc.dp_id,
        # pylint: disable-next=unexpected-keyword-arg
        description=LawnMowerEntityEntityDescription(
            key=desc.translation_key or str(desc.dp_id),
            name=desc.name,
            icon=desc.icon,
        ),
        force_add=desc.force_add,
        dp_type=(
            TuyaBLEDataPointType(desc.dp_type) if desc.dp_type is not None else None
        ),
        command_dp_id=command_dp_id,
        statuses=statuses,
        commands=commands,
        activities=_activities(desc, statuses),
        dock_commands=dock_commands,
        **{name: _command(desc, name, commands) for name in _COMMAND_FIELDS},
    )


def _build_mapping() -> dict[str, TuyaBLECategoryLawnMowerMapping]:
    """Build the lawn mower mappings dict from the device registry."""
    result: dict[str, TuyaBLECategoryLawnMowerMapping] = {}
    for device_entities in get_registry().products.values():
        descriptors = device_entities.get("lawn_mower")
        if not descriptors:
            continue
        category_mapping = result.setdefault(
            device_entities.category,
            TuyaBLECategoryLawnMowerMapping(products={}),
        )
        assert category_mapping.products is not None
        category_mapping.products[device_entities.product_id] = [
            _build_lawn_mower_mapping(desc) for desc in descriptors
        ]
    return result


mapping: dict[str, TuyaBLECategoryLawnMowerMapping] = _build_mapping()


def get_mapping_by_device(device: TuyaBLEDevice) -> list[TuyaBLELawnMowerMapping]:
    """Get the lawn mower mappings for a device."""
    category = mapping.get(device.category)
    if category is not None and category.products is not None:
        product_mapping = category.products.get(device.product_id)
        if product_mapping is not None:
            return product_mapping
        if category.mapping is not None:
            return category.mapping
    return []


def _supported_features(
    mower_mapping: TuyaBLELawnMowerMapping,
) -> LawnMowerEntityFeature:
    """Offer only the features whose command the descriptor declares."""
    features = LawnMowerEntityFeature(0)
    if mower_mapping.start_command is not None:
        features |= LawnMowerEntityFeature.START_MOWING
    if mower_mapping.pause_command is not None:
        features |= LawnMowerEntityFeature.PAUSE
    if mower_mapping.dock_commands:
        features |= LawnMowerEntityFeature.DOCK
    if mower_mapping.stop_command is not None:
        features |= LawnMowerEntityFeature.STOP
    return features


class TuyaBLELawnMower(TuyaBLEEntity, LawnMowerEntity):  # pylint: disable=abstract-method
    """Representation of a Tuya BLE robot lawn mower."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: TuyaBLECoordinator,
        device: TuyaBLEDevice,
        product: TuyaBLEProductInfo,
        mower_mapping: TuyaBLELawnMowerMapping,
    ) -> None:
        super().__init__(hass, coordinator, device, product, mower_mapping.description)
        self._mapping = mower_mapping
        self._attr_supported_features = _supported_features(mower_mapping)

    def _status(self) -> str | None:
        """Return the reported machine status as a declared status name."""
        datapoint = self.device.datapoints[self._mapping.dp_id]
        if datapoint is None:
            return None
        value = datapoint.value
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        if 0 <= value < len(self._mapping.statuses):
            return self._mapping.statuses[value]
        _LOGGER.debug(
            "%s: Mower status code %s is outside the declared statuses",
            self.device.address,
            value,
        )
        return None

    @property
    def activity(self) -> LawnMowerActivity | None:
        """Return the activity the reported status maps to."""
        status = self._status()
        if status is None:
            return None
        return self._mapping.activities.get(status)

    async def _send_commands(self, *commands: str | None) -> None:
        """Write each command to the command DP, in order.

        Commands share a single DP, so every write is awaited before the next
        one is encoded; a failed write stops the sequence.
        """
        dp_id = self._mapping.command_dp_id
        for command in commands:
            if command is None:
                return
            # A declared command always comes with a command DP (validated).
            assert dp_id is not None
            value = self._mapping.commands.index(command)
            _LOGGER.debug("%s: Sending mower command %s", self.device.address, command)
            datapoint = self.device.datapoints.get_or_create(
                dp_id, TuyaBLEDataPointType.DT_ENUM, value
            )
            await datapoint.set_value(value)

    async def async_start_mowing(self) -> None:
        """Start mowing, or resume the running job when paused.

        The mower rejects the start command while paused and only accepts the
        resume command in that state.
        """
        if (
            self._mapping.resume_command is not None
            and self.activity == LawnMowerActivity.PAUSED
        ):
            await self._send_commands(self._mapping.resume_command)
            return
        await self._send_commands(self._mapping.start_command)

    async def async_pause(self) -> None:
        """Pause mowing."""
        await self._send_commands(self._mapping.pause_command)

    async def async_stop(self) -> None:
        """Stop the running job."""
        await self._send_commands(self._mapping.stop_command)

    async def async_dock(self) -> None:
        """Send the mower back to its charging station."""
        await self._send_commands(*self._mapping.dock_commands)


async def async_setup_entry(  # noqa: S7503
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Tuya BLE lawn mowers."""
    data: TuyaBLEData = hass.data[DOMAIN][entry.entry_id]
    mappings = get_mapping_by_device(data.device)
    entities: list[TuyaBLELawnMower] = []
    for mower_mapping in mappings:
        if mower_mapping.force_add or data.device.datapoints.has_id(
            mower_mapping.dp_id, mower_mapping.dp_type
        ):
            entities.append(
                TuyaBLELawnMower(
                    hass,
                    data.coordinator,
                    data.device,
                    data.product,
                    mower_mapping,
                )
            )
    async_add_entities(entities)
