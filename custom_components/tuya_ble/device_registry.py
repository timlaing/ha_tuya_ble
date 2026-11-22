"""Data-driven device registry backed by YAML descriptors.

Loads per-product YAML device descriptors from ``devices/``, validates them
structurally, resolves handler paths and common HA enums, and exposes
per-platform entity descriptors for each product.

``products.py`` and the platform files remain the consumers; this module
replaces the hardcoded per-platform ``mapping`` dicts.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from homeassistant.components.climate.const import HVACMode
from homeassistant.components.event import EventDeviceClass
from homeassistant.components.vacuum.const import VacuumActivity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
import yaml

from . import device_descriptors
from .const import DOMAIN
from .device_descriptors.handlers import resolve_handler

_DEVICES_DIR = Path(device_descriptors.__file__).parent
_SKIPPED_FILES = {"_schema.yaml"}
_CATEGORY_PREFIX = "_category_"
_HANDLER_ROLES = {"read", "write", "when", "pulse", "open"}
# Roles that only make sense on one platform.
_PLATFORM_HANDLER_ROLES = {"open": "lock"}
_SINGLE_ENTITY_PLATFORMS = {"climate", "cover", "light"}
_INIT_LOCK_KEY = f"{DOMAIN}_registry_init_lock"
# Switch modes with an unambiguous running action, for hvac_action_from_switch.
SWITCH_ACTION_MODES = frozenset({"heat", "cool", "dry", "fan_only"})
_SENSOR_NUMBER_OPTIONS = ("report_min_change", "report_max_interval")


class DeviceRegistryError(Exception):
    """Raised when a device descriptor is invalid or cannot be resolved."""


@dataclass
class EntityDescriptor:
    """A single declarative entity (data-point mapping) from YAML."""

    platform: str
    dp_id: int
    translation_key: str | None = None
    icon: str | None = None
    device_class: str | None = None
    unit: str | None = None
    state_class: str | None = None
    entity_category: str | None = None
    options: list[str] | None = None
    values: list[str] | None = None
    suggested_display_precision: int | None = None
    enabled_by_default: bool | None = None
    dp_type: int | None = None
    coefficient: float = 1.0
    force_add: bool = True
    restore: bool = False
    name: str | None = None
    min_value: float | None = None
    max_value: float | None = None
    step: float | None = None
    mode: str | None = None
    handlers: dict[str, str] = field(default_factory=dict)
    pattern: str | None = None
    min_length: int | None = None
    max_length: int | None = None
    door_dp_id: int | None = None
    legacy_keys: list[str] | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def resolved_entity_category(self) -> EntityCategory | None:
        """Resolve the entity category string to an HA enum."""
        if self.entity_category is None:
            return None
        try:
            return EntityCategory(self.entity_category)
        except ValueError as exc:
            raise DeviceRegistryError(
                f"Unknown entity_category: {self.entity_category!r}"
            ) from exc

    def resolved_handler(self, role: str) -> Any | None:
        """Resolve a handler role to its callable."""
        path = self.handlers.get(role)
        if path is None:
            return None
        try:
            handler = resolve_handler(path)
        except (ImportError, AttributeError, TypeError, ValueError) as exc:
            raise DeviceRegistryError(
                f"Failed to resolve {role} handler {path!r} for "
                f"{self.platform} entity dp_id {self.dp_id}"
            ) from exc
        missing = [
            key
            for key in getattr(handler, "required_dp_ids", ())
            if not _is_dp_id(self.extra.get(key))
        ]
        if missing:
            raise DeviceRegistryError(
                f"{role} handler {path!r} for {self.platform} entity dp_id "
                f"{self.dp_id} needs data point ids for {missing}"
            )
        return handler


def _is_dp_id(value: Any) -> bool:
    """Return whether *value* is a usable data point id."""
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


@dataclass
class DeviceEntities:
    """Entity descriptors for a product, per platform."""

    category: str
    product_id: str
    model_name: str | None = None
    device_name: str | None = None
    manufacturer: str | None = None
    entities: dict[str, list[EntityDescriptor]] = field(default_factory=dict)
    category_defaults: dict[str, list[EntityDescriptor]] = field(default_factory=dict)
    sensitive_dp_ids: frozenset[int] = frozenset()

    def get(self, platform: str) -> list[EntityDescriptor]:
        """Return descriptors for a platform, falling back to category defaults."""
        if platform in self.entities:
            return self.entities[platform]
        return self.category_defaults.get(platform, [])

    def dp_id_for(self, platform: str, translation_key: str) -> int | None:
        """Return the dp_id of the entity named *translation_key*, if any.

        Looks through ``get`` so a category default counts the same as a
        product-specific entity, and returns None when the product declares no
        such entity on that platform.
        """
        for descriptor in self.get(platform):
            if descriptor.translation_key == translation_key:
                return descriptor.dp_id
        return None


def _parse_handlers(platform: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Return an entity's ``handlers`` mapping after validating its roles."""
    handlers_raw = raw.get("handlers", {})
    if not isinstance(handlers_raw, dict):
        raise DeviceRegistryError(
            f"Entity {raw.get('dp_id', '?')} handlers must be a mapping"
        )
    unknown_roles = set(handlers_raw) - _HANDLER_ROLES
    if unknown_roles:
        raise DeviceRegistryError(
            f"Entity {raw.get('dp_id', '?')} has unknown handler roles: "
            f"{sorted(unknown_roles)} (expected one of {sorted(_HANDLER_ROLES)})"
        )
    misplaced_roles = sorted(
        role
        for role in handlers_raw
        if _PLATFORM_HANDLER_ROLES.get(role, platform) != platform
    )
    if misplaced_roles:
        raise DeviceRegistryError(
            f"Entity {raw.get('dp_id', '?')} on {platform!r} cannot use handler "
            f"roles {misplaced_roles}"
        )
    return handlers_raw


