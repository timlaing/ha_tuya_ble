"""Data-driven device registry backed by YAML descriptors.

Loads per-product YAML device descriptors from ``devices/``, validates them
structurally, resolves handler paths and common HA enums, and exposes
per-platform entity descriptors for each product.

``products.py`` and the platform files remain the consumers; this module
replaces the hardcoded per-platform ``mapping`` dicts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from homeassistant.const import EntityCategory
import yaml

from . import device_descriptors
from .device_descriptors.handlers import resolve_handler

_DEVICES_DIR = Path(device_descriptors.__file__).parent
_SKIPPED_FILES = {"_schema.yaml"}
_CATEGORY_PREFIX = "_category_"
_HANDLER_ROLES = {"read", "write", "when"}
_SINGLE_ENTITY_PLATFORMS = {"climate", "cover", "light"}


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
    enabled_by_default: bool | None = None
    dp_type: int | None = None
    coefficient: float = 1.0
    force_add: bool = True
    name: str | None = None
    min_value: float | None = None
    max_value: float | None = None
    step: float | None = None
    mode: str | None = None
    kind: str | None = None
    handlers: dict[str, str] = field(default_factory=dict)
    pattern: str | None = None
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
        """Resolve a handler role (read/write/when) to its callable."""
        path = self.handlers.get(role)
        if path is None:
            return None
        return resolve_handler(path)


@dataclass
class DeviceEntities:
    """Entity descriptors for a product, per platform."""

    category: str
    product_id: str
    entities: dict[str, list[EntityDescriptor]] = field(default_factory=dict)
    category_defaults: dict[str, list[EntityDescriptor]] = field(default_factory=dict)

    def get(self, platform: str) -> list[EntityDescriptor]:
        """Return descriptors for a platform, falling back to category defaults."""
        if platform in self.entities:
            return self.entities[platform]
        return self.category_defaults.get(platform, [])


def _parse_entity(platform: str, raw: dict[str, Any]) -> EntityDescriptor:
    """Parse a raw entity mapping dict into an EntityDescriptor."""
    if "dp_id" not in raw and platform not in _SINGLE_ENTITY_PLATFORMS:
        raise DeviceRegistryError(f"Entity in {platform!r} missing 'dp_id': {raw}")
    handlers_raw = raw.get("handlers", {})
    if not isinstance(handlers_raw, dict):
        raise DeviceRegistryError(
            f"Entity {raw.get('dp_id', '?')} handlers must be a mapping"
        )
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
        name=raw.get("name"),
        min_value=raw.get("min_value"),
        max_value=raw.get("max_value"),
        step=raw.get("step"),
        mode=raw.get("mode"),
        kind=raw.get("kind"),
        pattern=raw.get("pattern"),
        door_dp_id=raw.get("door_dp_id"),
        legacy_keys=legacy_keys_raw,
        handlers={
            role: path for role, path in handlers_raw.items() if role in _HANDLER_ROLES
        },
        extra={
            k: v for k, v in raw.items() if k not in _BASE_ENTITY_KEYS and k != "dp_id"
        },
    )


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
    "name",
    "min_value",
    "max_value",
    "step",
    "mode",
    "kind",
    "handlers",
    "pattern",
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
            entities={
                platform: [_parse_entity(platform, e) for e in es]
                for platform, es in entities.items()
            },
            category_defaults=self._category_defaults.get(category, {}),
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


def get_entity_descriptors(
    category: str, product_id: str, platform: str
) -> list[EntityDescriptor]:
    """Return platform entity descriptors for a product."""
    return get_registry().get_entities(category, product_id, platform)
