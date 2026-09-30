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
from custom_components.tuya_ble.products import get_product_info_by_ids


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


def test_co2_alarm_sensor_key_is_adopted_from_its_legacy_name() -> None:
    """The renamed status sensor keeps the unique id of existing entities."""
    status = get_entity_descriptors("co2bj", "59s19z5m", "sensor")[0]
    assert status.translation_key == "co2_status"
    assert status.legacy_keys == ["carbon_dioxide_alarm"]


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


def test_restore_flag_captured() -> None:
    """The restore opt-in is read from the descriptor."""
    registry = DeviceRegistry()
    _load_product(
        registry,
        {
            "sensor": [
                {"dp_id": 5, "translation_key": "x", "restore": True},
                {"dp_id": 6, "translation_key": "y", "restore": False},
                {"dp_id": 7, "translation_key": "z"},
            ]
        },
    )
    descriptors = registry.get("ms", "foo").get("sensor")  # type: ignore[union-attr]
    assert [desc.restore for desc in descriptors] == [True, False, False]
    assert "restore" not in descriptors[0].extra


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


def test_parse_entity_unknown_handler_role_raises() -> None:
    """An unrecognised handler role (e.g. a typo) is rejected, not silently dropped."""
    registry = DeviceRegistry()
    try:
        _load_product(
            registry,
            {
                "sensor": [
                    {"dp_id": 5, "handlers": {"unknown_key": "battery.battery_enum"}}
                ]
            },
        )
    except DeviceRegistryError as exc:
        assert "unknown handler roles" in str(exc)
        assert "unknown_key" in str(exc)
    else:
        raise AssertionError("expected DeviceRegistryError")


def test_parse_entity_removed_kind_field_raises() -> None:
    """A descriptor still carrying the removed 'kind' field fails loudly.

    Unknown keys are otherwise collected into ``extra`` and silently ignored,
    which would let a reintroduced ``kind:`` look like it worked.
    """
    registry = DeviceRegistry()
    with pytest.raises(DeviceRegistryError, match="removed 'kind' field"):
        _load_product(
            registry,
            {"sensor": [{"dp_id": 7, "kind": "battery", "device_class": "battery"}]},
        )


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


@pytest.mark.parametrize("field", ["model_name", "device_name"])
def test_validate_descriptor_non_string_name_field_raises(field: str) -> None:
    """A non-string model_name/device_name is rejected."""
    registry = DeviceRegistry()
    try:
        registry._load_product({
            "category": "ms",
            "product_id": "foo",
            "entities": {},
            field: ["not", "a", "string"],
        })
    except DeviceRegistryError as exc:
        assert field in str(exc)
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


def test_load_product_non_list_platform_raises() -> None:
    """A platform whose entities value is not a list is rejected."""
    registry = DeviceRegistry()
    try:
        registry._load_product({
            "category": "ms",
            "product_id": "foo",
            "entities": {"sensor": "oops"},
        })
    except DeviceRegistryError as exc:
        assert "must be a list" in str(exc)
    else:
        raise AssertionError("expected DeviceRegistryError")


def test_load_category_defaults_non_list_platform_raises() -> None:
    """A category-default platform whose entities value is not a list is rejected."""
    registry = DeviceRegistry()
    try:
        registry._load_category_defaults(
            "_category_ggq.yaml", {"entities": {"sensor": "oops"}}
        )
    except DeviceRegistryError as exc:
        assert "must be a list" in str(exc)
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


def test_resolved_handler_bad_path_raises_descriptor_error() -> None:
    """A bad handler path surfaces as a descriptor-scoped DeviceRegistryError."""
    desc = EntityDescriptor(
        platform="sensor", dp_id=7, handlers={"read": "battery.does_not_exist"}
    )
    try:
        desc.resolved_handler("read")
    except DeviceRegistryError as exc:
        assert "read" in str(exc)
        assert "battery.does_not_exist" in str(exc)
        assert "sensor" in str(exc)
    else:
        raise AssertionError("expected DeviceRegistryError")


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
        "        when: fingerbot.mode.in_program_mode\n",
        encoding="utf-8",
    )
    registry = DeviceRegistry.load()
    assert registry.get("ggq", "prod") is not None
    desc = registry.get("ggq", "prod").get("sensor")[0]  # type: ignore[union-attr]
    resolved = desc.resolved_handler("when")
    assert resolved is not None
    assert resolved.__name__ == "in_program_mode"
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