def _parse_entity(platform: str, raw: dict[str, Any]) -> EntityDescriptor:
    """Parse a raw entity mapping dict into an EntityDescriptor."""
    if "dp_id" not in raw and platform not in _SINGLE_ENTITY_PLATFORMS:
        raise DeviceRegistryError(f"Entity in {platform!r} missing 'dp_id': {raw}")
    if "kind" in raw:
        raise DeviceRegistryError(
            f"Entity {raw.get('dp_id', '?')} uses the removed 'kind' field. It named a "
            "mapping class whose defaults were always overridden, so it had no effect. "
            "Describe the entity with its own fields instead."
        )
    handlers_raw = _parse_handlers(platform, raw)
    legacy_keys_raw = raw.get("legacy_keys")
    if legacy_keys_raw is not None:
        if isinstance(legacy_keys_raw, str):
            legacy_keys_raw = [legacy_keys_raw]
        elif not isinstance(legacy_keys_raw, list):
            raise DeviceRegistryError(
                f"Entity {raw.get('dp_id', '?')} legacy_keys must be a list or string"
            )
        if not all(isinstance(item, str) for item in legacy_keys_raw):
            raise DeviceRegistryError(
                f"Entity {raw.get('dp_id', '?')} legacy_keys items must be strings"
            )
    for validator in _PLATFORM_VALIDATORS.get(platform, ()):
        validator(raw)
    return EntityDescriptor(
        platform=platform,
        dp_id=int(raw.get("dp_id", 0)),
        translation_key=raw.get("translation_key") or raw.get("key"),
        icon=raw.get("icon"),
        device_class=raw.get("device_class"),
        unit=raw.get("unit"),
        state_class=raw.get("state_class"),
        entity_category=raw.get("entity_category"),
        options=raw.get("options"),
        values=raw.get("values"),
        suggested_display_precision=raw.get("suggested_display_precision"),
        enabled_by_default=raw.get("enabled_by_default"),
        dp_type=raw.get("dp_type"),
        coefficient=float(raw.get("coefficient", 1.0)),
        force_add=bool(raw.get("force_add", True)),
        restore=bool(raw.get("restore", False)),
        name=raw.get("name"),
        min_value=raw.get("min_value"),
        max_value=raw.get("max_value"),
        step=raw.get("step"),
        mode=raw.get("mode"),
        pattern=raw.get("pattern"),
        min_length=raw.get("min_length"),
        max_length=raw.get("max_length"),
        door_dp_id=raw.get("door_dp_id"),
        legacy_keys=legacy_keys_raw,
        handlers=dict(handlers_raw),
        extra={
            k: v for k, v in raw.items() if k not in _BASE_ENTITY_KEYS and k != "dp_id"
        },
    )


