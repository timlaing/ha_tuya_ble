"""Unit tests for the data-driven device registry."""
# pylint: disable=protected-access

from __future__ import annotations

from pathlib import Path

import pytest

import custom_components.tuya_ble.device_registry as dr
from custom_components.tuya_ble.device_registry import (
    DeviceEntities,
    DeviceRegistry,
    DeviceRegistryError,
    EntityDescriptor,
    get_entity_descriptors,
    get_registry,
)


def _descriptor() -> EntityDescriptor:
    """Build a representative sensor entity descriptor."""
    return EntityDescriptor(
        platform="sensor",
        dp_id=1,
        translation_key="foo",
        device_class="battery",
        unit="%",
        state_class="measurement",
        entity_category="diagnostic",
    )


def _load_product(registry: DeviceRegistry, entities: dict[str, object]) -> None:
    """Load a single product with a fixed category/pid and given entities."""
    registry._load_product({
        "category": "ms",
        "product_id": "foo",
        "entities": entities,
    })


def test_registry_loads_all_real_products() -> None:
    """The cached registry loads every shipped YAML descriptor."""
    registry = get_registry()
    assert len(registry._products) > 50
    device = registry.get("co2bj", "59s19z5m")
    assert device is not None
    assert device.category == "co2bj"
    assert device.product_id == "59s19z5m"


def test_get_entity_descriptors_returns_sensor_entities() -> None:
    """The co2 detector sensor descriptors are exposed in order."""
    descriptors = get_entity_descriptors("co2bj", "59s19z5m", "sensor")
    assert [d.dp_id for d in descriptors] == [1, 2, 15, 18, 19]


def test_get_entity_descriptors_unknown_product_returns_empty() -> None:
    """Unknown products and categories yield no descriptors."""
    assert get_entity_descriptors("co2bj", "nope", "sensor") == []
    assert get_entity_descriptors("nope", "59s19z5m", "sensor") == []


def test_translation_key_maps_from_key() -> None:
    """A raw `key` is adopted as the translation_key fallback."""
    registry = DeviceRegistry()
    _load_product(registry, {"sensor": [{"dp_id": 5, "key": "carbon_dioxide"}]})
    desc = registry.get("ms", "foo").get("sensor")[0]  # type: ignore[union-attr]
    assert desc.translation_key == "carbon_dioxide"


def test_extra_fields_captured() -> None:
    """Platform-specific extra fields are preserved on the descriptor."""
    registry = DeviceRegistry()
    _load_product(
        registry,
        {
            "sensor": [
                {
                    "dp_id": 5,
                    "translation_key": "x",
                    "icons": ["mdi:alert"],
                    "coefficient": 2.0,
                    "force_add": False,
                }
            ]
        },
    )
    desc = registry.get("ms", "foo").get("sensor")[0]  # type: ignore[union-attr]
    assert desc.extra["icons"] == ["mdi:alert"]
    assert desc.coefficient == 2.0
    assert desc.force_add is False


def test_category_default_fallback_merge() -> None:
    """Specific platform entries win and unmapped platforms fall back."""
    registry = DeviceRegistry()
    registry._load_category_defaults(
        "_category_ggq.yaml",
        {
            "entities": {
                "sensor": [{"dp_id": 9, "translation_key": "category_dp"}],
                "button": [{"dp_id": 10, "translation_key": "button_dp"}],
            }
        },
    )
    registry._load_product({
        "category": "ggq",
        "product_id": "hfgdqhho",
        "entities": {"sensor": [{"dp_id": 7, "translation_key": "specific"}]},
    })
    device = registry.get("ggq", "hfgdqhho")
    assert device is not None
    assert [d.dp_id for d in device.get("sensor")] == [7]
    assert [d.dp_id for d in device.get("button")] == [10]
    assert device.get("unknown_platform") == []


def test_parse_entity_missing_dp_id_raises() -> None:
    """An entity without dp_id is rejected."""
    registry = DeviceRegistry()
    try:
        _load_product(registry, {"sensor": [{"translation_key": "x"}]})
    except DeviceRegistryError as exc:
        assert "dp_id" in str(exc)
    else:
        raise AssertionError("expected DeviceRegistryError")


def test_parse_entity_non_dict_handlers_raises() -> None:
    """Handlers must be a mapping."""
    registry = DeviceRegistry()
    try:
        _load_product(registry, {"sensor": [{"dp_id": 5, "handlers": "bad"}]})
    except DeviceRegistryError as exc:
        assert "handlers" in str(exc)
    else:
        raise AssertionError("expected DeviceRegistryError")


def test_parse_entity_non_dict_handlers_raises_without_dp_id() -> None:
    """A single-entity platform without dp_id still reports a valid error."""
    registry = DeviceRegistry()
    try:
        _load_product(
            registry, {"light": [{"translation_key": "x", "handlers": "bad"}]}
        )
    except DeviceRegistryError as exc:
        assert "handlers" in str(exc)
    else:
        raise AssertionError("expected DeviceRegistryError")


def test_parse_entity_legacy_keys_string_coerced() -> None:
    """A single-string legacy_keys is coerced to a list."""
    registry = DeviceRegistry()
    _load_product(
        registry,
        {"sensor": [{"dp_id": 5, "translation_key": "new", "legacy_keys": "old"}]},
    )
    products = registry.get("ms", "foo")
    assert products is not None
    desc = products.get("sensor")[0]
    assert desc.legacy_keys == ["old"]