def test_parse_yaml_non_mapping_root_raises(tmp_path: Path) -> None:
    """A YAML root that is not a mapping raises DeviceRegistryError."""
    bad = tmp_path / "bad.yaml"
    bad.write_text("- just\n- a\n- list\n", encoding="utf-8")

    with pytest.raises(DeviceRegistryError, match="must be a mapping"):
        dr._parse_yaml(bad)


def test_device_entities_get_specific_over_default() -> None:
    """Specific platform entries take precedence over category defaults."""
    de = DeviceEntities(category="c", product_id="p")
    de.category_defaults = {"sensor": [EntityDescriptor(platform="sensor", dp_id=1)]}
    de.entities = {}
    assert [e.dp_id for e in de.get("sensor")] == [1]
    de.entities = {"sensor": [EntityDescriptor(platform="sensor", dp_id=9)]}
    assert [e.dp_id for e in de.get("sensor")] == [9]


def test_device_entities_empty_list_suppresses_default() -> None:
    """An explicitly empty per-product list suppresses the category default."""
    de = DeviceEntities(category="c", product_id="p")
    de.category_defaults = {"sensor": [EntityDescriptor(platform="sensor", dp_id=1)]}
    de.entities = {"sensor": []}
    assert de.get("sensor") == []


def _mapped_ids(device: DeviceEntities) -> frozenset[int]:
    """Return the mapped ids for a registry entry patched into the singleton."""
    return dr.get_mapped_dp_ids(device.category, device.product_id)


def test_get_mapped_dp_ids_unknown_product_is_empty() -> None:
    """An unregistered product has no known ids."""
    assert dr.get_mapped_dp_ids("nope", "missing") == frozenset()


def test_get_mapped_dp_ids_collects_entity_and_auxiliary_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Composite descriptors contribute their own and their auxiliary ids."""
    registry = DeviceRegistry()
    device = DeviceEntities(category="zz", product_id="zz1")
    device.entities = {
        "sensor": [
            EntityDescriptor(
                platform="sensor",
                dp_id=7,
                extra={"icons": ["mdi:battery"], "battery_dp_id": 13},
            )
        ],
        "cover": [
            EntityDescriptor(
                platform="cover",
                dp_id=0,
                extra={"state_dp_id": 1, "position_set_dp_id": 2, "position_dp_id": 3},
            )
        ],
        "lock": [EntityDescriptor(platform="lock", dp_id=47, door_dp_id=40)],
    }
    registry._products[("zz", "zz1")] = device
    monkeypatch.setattr(dr, "get_registry", lambda: registry)
    assert _mapped_ids(device) == frozenset({7, 13, 1, 2, 3, 47, 40})


def test_get_mapped_dp_ids_ignores_non_dp_extras() -> None:
    """Extras that are not data point references must not be collected."""
    assert dr._descriptor_dp_ids(
        EntityDescriptor(
            platform="sensor",
            dp_id=105,
            extra={"bitmap_mask": 3, "brightness_min": 1, "brightness_max": 100},
        )
    ) == {105}


def test_get_mapped_dp_ids_expands_nested_dp_references() -> None:
    """A dict of preset-mode ids contributes every value it holds."""
    descriptor = EntityDescriptor(
        platform="climate",
        dp_id=0,
        extra={"preset_mode_dp_ids": {"away": 106, "none": 106}},
    )
    assert dr._descriptor_dp_ids(descriptor) == {106}


def test_get_mapped_dp_ids_expands_list_dp_references() -> None:
    """A list of ids contributes each entry."""
    descriptor = EntityDescriptor(
        platform="number", dp_id=0, extra={"state_dp_ids": [2, 3]}
    )
    assert dr._descriptor_dp_ids(descriptor) == {2, 3}


@pytest.mark.parametrize("value", [True, False, "3", 3.5, None])
def test_descriptor_dp_ids_skips_non_integer_references(value: object) -> None:
    """Only plain integer data point ids are collected."""
    descriptor = EntityDescriptor(platform="sensor", dp_id=0, extra={"x_dp_id": value})
    assert dr._descriptor_dp_ids(descriptor) == set()


def test_descriptor_dp_ids_skips_zero_reference() -> None:
    """A zero id is a placeholder, not a data point."""
    descriptor = EntityDescriptor(
        platform="sensor", dp_id=0, extra={"state_dp_id": 0}, door_dp_id=0
    )
    assert dr._descriptor_dp_ids(descriptor) == set()


def test_get_mapped_dp_ids_uses_category_defaults() -> None:
    """A product with no per-platform override still inherits its category."""
    mapped = dr.get_mapped_dp_ids("ms", "bvclwu9b")
    assert {21, 8, 40} <= mapped
    assert 47 in mapped


def test_get_mapped_dp_ids_prefers_product_over_category_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The category default is not merged in when the product overrides it."""
    registry = DeviceRegistry()
    device = DeviceEntities(category="zz2", product_id="zz2")
    device.category_defaults = {
        "sensor": [EntityDescriptor(platform="sensor", dp_id=1)]
    }
    device.entities = {"sensor": [EntityDescriptor(platform="sensor", dp_id=2)]}
    registry._products[("zz2", "zz2")] = device
    monkeypatch.setattr(dr, "get_registry", lambda: registry)
    assert _mapped_ids(device) == frozenset({2})