def _validate_climate_modes(raw: dict[str, Any]) -> None:
    """Validate climate HVAC mode strings against the HVACMode enum."""
    dp_id = raw.get("dp_id", "?")
    modes = raw.get("hvac_modes", [])
    if not isinstance(modes, list):
        raise DeviceRegistryError(
            f"Climate entity {raw.get('translation_key', dp_id)!r} hvac_modes must be "
            f"a list, got {type(modes).__name__}"
        )
    for mode in modes:
        try:
            HVACMode(mode)
        except ValueError as exc:
            raise DeviceRegistryError(
                f"Climate entity {raw.get('translation_key', dp_id)!r} has unknown "
                f"hvac_mode: {mode!r}"
            ) from exc
    switch_mode = raw.get("hvac_switch_mode")
    if switch_mode is not None:
        try:
            HVACMode(switch_mode)
        except ValueError as exc:
            raise DeviceRegistryError(
                f"Climate entity {raw.get('translation_key', dp_id)!r} has unknown "
                f"hvac_switch_mode: {switch_mode!r}"
            ) from exc


def _validate_vacuum(raw: dict[str, Any]) -> None:
    """Validate vacuum status activities and fan speeds.

    ``status_activities`` maps each status value, in the data point's enum
    order, to a ``VacuumActivity``; ``fan_speeds`` lists the mode values, in
    enum order, that ``mode_dp_id`` accepts.
    """
    name = raw.get("translation_key", raw.get("dp_id", "?"))
    activities = raw.get("status_activities", {})
    if not isinstance(activities, dict):
        raise DeviceRegistryError(
            f"Vacuum entity {name!r} status_activities must be a mapping, "
            f"got {type(activities).__name__}"
        )
    for value, activity in activities.items():
        try:
            VacuumActivity(activity)
        except ValueError as exc:
            raise DeviceRegistryError(
                f"Vacuum entity {name!r} has unknown activity {activity!r} "
                f"for status {value!r}"
            ) from exc
    if activities and "status_dp_id" not in raw:
        raise DeviceRegistryError(
            f"Vacuum entity {name!r} declares status_activities without status_dp_id"
        )
    fan_speeds = raw.get("fan_speeds", [])
    if not isinstance(fan_speeds, list) or not all(
        isinstance(item, str) for item in fan_speeds
    ):
        raise DeviceRegistryError(
            f"Vacuum entity {name!r} fan_speeds must be a list of strings"
        )
    if ("mode_dp_id" in raw) != bool(fan_speeds):
        raise DeviceRegistryError(
            f"Vacuum entity {name!r} needs both mode_dp_id and fan_speeds, or neither"
        )


def _validate_event_values(raw: dict[str, Any]) -> None:
    """Require explicit, typed wire values instead of guessing enum order."""
    wire_types = {1: bool, 3: str, 4: int}
    dp_type = raw.get("dp_type")
    if (
        not isinstance(dp_type, int)
        or isinstance(dp_type, bool)
        or dp_type not in wire_types
    ):
        raise DeviceRegistryError(
            "Event dp_type must be bool (1), string (3) or enum (4)"
        )
    values = raw.get("event_values")
    if not isinstance(values, dict) or not values:
        raise DeviceRegistryError("Event event_values must be a non-empty mapping")
    for value, event_type in values.items():
        if not isinstance(value, wire_types[dp_type]) or (
            dp_type == 4 and isinstance(value, bool)
        ):
            raise DeviceRegistryError("Event wire values must match dp_type")
        if not isinstance(event_type, str) or not event_type:
            raise DeviceRegistryError("Event types must be non-empty strings")


def _validate_event(raw: dict[str, Any]) -> None:
    """Validate event metadata and disallow replay of restored values."""
    dp_id = raw.get("dp_id")
    if not isinstance(dp_id, int) or isinstance(dp_id, bool) or not 1 <= dp_id <= 255:
        raise DeviceRegistryError("Event dp_id must be an integer from 1 to 255")
    _validate_event_values(raw)
    try:
        EventDeviceClass(raw.get("device_class", ""))
    except ValueError as exc:
        raise DeviceRegistryError(
            "Event device_class must be button or doorbell"
        ) from exc
    if raw.get("restore", False):
        raise DeviceRegistryError("Event entities cannot restore data point values")
    if raw["device_class"] == "doorbell" and set(raw["event_values"].values()) != {
        "ring"
    }:
        raise DeviceRegistryError(
            "Doorbell event types must use the standard ring event"
        )


