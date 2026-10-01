"""Base entity class for Tuya BLE devices."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING, Any

from home_assistant_bluetooth import BluetoothServiceInfoBleak
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.entity_registry import (
    async_get as async_get_entity_registry,
)
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .base import EnumTypeData, IntegerTypeData
from .const import (
    DEVICE_DEF_MANUFACTURER,
    DOMAIN,
    DPCode,
    DPType,
)
from .device_registry import DeviceEntities, get_registry
from .tuya_ble import (
    AbstractTuyaBLEDeviceManager,
    TuyaBLEDataPointType,
    TuyaBLEDevice,
    TuyaBLEDeviceCredentials,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .coordinator import TuyaBLECoordinator


_HASS_DATA_LEGACY_KEYS = "legacy_unique_id_suffixes"

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class TuyaBLEProductInfo:
    """Product metadata, backed by the device descriptor.

    A thin view over a descriptor, which is the single source of truth for a
    product's name, manufacturer and data point layout. A device with no
    descriptor yields an empty view, so an unsupported product still loads with
    no entities instead of failing setup.

    The data point ids are looked up by entity rather than stored here, so
    adding an entity to a descriptor is all it takes to make the handlers below
    see it -- there is no second copy of the layout to keep in step.
    """

    name: str = ""
    manufacturer: str = DEVICE_DEF_MANUFACTURER
    entities: DeviceEntities | None = None

    @classmethod
    def from_descriptor(cls, entities: DeviceEntities | None) -> TuyaBLEProductInfo:
        """Build a view of *entities*, or an empty view when it is None."""
        if entities is None:
            return cls()
        return cls(
            name=entities.device_name or "",
            manufacturer=entities.manufacturer or DEVICE_DEF_MANUFACTURER,
            entities=entities,
        )

    def dp_id_for(self, platform: str, translation_key: str) -> int | None:
        """Return the dp_id of the named entity, or None if the product lacks it."""
        if self.entities is None:
            return None
        return self.entities.dp_id_for(platform, translation_key)

    @property
    def fingerbot_mode_dp_id(self) -> int | None:
        """The mode dp_id, which is also what identifies a Fingerbot."""
        return self.dp_id_for("select", "fingerbot_mode")

    @property
    def fingerbot_switch_dp_id(self) -> int | None:
        """The dp_id that switches the Fingerbot on and off."""
        return self.dp_id_for("switch", "switch")

    @property
    def fingerbot_manual_control_dp_id(self) -> int | None:
        """The sensor button dp_id, or None on a Fingerbot that has no button."""
        return self.dp_id_for("switch", "manual_control")

    @property
    def fingerbot_program_dp_id(self) -> int | None:
        """The program dp_id, or None on a Fingerbot with no program entity.

        Only the products whose descriptors use a ``fingerbot.program.*``
        handler declare a program, so a plain Fingerbot has none.
        """
        return self.dp_id_for("number", "program_idle_position")

    @property
    def is_water_valve(self) -> bool:
        """Whether this product is a water valve.

        A water valve is the only kind of device that declares a ``valve``.
        """
        return self.dp_id_for("valve", "valve") is not None


def get_product_info_by_ids(category: str, product_id: str) -> TuyaBLEProductInfo:
    """Look up product info by category and product ID."""
    return TuyaBLEProductInfo.from_descriptor(get_registry().get(category, product_id))


def get_device_product_info(device: TuyaBLEDevice) -> TuyaBLEProductInfo:
    """Get product info for a Tuya BLE device."""
    return get_product_info_by_ids(device.category, device.product_id)


def get_short_address(address: str) -> str:
    """Get a short formatted address from a Bluetooth MAC address."""
    results = address.replace("-", ":").upper().split(":")
    return f"{results[-3]}{results[-2]}{results[-1]}"[-6:]


async def get_device_readable_name(
    discovery_info: BluetoothServiceInfoBleak,
    manager: AbstractTuyaBLEDeviceManager | None,
) -> str:
    """Get a human-readable name for a discovered BLE device."""
    credentials: TuyaBLEDeviceCredentials | None = None
    product_info: TuyaBLEProductInfo | None = None
    if manager:
        credentials = await manager.get_device_credentials(discovery_info.address)
        if credentials:
            product_info = get_product_info_by_ids(
                credentials.category,
                credentials.product_id,
            )
    short_address = get_short_address(discovery_info.address)
    if product_info and product_info.name:
        return f"{product_info.name} {short_address}"
    if credentials:
        return f"{credentials.device_name} {short_address}"
    return f"{discovery_info.device.name} {short_address}"


def get_device_info(device: TuyaBLEDevice) -> DeviceInfo | None:
    """Get Home Assistant device registry info for a Tuya BLE device."""
    product_info = get_device_product_info(device)
    device_name = (
        device.cloud_name
        or _descriptor_device_name(device)
        or device.advertised_name
        or device.address
    )
    model = (
        device.product_name
        or device.product_model
        or _descriptor_model_name(device)
        or product_info.name
        or device.product_id
    )
    sw_version = device.device_version or None
    if sw_version and device.protocol_version:
        sw_version = f"{sw_version} (protocol {device.protocol_version})"
    result = DeviceInfo(
        connections={("bluetooth", device.address)},
        hw_version=device.hardware_version or None,
        identifiers={(DOMAIN, device.address)},
        manufacturer=_descriptor_manufacturer(device) or product_info.manufacturer,
        model=model or None,
        name=device_name,
        sw_version=sw_version,
    )
    return result


def _descriptor(device: TuyaBLEDevice) -> DeviceEntities | None:
    """Return the device's descriptor, if it has one."""
    if not device.category or not device.product_id:
        return None
    return get_registry().get(device.category, device.product_id)