def test_get_mapped_dp_ids_includes_handler_only_specs() -> None:
    """Data points known only from a shared handler spec count as mapped."""
    assert {8, 15, 17, 121} <= dr.get_mapped_dp_ids("szjqr", "riecov42")


def test_get_mapped_dp_ids_includes_water_valve_spec() -> None:
    """The water valve handler's data points count as mapped."""
    assert {1, 10, 11, 13, 15} <= dr.get_mapped_dp_ids("sfkzq", "16wgjvck")


def test_sensor_descriptors_never_declare_a_dp_type() -> None:
    """A sensor's ``dp_type`` is inert, so no descriptor may declare one.

    ``sensor.py`` reads ``dp_type`` only in the ``has_id`` gate deciding
    whether the entity is created, and that gate is short-circuited because no
    descriptor sets ``force_add: false``. Enum decoding uses the type the
    device pushed, not a declared one. ``select`` is the one platform where
    the field carries meaning: it picks the wire type used to serialise the
    chosen option.
    """
    offenders = [
        f"{product.category}/{product.product_id} dp_id {desc.dp_id}"
        for product in get_registry().products.values()
        for desc in product.get("sensor")
        if desc.dp_type is not None
    ]
    assert offenders == []


# The field set every sensor must carry to be a well-formed battery or
# temperature reading. These replace the ``kind: battery`` / ``kind:
# temperature`` annotations, which were inert: the sensor builder always
# passes an explicit ``description``, so a kind class's ``default_factory``
# could never take effect. Keyed on ``device_class`` so the rule still holds
# for descriptors that never carried the annotation.
_REQUIRED_SENSOR_FIELDS: dict[str, dict[str, str]] = {
    "battery": {
        "unit": "%",
        "state_class": "measurement",
        "entity_category": "diagnostic",
    },
    "temperature": {"unit": "°C", "state_class": "measurement"},
}


def _missing_required_fields(desc: dr.EntityDescriptor) -> list[str]:
    """Return the required fields a battery/temperature sensor does not set."""
    required = _REQUIRED_SENSOR_FIELDS.get(desc.device_class or "", {})
    return sorted(
        field for field, value in required.items() if getattr(desc, field) != value
    )


@pytest.mark.parametrize("device_class", sorted(_REQUIRED_SENSOR_FIELDS))
def test_battery_and_temperature_sensors_are_fully_described(
    device_class: str,
) -> None:
    """Battery and temperature sensors carry every field their class needs.

    ``kind`` used to name a mapping class here, but the sensor builder always
    passes its own ``description``, so the class defaults never applied and the
    annotation was documentation at best. Asserting the fields directly makes
    the rule enforceable and catches a partially declared sensor.
    """
    offenders = [
        f"{product.category}/{product.product_id} dp_id {desc.dp_id}"
        f" is missing {_missing_required_fields(desc)}"
        for product in get_registry().products.values()
        for desc in product.get("sensor")
        if desc.device_class == device_class and _missing_required_fields(desc)
    ]
    assert offenders == []


def _szjqr_products() -> list[DeviceEntities]:
    """Every Fingerbot-family product, in product-id order."""
    return sorted(
        (
            product
            for product in get_registry().products.values()
            if product.category == "szjqr"
        ),
        key=lambda product: product.product_id,
    )