def _validate_climate_action(raw: dict[str, Any]) -> None:
    """Validate that a switch-driven hvac action names a usable switch."""
    name = raw.get("translation_key", raw.get("dp_id", "?"))
    from_switch = raw.get("hvac_action_from_switch", False)
    if not isinstance(from_switch, bool):
        raise DeviceRegistryError(
            f"Climate entity {name!r} hvac_action_from_switch must be a boolean"
        )
    if not from_switch:
        return
    switch_dp_id = raw.get("hvac_switch_dp_id")
    if (
        not isinstance(switch_dp_id, int)
        or isinstance(switch_dp_id, bool)
        or switch_dp_id <= 0
    ):
        raise DeviceRegistryError(
            f"Climate entity {name!r} hvac_action_from_switch needs a "
            "positive hvac_switch_dp_id"
        )
    if raw.get("hvac_switch_mode") not in SWITCH_ACTION_MODES:
        raise DeviceRegistryError(
            f"Climate entity {name!r} hvac_action_from_switch needs "
            f"hvac_switch_mode in {sorted(SWITCH_ACTION_MODES)}"
        )


def _validate_settable_options(raw: dict[str, Any]) -> None:
    """Validate that settable select options are a subset of the options."""
    if "settable_options" not in raw:
        return
    dp_id = raw.get("dp_id", "?")
    settable = raw["settable_options"]
    options = raw.get("options")
    if not isinstance(settable, list) or not all(
        isinstance(item, str) for item in settable
    ):
        raise DeviceRegistryError(
            f"Select entity {dp_id} settable_options must be a list of strings"
        )
    if not isinstance(options, list) or not set(settable) <= set(options):
        raise DeviceRegistryError(
            f"Select entity {dp_id} settable_options must be a subset of options"
        )


def _is_number(value: Any) -> bool:
    """Return True for an int or float that is not a bool."""
    return isinstance(value, int | float) and not isinstance(value, bool)


def _validate_light_mode(raw: dict[str, Any]) -> None:
    """Validate optional mode-controlled light parameters before handler use."""
    handlers = raw.get("handlers", {})
    has_mode_handlers = (
        handlers.get("read") == "light.mode_is_on"
        and handlers.get("write") == "light.write_mode"
    )
    if "mode_dp_id" not in raw:
        if has_mode_handlers:
            raise DeviceRegistryError(
                "Light entity with mode_is_on/write_mode handlers requires 'mode_dp_id'"
            )
        return
    for key in ("mode_dp_id", "color_mode_dp_id", "brightness_dp_id"):
        _validate_light_index(raw, key, 1)
    if len({raw["mode_dp_id"], raw["color_mode_dp_id"], raw["brightness_dp_id"]}) != 3:
        raise DeviceRegistryError(
            "Light mode, work mode and brightness DP ids must differ"
        )
    for key in ("on_value", "off_value", "white_value"):
        _validate_light_index(raw, key, 0)
    if raw["on_value"] == raw["off_value"]:
        raise DeviceRegistryError("Light on_value and off_value must differ")
    minimum = raw.get("brightness_min", 0)
    maximum = raw.get("brightness_max", 255)
    if not _is_number(minimum) or not _is_number(maximum) or not 0 <= minimum < maximum:
        raise DeviceRegistryError(
            "Light brightness range must be non-negative and ordered"
        )


def _validate_light_index(raw: dict[str, Any], key: str, minimum: int) -> None:
    """Validate a wire DP id or enum index, excluding bools."""
    value = raw.get(key)
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not minimum <= value <= 255
    ):
        raise DeviceRegistryError(
            f"Light {key} must be an integer from {minimum} to 255"
        )


def _validate_sensor_report_options(raw: dict[str, Any]) -> None:
    """Validate the gating and throttling options read by sensor handlers."""
    dp_id = raw.get("dp_id", "?")
    if "active_dp_id" in raw:
        active_dp_id = raw["active_dp_id"]
        if (
            not isinstance(active_dp_id, int)
            or isinstance(active_dp_id, bool)
            or active_dp_id <= 0
        ):
            raise DeviceRegistryError(
                f"Sensor entity {dp_id} active_dp_id must be a positive integer"
            )
    for option in _SENSOR_NUMBER_OPTIONS:
        if option in raw and (not _is_number(raw[option]) or raw[option] < 0):
            raise DeviceRegistryError(
                f"Sensor entity {dp_id} {option} must be a non-negative number"
            )