def _descriptor_device_name(device: TuyaBLEDevice) -> str | None:
    """Return the descriptor device_name for the device, if any."""
    product = _descriptor(device)
    return product.device_name if product is not None else None


def _descriptor_model_name(device: TuyaBLEDevice) -> str | None:
    """Return the descriptor model_name for the device, if any."""
    product = _descriptor(device)
    return product.model_name if product is not None else None


def _descriptor_manufacturer(device: TuyaBLEDevice) -> str | None:
    """Return the descriptor manufacturer for the device, if any."""
    product = _descriptor(device)
    return product.manufacturer if product is not None else None


def _find_legacy_keys(
    device: TuyaBLEDevice,
    key: str,
) -> list[str]:
    """Return legacy alias keys for *key* from the product descriptor, if any."""
    product = get_registry().get(device.category or "", device.product_id or "")
    if product is None:
        return []

    sources = [*product.entities.values(), *product.category_defaults.values()]
    for descriptors in sources:
        for desc in descriptors:
            if (desc.translation_key or str(desc.dp_id)) == key:
                return desc.legacy_keys or []
    return []


def _legacy_suffixes(hass: HomeAssistant, device_id: str) -> set[str]:
    """Return existing legacy unique-id suffixes for a device, cached per setup."""
    cache: dict[str, set[str]] = hass.data.setdefault(DOMAIN, {}).setdefault(
        _HASS_DATA_LEGACY_KEYS, {}
    )
    if device_id not in cache:
        registry = async_get_entity_registry(hass)
        prefix = f"{device_id}-"
        cache[device_id] = {
            entry.unique_id[len(prefix) :]
            for entry in registry.entities.values()
            if entry.platform == DOMAIN
            and entry.unique_id is not None
            and entry.unique_id.startswith(prefix)
        }
    return cache[device_id]


def _resolve_unique_id(
    hass: HomeAssistant,
    device: TuyaBLEDevice,
    key: str,
) -> str:
    """Resolve the unique-id, preferring a legacy id when one exists in the registry."""
    uid = f"{device.device_id}-{key}"
    legacy_keys = _find_legacy_keys(device, key)
    if legacy_keys:
        existing = _legacy_suffixes(hass, device.device_id)
        for old_key in legacy_keys:
            if old_key in existing:
                uid = f"{device.device_id}-{old_key}"
                _LOGGER.debug(
                    "%s: unique id for key %s resolved to legacy key %s",
                    device.address,
                    key,
                    old_key,
                )
                break
    else:
        _LOGGER.debug(
            "%s: resolved unique id %s (no legacy key for %s)",
            device.address,
            uid,
            key,
        )
    return uid


