"""Base entity class for Tuya BLE devices."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.entity_registry import (
    async_get as async_get_entity_registry,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .base import EnumTypeData, IntegerTypeData
from .const import (
    DEVICE_DEF_MANUFACTURER,
    DOMAIN,
    DPCode,
    DPType,
)
from .products import TuyaBLEProductInfo, get_product_info_by_ids
from .tuya_ble import (
    TuyaBLEDataPointType,
    TuyaBLEDevice,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .coordinator import TuyaBLECoordinator
    from .device_registry import EntityDescriptor


def get_device_info(device: TuyaBLEDevice) -> DeviceInfo | None:
    """Get Home Assistant device registry info for a Tuya BLE device."""
    product_info = None
    if device.category and device.product_id:
        product_info = get_product_info_by_ids(device.category, device.product_id)
    device_name = device.name
    model = (
        device.product_name
        or device.product_model
        or (product_info.name if product_info else "")
        or device.product_id
    )
    sw_version = device.device_version or None
    if sw_version and device.protocol_version:
        sw_version = f"{sw_version} (protocol {device.protocol_version})"
    result = DeviceInfo(
        connections={("bluetooth", device.address)},
        hw_version=device.hardware_version or None,
        identifiers={(DOMAIN, device.address)},
        manufacturer=(
            product_info.manufacturer if product_info else DEVICE_DEF_MANUFACTURER
        ),
        model=model or None,
        name=device_name,
        sw_version=sw_version,
    )
    return result


def _find_legacy_keys(
    device: TuyaBLEDevice,
    key: str,
) -> list[str]:
    """Return legacy alias keys for *key* from the product descriptor, if any."""
    from .device_registry import get_registry  # pylint: disable=C0415

    reg = get_registry()
    product = reg.get(device.category or "", device.product_id or "")
    if product is None:
        return []

    _missing: list[str] = []

    def _scan(descriptors: list[EntityDescriptor]) -> list[str] | None:
        for desc in descriptors:
            if (desc.translation_key or str(desc.dp_id)) == key:
                return desc.legacy_keys
        return None

    for _, items in product.entities.items():
        if (found := _scan(items)) is not None:
            return found
    for _, defaults in product.category_defaults.items():
        if (found := _scan(defaults)) is not None:
            return found
    return _missing


def _resolve_unique_id(
    hass: HomeAssistant,
    device: TuyaBLEDevice,
    key: str,
) -> str:
    """Resolve the unique-id, preferring a legacy id when one exists in the registry."""
    uid = f"{device.device_id}-{key}"
    legacy_keys = _find_legacy_keys(device, key)
    if legacy_keys:
        registry = async_get_entity_registry(hass)
        prefix = f"{device.device_id}-"
        targets = set(legacy_keys)
        existing = {
            entry.unique_id[len(prefix) :]
            for entry in registry.entities.values()
            if entry.platform == DOMAIN
            and entry.unique_id is not None
            and entry.unique_id.startswith(prefix)
            and entry.unique_id[len(prefix) :] in targets
        }
        for old_key in legacy_keys:
            if old_key in existing:
                uid = f"{prefix}{old_key}"
                break
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
            return
        datapoint = self.device.datapoints.get_or_create(
            key,
            dp_type,
            value,
        )
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
        for command in commands:
            dp_id = command.get("dp_id")
            dp_type = command.get("dp_type")
            value = command.get("value")
            if dp_id is not None and dp_type is not None and value is not None:
                self.send_dp_value(dp_id, dp_type, value)