def _szjqr_modes(product: DeviceEntities) -> list[str]:
    """Every handler path in a descriptor that gates on the device's mode."""
    return [
        role_path
        for desc in (
            product.get("button")
            + product.get("number")
            + product.get("select")
            + product.get("switch")
            + product.get("text")
        )
        for role_path in desc.handlers.values()
        if role_path.startswith("fingerbot.mode.")
    ]


def _mode_selects(product: DeviceEntities) -> list[EntityDescriptor]:
    """The mode selects a descriptor declares."""
    return [
        desc
        for desc in product.get("select")
        if desc.translation_key == "fingerbot_mode"
    ]


def test_every_mode_gated_descriptor_exposes_a_mode_select() -> None:
    """A ``fingerbot.mode.*`` gate is inert without a mode select to read.

    The gates decide which entities are available from the device's operating
    mode, but the mode itself lives on its own data point that no other entity
    exposes. A descriptor that used the gates without declaring that select had
    nothing to read the mode from, so the gated entities could not be offered.
    """
    missing = [
        f"{product.product_id} gates on mode "
        f"({sorted(set(_szjqr_modes(product)))}) but declares no mode select"
        for product in _szjqr_products()
        if _szjqr_modes(product) and not _mode_selects(product)
    ]
    assert missing == []


def test_fingerbot_mode_select_is_uniform() -> None:
    """Every mode select offers the same three modes, in the same order.

    The options map positionally onto the wire values the mode data point
    accepts, so the order is part of the contract rather than a preference.
    """
    offenders = [
        f"{product.product_id} declares {desc.options}"
        for product in _szjqr_products()
        for desc in _mode_selects(product)
        if desc.options != ["push", "switch", "program"]
    ]
    assert offenders == []


def _declared_mode_dp(product: DeviceEntities) -> int:
    """The data point ``products.py`` declares as the device's mode."""
    info = get_product_info_by_ids(product.category, product.product_id)
    assert info is not None, (
        f"{product.category}/{product.product_id} has no product info"
    )
    assert info.fingerbot is not None, (
        f"{product.category}/{product.product_id} declares no fingerbot info"
    )
    return info.fingerbot.mode


def test_fingerbot_mode_select_matches_the_declared_mode_data_point() -> None:
    """The mode select sits on the same data point the product declares.

    ``products.py`` is the only remaining record of which data point carries
    the mode for these devices; nothing else in a descriptor references it. This
    pins the select to that data point until the registry takes ownership of it.
    """
    offenders = [
        f"{product.product_id} mode select is on dp_id {desc.dp_id}, "
        f"but the product declares mode on {_declared_mode_dp(product)}"
        for product in _szjqr_products()
        for desc in _mode_selects(product)
        if desc.dp_id != _declared_mode_dp(product)
    ]
    assert offenders == []


def test_szjqr_down_position_never_excludes_half_way() -> None:
    """Any ``down_position`` that can reach 50 % must not start above it.

    The finger pushes from its fully raised position, which the devices report
    as 100 %, and 50 % is mid-travel. A floor of 51 meant the slider could not
    be returned to the centre by hand once it had been moved. Descriptors that
    legitimately travel less far -- the CubeTouch and Nedis models stop at 30 %
    or start at 0 -- are unaffected, so the rule is stated as reachability
    rather than as a fixed floor.
    """
    offenders = [
        f"{product.product_id} down_position dp_id {desc.dp_id} spans "
        f"{desc.min_value}-{desc.max_value}"
        for product in _szjqr_products()
        for desc in product.get("number")
        if desc.translation_key == "down_position"
        and desc.max_value is not None
        and desc.max_value >= 50
        and desc.min_value is not None
        and desc.min_value > 50
    ]
    assert offenders == []


@pytest.mark.parametrize("product_id", ["6jcvqwh0", "h8kdwywx", "riecov42"])
def test_fingerbot_plus_products_are_fully_described(product_id: str) -> None:
    """The three incomplete Fingerbot Plus descriptors now match the complete one.

    ``blliqpsj`` is the reference: the same hardware under a different product
    id, with its push button, mode select and program text all declared. The
    other three shipped without them, so a push could not be triggered, the mode
    could not be read, and a program could not be edited from the UI.
    """
    reference = get_registry().get("szjqr", "blliqpsj")
    assert reference is not None
    product = get_registry().get("szjqr", product_id)
    assert product is not None

    for platform in ("button", "select", "text"):
        assert product.get(platform) == reference.get(platform), (
            f"{product_id} {platform} differs from blliqpsj"
        )