class TuyaBLEEntity(CoordinatorEntity["TuyaBLECoordinator"]):
    """Tuya BLE base entity."""

    def __init__(
        self,
        _hass: HomeAssistant,
        coordinator: TuyaBLECoordinator,
        device: TuyaBLEDevice,
        product: TuyaBLEProductInfo,
        description: EntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self._coordinator = coordinator
        self.device = device
        self._product = product
        if description.translation_key is None:
            self._attr_translation_key = description.key
        self.entity_description = description
        self._attr_has_entity_name = True
        self._attr_device_info = get_device_info(self.device)
        self._attr_unique_id = _resolve_unique_id(_hass, device, description.key)

    @property
    def available(self) -> bool:
        """True when coordinator is connected and the availability predicate passes."""
        return self._coordinator.connected

    def send_dp_value(
        self,
        key: int | None,
        dp_type: TuyaBLEDataPointType,
        value: bytes | bool | int | str | None = None,
    ) -> None:
        """Send a data point value to the device."""
        if key is None or value is None:
            _LOGGER.debug(
                "%s: skipping send of %s, key: %s, value: %s",
                self.device.address,
                self.entity_description.key,
                key,
                value,
            )
            return
        datapoint = self.device.datapoints.get_or_create(
            key,
            dp_type,
            value,
        )
        _LOGGER.debug("%s: sending data point %s = %s", self.device.address, key, value)
        self.hass.create_task(datapoint.set_value(value))

    def send_multiple_dp_values(
        self,
        updates: list[tuple[int, TuyaBLEDataPointType, bytes | bool | int | str]],
    ) -> None:
        """Send multiple data point values to the device atomically."""
        dp_updates: dict[int, bytes | bool | int | str] = {}
        for key, dp_type, value in updates:
            self.device.datapoints.get_or_create(key, dp_type, value)
            dp_updates[key] = value
        _LOGGER.debug(
            "%s: sending %d data point(s): %s",
            self.device.address,
            len(dp_updates),
            dp_updates,
        )
        self.hass.create_task(self.device.set_multiple_values(dp_updates))

    def find_dpid(
        self, dpcode: DPCode | None, prefer_function: bool = False
    ) -> int | None:
        """Return the dp id for the given code."""
        if dpcode is None:
            return None

        order = ["status_range", "function"]
        if prefer_function:
            order = ["function", "status_range"]
        for key in order:
            if dpcode in getattr(self.device, key):
                return int(getattr(self.device, key)[dpcode].dp_id)

        return None

    def find_dpcode(
        self,
        dpcodes: str | DPCode | tuple[DPCode, ...] | None,
        *,
        prefer_function: bool = False,
        dptype: DPType | None = None,
    ) -> DPCode | EnumTypeData | IntegerTypeData | None:
        """Find a matching DP code available on this device."""
        if dpcodes is None:
            return None

        if isinstance(dpcodes, str):
            dpcodes = (DPCode(dpcodes),)
        elif not isinstance(dpcodes, tuple):
            dpcodes = (dpcodes,)

        order = ["status_range", "function"]
        if prefer_function:
            order = ["function", "status_range"]

        if not dptype:
            order.append("status")

        for dpcode in dpcodes:
            result = self._match_dpcode(dpcode, order, dptype)
            if result is not None:
                return result

        _LOGGER.debug(
            "%s: no matching DP code found for %s in %s, entity %s will be unavailable",
            self.device.address,
            ", ".join(str(code) for code in dpcodes),
            "/".join(order),
            self.entity_description.key,
        )
        return None

    def _match_dpcode(
        self,
        dpcode: DPCode,
        order: list[str],
        dptype: DPType | None,
    ) -> DPCode | EnumTypeData | IntegerTypeData | None:
        """Check a single dpcode against ordered device attribute dicts."""
        parsed: DPCode | EnumTypeData | IntegerTypeData | None = None
        for key in order:
            attrs = getattr(self.device, key)
            if dpcode not in attrs:
                continue
            entry = attrs[dpcode]
            if dptype == DPType.ENUM and entry.type == DPType.ENUM:
                parsed = EnumTypeData.from_json(dpcode, entry.values)
                if parsed is not None:
                    break
            elif dptype == DPType.INTEGER and entry.type == DPType.INTEGER:
                parsed = IntegerTypeData.from_json(dpcode, entry.values)
                if parsed is not None:
                    break
            elif dptype not in (DPType.ENUM, DPType.INTEGER):
                parsed = dpcode
        return parsed

    def get_dptype(
        self, dpcode: DPCode | None, prefer_function: bool = False
    ) -> DPType | None:
        """Return the cloud spec data type for the given code."""
        if dpcode is None:
            return None

        order = ["status_range", "function"]
        if prefer_function:
            order = ["function", "status_range"]
        for key in order:
            if dpcode in getattr(self.device, key):
                return DPType(getattr(self.device, key)[dpcode].type)

        return None

    def _send_command(self, commands: list[dict[str, Any]]) -> None:
        """Send commands to the device."""
        _LOGGER.debug("%s: sending %d command(s)", self.device.address, len(commands))
        for command in commands:
            dp_id = command.get("dp_id")
            dp_type = command.get("dp_type")
            value = command.get("value")
            if dp_id is not None and dp_type is not None and value is not None:
                self.send_dp_value(dp_id, dp_type, value)
            else:
                _LOGGER.debug(
                    "%s: skipping command, dp_id: %s, dp_type: %s, value: %s",
                    self.device.address,
                    dp_id,
                    dp_type,
                    value,
                )


class TuyaBLERestoreEntity(TuyaBLEEntity, RestoreEntity):
    """Tuya BLE entity that keeps its last known state across restarts.

    A Tuya BLE device only pushes the data points that change, so a datapoint
    such as a push-only timer or a read-only status has no value at all until
    the device next reports it. Entities opt in through the ``restore`` flag of
    their descriptor and get the value stored by the previous run applied while
    the device has reported none, which covers the window between Home
    Assistant starting and the device answering a status request.

    Availability is left to the coordinator as usual, so a restored entity is
    still ``unavailable`` while the device is not connected.
    """

    _attr_restore: bool = False

    @property
    def _restore_dp_id(self) -> int:
        """Return the data point id whose presence supersedes a restored state."""
        raise NotImplementedError

    def _async_restore_allowed(self) -> bool:
        """Return True while the device has not reported this entity's value."""
        return self.device.datapoints[self._restore_dp_id] is None

    async def async_added_to_hass(self) -> None:
        """Register with the coordinator, then restore the last known state."""
        await super().async_added_to_hass()
        if self._attr_restore and self._async_restore_allowed():
            await self._async_restore_state()

    async def _async_restore_state(self) -> None:
        """Apply the state stored by the previous run."""
        raise NotImplementedError