_PLATFORM_VALIDATORS: dict[str, tuple[Callable[[dict[str, Any]], None], ...]] = {
    "climate": (_validate_climate_modes, _validate_climate_action),
    "event": (_validate_event,),
    "light": (_validate_light_mode,),
    "select": (_validate_settable_options,),
    "sensor": (_validate_sensor_report_options,),
    "vacuum": (_validate_vacuum,),
}


_BASE_ENTITY_KEYS = {
    "dp_id",
    "translation_key",
    "key",
    "icon",
    "device_class",
    "unit",
    "state_class",
    "entity_category",
    "options",
    "values",
    "suggested_display_precision",
    "enabled_by_default",
    "dp_type",
    "coefficient",
    "force_add",
    "restore",
    "name",
    "min_value",
    "max_value",
    "step",
    "mode",
    "handlers",
    "pattern",
    "min_length",
    "max_length",
    "door_dp_id",
    "legacy_keys",
}


@dataclass
class DeviceRegistry:
    """Registry of device descriptors loaded from the devices package."""

    _products: dict[tuple[str, str], DeviceEntities] = field(default_factory=dict)
    _category_defaults: dict[str, dict[str, list[EntityDescriptor]]] = field(
        default_factory=dict
    )

    @classmethod
    def load(cls) -> DeviceRegistry:
        """Load and validate all descriptors from the devices package."""
        registry = cls()
        for path in sorted(_DEVICES_DIR.glob("*.yaml")):
            if path.name in _SKIPPED_FILES:
                continue
            data = _parse_yaml(path)
            if path.name.startswith(_CATEGORY_PREFIX):
                registry._load_category_defaults(path.name, data)
            else:
                registry._load_product(data)
        return registry

    def _load_category_defaults(self, filename: str, data: dict[str, Any]) -> None:
        category = filename[len(_CATEGORY_PREFIX) :].removesuffix(".yaml")
        entities = data.get("entities", {})
        if not isinstance(entities, dict):
            raise DeviceRegistryError(f"{filename}: 'entities' must be a mapping")
        for platform, es in entities.items():
            if not isinstance(es, list):
                raise DeviceRegistryError(
                    f"{filename}: '{platform}' entities must be a list, "
                    f"got {type(es).__name__}"
                )
            self._category_defaults.setdefault(category, {})[platform] = [
                _parse_entity(platform, e) for e in es
            ]

    def _load_product(self, data: dict[str, Any]) -> None:
        _validate_descriptor(data)
        category = data["category"]
        product_id = data["product_id"]
        entities = data.get("entities", {})
        if not isinstance(entities, dict):
            raise DeviceRegistryError(f"{product_id}: 'entities' must be a mapping")
        for platform, es in entities.items():
            if not isinstance(es, list):
                raise DeviceRegistryError(
                    f"{product_id}: '{platform}' entities must be a list, "
                    f"got {type(es).__name__}"
                )
        device_entities = DeviceEntities(
            category=category,
            product_id=product_id,
            model_name=data.get("model_name"),
            device_name=data.get("device_name"),
            manufacturer=data.get("manufacturer"),
            entities={
                platform: [_parse_entity(platform, e) for e in es]
                for platform, es in entities.items()
            },
            category_defaults=self._category_defaults.get(category, {}),
            sensitive_dp_ids=frozenset(data.get("sensitive_dp_ids") or ()),
        )
        self._products[(category, product_id)] = device_entities

    def get(self, category: str, product_id: str) -> DeviceEntities | None:
        """Return the device entities for a category/product, if registered."""
        return self._products.get((category, product_id))

    def get_entities(
        self, category: str, product_id: str, platform: str
    ) -> list[EntityDescriptor]:
        """Return platform entity descriptors for a product, merged."""
        device = self.get(category, product_id)
        if device is None:
            return []
        return device.get(platform)

    @property
    def products(self) -> dict[tuple[str, str], DeviceEntities]:
        """Return all loaded per-product entities keyed by (category, product_id)."""
        return self._products

    @property
    def category_defaults(self) -> dict[str, dict[str, list[EntityDescriptor]]]:
        """Return per-category platform entity defaults."""
        return self._category_defaults