def test_parse_entity_legacy_keys_invalid_type_raises() -> None:
    """An unsupported legacy_keys type is rejected."""
    registry = DeviceRegistry()
    try:
        _load_product(registry, {"sensor": [{"dp_id": 5, "legacy_keys": 42}]})
    except DeviceRegistryError as exc:
        assert "legacy_keys" in str(exc)
    else:
        raise AssertionError("expected DeviceRegistryError")


def test_parse_entity_legacy_keys_non_string_item_raises() -> None:
    """A list item that is not a string is rejected."""
    registry = DeviceRegistry()
    try:
        _load_product(registry, {"sensor": [{"dp_id": 5, "legacy_keys": [123]}]})
    except DeviceRegistryError as exc:
        assert "legacy_keys" in str(exc)
    else:
        raise AssertionError("expected DeviceRegistryError")


def test_validate_descriptor_missing_product_id_raises() -> None:
    """A descriptor without product_id is rejected."""
    registry = DeviceRegistry()
    try:
        registry._load_product({"category": "ms", "entities": {}})
    except DeviceRegistryError as exc:
        assert "product_id" in str(exc)
    else:
        raise AssertionError("expected DeviceRegistryError")


def test_validate_descriptor_missing_category_raises() -> None:
    """A descriptor without category is rejected."""
    registry = DeviceRegistry()
    try:
        registry._load_product({"product_id": "foo", "entities": {}})
    except DeviceRegistryError as exc:
        assert "category" in str(exc)
    else:
        raise AssertionError("expected DeviceRegistryError")


def test_load_product_non_dict_entities_raises() -> None:
    """A non-mapping entities value is rejected."""
    registry = DeviceRegistry()
    try:
        registry._load_product({
            "category": "ms",
            "product_id": "foo",
            "entities": "oops",
        })
    except DeviceRegistryError as exc:
        assert "entities" in str(exc)
    else:
        raise AssertionError("expected DeviceRegistryError")


def test_load_category_defaults_non_dict_entities_raises() -> None:
    """A category-default file with non-mapping entities is rejected."""
    registry = DeviceRegistry()
    try:
        registry._load_category_defaults("_category_ggq.yaml", {"entities": "oops"})
    except DeviceRegistryError as exc:
        assert "entities" in str(exc)
    else:
        raise AssertionError("expected DeviceRegistryError")


def test_resolved_entity_category() -> None:
    """A valid entity_category resolves to an HA enum."""
    resolved = _descriptor().resolved_entity_category()
    assert resolved is not None
    assert resolved.value == "diagnostic"


def test_resolved_entity_category_unknown_raises() -> None:
    """An unknown entity_category raises DeviceRegistryError."""
    desc = EntityDescriptor(platform="sensor", dp_id=1, entity_category="nope")
    try:
        desc.resolved_entity_category()
    except DeviceRegistryError as exc:
        assert "entity_category" in str(exc)
    else:
        raise AssertionError("expected DeviceRegistryError")


def test_resolved_entity_category_none() -> None:
    """A descriptor without entity_category resolves to None."""
    assert (
        EntityDescriptor(platform="sensor", dp_id=1).resolved_entity_category() is None
    )


def test_resolved_handler_none_when_unset() -> None:
    """Handlers that are not set resolve to None."""
    assert _descriptor().resolved_handler("read") is None
    assert _descriptor().resolved_handler("when") is None


def test_load_skips_schema_and_loads_category_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """load() skips _schema.yaml and loads _category_ prefix files."""
    monkeypatch.setattr(dr, "_DEVICES_DIR", tmp_path)
    (tmp_path / "_schema.yaml").write_text("schema: true\n", encoding="utf-8")
    (tmp_path / "_category_ggq.yaml").write_text(
        "entities:\n  sensor:\n    - dp_id: 9\n      translation_key: cat_dp\n",
        encoding="utf-8",
    )
    (tmp_path / "g_prod.yaml").write_text(
        "category: ggq\n"
        "product_id: prod\n"
        "entities:\n"
        "  sensor:\n"
        "    - dp_id: 1\n"
        "      translation_key: x\n"
        "      handlers:\n"
        "        when: co2.alarm_enabled\n",
        encoding="utf-8",
    )
    registry = DeviceRegistry.load()
    assert registry.get("ggq", "prod") is not None
    desc = registry.get("ggq", "prod").get("sensor")[0]  # type: ignore[union-attr]
    resolved = desc.resolved_handler("when")
    assert resolved is not None
    assert resolved.__name__ == "alarm_enabled"
    assert registry._category_defaults["ggq"]["sensor"][0].dp_id == 9


def test_state_class_and_precision_passthrough() -> None:
    """state_class and suggested_display_precision are preserved."""
    desc = EntityDescriptor(
        platform="sensor",
        dp_id=1,
        state_class="measurement",
        suggested_display_precision=2,
    )
    assert desc.state_class == "measurement"
    assert desc.suggested_display_precision == 2


def test_device_entities_get_specific_over_default() -> None:
    """Specific platform entries take precedence over category defaults."""
    de = DeviceEntities(category="c", product_id="p")
    de.category_defaults = {"sensor": [EntityDescriptor(platform="sensor", dp_id=1)]}
    de.entities = {}
    assert [e.dp_id for e in de.get("sensor")] == [1]
    de.entities = {"sensor": [EntityDescriptor(platform="sensor", dp_id=9)]}
    assert [e.dp_id for e in de.get("sensor")] == [9]