def _validate_descriptor(data: dict[str, Any]) -> None:
    """Validate structural fields of a loaded product descriptor."""
    if not isinstance(data.get("product_id"), str):
        raise DeviceRegistryError(f"Device descriptor missing 'product_id': {data}")
    if not isinstance(data.get("category"), str):
        raise DeviceRegistryError(
            f"Device descriptor for {data.get('product_id')!r} missing 'category'"
        )
    sensitive_dp_ids = data.get("sensitive_dp_ids")
    if sensitive_dp_ids is not None and (
        not isinstance(sensitive_dp_ids, list)
        or not all(_is_dp_id(dp_id) for dp_id in sensitive_dp_ids)
    ):
        raise DeviceRegistryError(
            f"Device descriptor for {data.get('product_id')!r} "
            "'sensitive_dp_ids' must be a list of data point ids"
        )
    for name_field in ("model_name", "device_name", "manufacturer"):
        if data.get(name_field) is not None and not isinstance(
            data.get(name_field), str
        ):
            raise DeviceRegistryError(
                f"Device descriptor for {data.get('product_id')!r} "
                f"'{name_field}' must be a string, "
                f"got {type(data.get(name_field)).__name__}"
            )


def _parse_yaml(path: Path) -> dict[str, Any]:
    """Parse a YAML file into a dict."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise DeviceRegistryError(
            f"Device descriptor {path} must be a mapping, got {type(data).__name__}"
        )
    return data


@lru_cache(maxsize=1)
def get_registry() -> DeviceRegistry:
    """Return the lazily-loaded, cached device registry."""
    return DeviceRegistry.load()


async def async_setup_registry(hass: HomeAssistant) -> DeviceRegistry:
    """Load the descriptor registry off the event loop, once per HA instance.

    Discovery, file reads, YAML parsing and validation all happen in an
    executor job, so a cold startup never blocks the event loop. The executor
    target is the cached accessor ``get_registry`` rather than
    ``DeviceRegistry.load``, so the ``lru_cache`` is populated and every later
    lookup is strictly in-memory. The per-instance lock serialises concurrent
    config-entry setups onto a single load, and a failed load caches nothing so
    a retry is possible.
    """
    lock = hass.data.get(_INIT_LOCK_KEY)
    if lock is None:
        lock = hass.data[_INIT_LOCK_KEY] = asyncio.Lock()
    async with lock:
        return await hass.async_add_executor_job(get_registry)


def get_entity_descriptors(
    category: str, product_id: str, platform: str
) -> list[EntityDescriptor]:
    """Return platform entity descriptors for a product."""
    return get_registry().get_entities(category, product_id, platform)


def _iter_dp_reference(value: Any) -> Iterator[int]:
    """Yield the data point ids held by a single auxiliary descriptor value."""
    if isinstance(value, bool):
        return
    if isinstance(value, int):
        if value:
            yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_dp_reference(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _iter_dp_reference(item)


def _descriptor_dp_ids(descriptor: EntityDescriptor) -> set[int]:
    """Return every data point id referenced by a single entity descriptor.

    Covers the entity's own ``dp_id`` and any auxiliary reference stored in
    ``extra`` under an ``*_dp_id`` / ``*_dp_ids`` key, which is how composite
    entities (cover position, lock door, climate targets) point at the data
    points their handlers read. Keys such as ``bitmap_mask`` or
    ``brightness_min`` do not match and are correctly ignored, and ``dp_id: 0``
    is skipped because it is the placeholder used by single-entity platforms.
    """
    ids: set[int] = set()
    if descriptor.dp_id:
        ids.add(descriptor.dp_id)
    if descriptor.door_dp_id:
        ids.add(descriptor.door_dp_id)
    for key, value in descriptor.extra.items():
        if key.endswith(("_dp_id", "_dp_ids")):
            ids.update(_iter_dp_reference(value))
    return ids


def get_mapped_dp_ids(category: str, product_id: str) -> frozenset[int]:
    """Return every data point id this product's entities are expected to use.

    Used to tell a genuinely unsupported data point apart from one that simply
    has no entity yet, so diagnostics only report the former. Product-specific
    overrides take precedence over the category defaults, matching how
    entities are actually created.

    Every data point a shared handler reads is declared as an entity of its own
    -- the fingerbot mode, switch, manual control and program entities, and the
    water valve -- so scanning the entities covers them too.

    An empty result means the product is unknown to the descriptor registry.
    """
    ids: set[int] = set()
    device = get_registry().get(category, product_id)
    if device is not None:
        for platform in {*device.entities, *device.category_defaults}:
            for descriptor in device.get(platform):
                ids.update(_descriptor_dp_ids(descriptor))
    return frozenset(ids)
