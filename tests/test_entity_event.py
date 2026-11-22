"""Report identity and listener lifecycle tests for stateless events."""

from __future__ import annotations

from dataclasses import replace
from typing import Any
from unittest.mock import Mock, patch

from homeassistant.components.event import EventDeviceClass
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
import pytest
import yaml

from custom_components.tuya_ble import PLATFORMS, event, sensor
from custom_components.tuya_ble.device_registry import (
    DeviceRegistry,
    DeviceRegistryError,
    EntityDescriptor,
    get_mapped_dp_ids,
    get_registry,
)
from custom_components.tuya_ble.tuya_ble import TuyaBLEDataPointType as DPType
from tests.conftest import add_dp, build_context, make_product_info


def _mock_state_write(entity: Entity) -> None:
    """Mock only the instance write method, cleaning up on entity removal."""
    state_write = patch.object(entity, "async_write_ha_state")
    state_write.start()
    entity.async_on_remove(state_write.stop)


def _mapping(**overrides: Any) -> event.TuyaBLEEventMapping:
    """Build the real Arlec mapping, with optional test overrides."""
    base = event.mapping["wxkg"].products["kpzc6pm8"][0]
    return replace(base, **overrides)


async def _entity(hass: HomeAssistant) -> event.TuyaBLEEvent:
    """Add an event entity through the inherited HA subscription lifecycle."""
    device, coordinator, product = build_context(hass)
    entity = event.TuyaBLEEvent(hass, coordinator, device, product, _mapping())
    entity.hass = hass
    entity.entity_id = "event.test_button"
    entity.platform = Mock(platform_name="event", config_entry=None)
    _mock_state_write(entity)
    await entity.async_internal_added_to_hass()
    await entity.async_added_to_hass()
    return entity


def _report(
    entity: event.TuyaBLEEvent,
    value: bytes | bool | int | str = "single_click",
    *,
    dp_id: int = 1,
    dp_type: DPType = DPType.DT_STRING,
) -> None:
    """Deliver a fresh public datapoint report through the device callbacks."""
    add_dp(entity.device, dp_id, dp_type, value)
    dp = entity.device.datapoints[dp_id]
    assert dp is not None
    entity.device._fire_callbacks([dp])  # pylint: disable=protected-access


async def test_initial_repeated_and_unrelated_reports(hass: HomeAssistant) -> None:
    """Initial status is silent; equal clicks each have their own timestamp."""
    entity = await _entity(hass)
    assert not entity.available
    assert entity.state is None
    _report(entity)
    assert entity.available
    assert entity.state is None
    _report(entity)
    first = entity.state
    assert first is not None
    assert entity.state_attributes["event_type"] == "single_click"
    _report(entity)
    assert entity.state != first
    last = entity.state
    _report(entity, 80, dp_id=10, dp_type=DPType.DT_VALUE)
    entity.coordinator.async_update_listeners()
    assert entity.state == last
    assert entity.coordinator.last_updates == ()


@pytest.mark.parametrize("value", ["double_click", "long_press"])
async def test_click_types(hass: HomeAssistant, value: str) -> None:
    """All verified string event values reach Home Assistant."""
    entity = await _entity(hass)
    _report(entity)
    _report(entity, value)
    assert entity.state_attributes["event_type"] == value


@pytest.mark.parametrize(
    ("value", "dp_type"),
    [
        ("unknown", DPType.DT_STRING),
        (b"single_click", DPType.DT_RAW),
        (b"single_click", DPType.DT_STRING),
        (0, DPType.DT_ENUM),
        (True, DPType.DT_BOOL),
        (-1, DPType.DT_STRING),
    ],
)
async def test_unknown_or_unverified_values(
    hass: HomeAssistant, value: bytes | bool | int | str, dp_type: DPType
) -> None:
    """Never guess an enum index, accept unknown values, or crash on raw bytes."""
    entity = await _entity(hass)
    _report(entity)
    _report(entity, value, dp_type=dp_type)
    assert entity.state is None


async def test_reconnect_delayed_disconnect_and_unload(hass: HomeAssistant) -> None:
    """Session baselines reset immediately, even inside the availability grace."""
    entity = await _entity(hass)
    _report(entity)
    _report(entity)
    first = entity.state
    entity.device._fire_disconnected_callbacks()  # pylint: disable=protected-access
    assert entity.available  # Delayed availability must not delay event reset.
    entity.device._fire_connected_callbacks()  # pylint: disable=protected-access
    entity.coordinator.async_update_listeners()
    _report(entity)
    assert entity.state == first
    _report(entity)
    assert entity.state != first
    last = entity.state
    await entity.async_remove()
    _report(entity)
    assert entity.state == last
    await entity.coordinator.async_shutdown()
    assert entity.coordinator.last_updates == ()


async def test_cached_reports_and_local_writes(hass: HomeAssistant) -> None:
    """Cached state, re-dispatching an old report and local writes stay silent."""
    device, coordinator, product = build_context(hass)
    add_dp(device, 1, DPType.DT_STRING, "single_click")
    entity = event.TuyaBLEEvent(hass, coordinator, device, product, _mapping())
    entity.hass = hass
    entity.entity_id = "event.test_cached"
    _mock_state_write(entity)
    # Construction does not subscribe: a report before HA adds the entity is silent.
    dp = device.datapoints[1]
    assert dp is not None
    device._fire_callbacks([dp])  # pylint: disable=protected-access
    assert entity.state is None
    await entity.async_added_to_hass()
    device._fire_callbacks([dp])  # pylint: disable=protected-access
    assert entity.state is None
    _report(entity)
    assert entity.state is None
    _report(entity)
    first = entity.state
    device._fire_callbacks([dp])  # pylint: disable=protected-access
    dp.set_value_no_notify("double_click")
    device._fire_callbacks([dp])  # pylint: disable=protected-access
    assert entity.state == first


async def test_locally_created_datapoint_does_not_prime_events(
    hass: HomeAssistant,
) -> None:
    """Local creation and writes cannot consume the first device-report baseline."""
    device, coordinator, product = build_context(hass)
    dp = device.datapoints.get_or_create(1, DPType.DT_STRING, "single_click")
    entity = event.TuyaBLEEvent(hass, coordinator, device, product, _mapping())
    entity.hass = hass
    entity.entity_id = "event.test_local_creation"
    _mock_state_write(entity)
    await entity.async_added_to_hass()
    dp.set_value_no_notify("double_click")
    device._fire_callbacks([dp])  # pylint: disable=protected-access
    assert dp.report_revision == 0
    assert dp.reported_value is None
    assert entity.state is None
    _report(entity)
    assert entity.state is None
    _report(entity)
    first = entity.state
    assert first is not None
    _report(entity)
    assert entity.state != first
    assert entity.state_attributes == {"event_type": "single_click"}


async def test_report_value_survives_an_earlier_listener_write(
    hass: HomeAssistant,
) -> None:
    """An optimistic write during dispatch cannot replace the reported click."""
    device, coordinator, product = build_context(hass)

    def overwrite_local_value() -> None:
        for dp in coordinator.last_updates:
            dp.set_value_no_notify("long_press")

    unsubscribe = coordinator.async_add_listener(overwrite_local_value)
    entity = event.TuyaBLEEvent(hass, coordinator, device, product, _mapping())
    entity.hass = hass
    entity.entity_id = "event.test_report_snapshot"
    _mock_state_write(entity)
    await entity.async_added_to_hass()
    _report(entity)
    assert entity.state is None
    _report(entity)
    first = entity.state
    assert first is not None
    _report(entity)
    assert entity.state != first
    assert entity.state_attributes == {"event_type": "single_click"}
    dp = device.datapoints[1]
    assert dp is not None
    assert dp.value == "long_press"
    assert dp.reported_value == "single_click"
    assert dp.report_revision == 3
    unsubscribe()


async def test_boolean_doorbell(hass: HomeAssistant) -> None:
    """Generic DP24 support uses true/ring; no unverified lock is registered."""
    entity = await _entity(hass)
    descriptor = EntityDescriptor(
        platform="event",
        dp_id=24,
        device_class="doorbell",
        dp_type=1,
        translation_key="doorbell",
        extra={"event_values": {True: "ring"}},
    )
    doorbell = event.TuyaBLEEvent(
        hass,
        entity.coordinator,
        entity.device,
        make_product_info(),
        event._build_event_mapping(descriptor),  # pylint: disable=protected-access
    )
    doorbell.hass = hass
    doorbell.entity_id = "event.test_doorbell"
    _mock_state_write(doorbell)
    await doorbell.async_added_to_hass()
    _report(doorbell, True, dp_id=24, dp_type=DPType.DT_BOOL)
    assert doorbell.state is None
    _report(doorbell, False, dp_id=24, dp_type=DPType.DT_BOOL)
    assert doorbell.state is None
    _report(doorbell, True, dp_id=24, dp_type=DPType.DT_BOOL)
    first = doorbell.state
    _report(doorbell, True, dp_id=24, dp_type=DPType.DT_BOOL)
    assert doorbell.state != first
    assert doorbell.state_attributes == {"event_type": "ring"}
    assert doorbell.device_class == EventDeviceClass.DOORBELL
    assert "jtmspro" not in event.mapping


@pytest.mark.parametrize("product_id", ["kpzc6pm8", "ja5osu5g"])
async def test_descriptors_battery_and_lookup(
    hass: HomeAssistant, product_id: str
) -> None:
    """Both experimental products expose their battery and string click schema."""
    entity = await _entity(hass)
    with (
        patch.object(type(entity.device), "category", property(lambda _: "wxkg")),
        patch.object(type(entity.device), "product_id", property(lambda _: product_id)),
    ):
        mappings = event.get_mapping_by_device(entity.device)
        assert mappings[0].description.device_class == EventDeviceClass.BUTTON
        assert mappings[0].dp_type == DPType.DT_STRING
        battery_mapping = sensor.get_mapping_by_device(entity.device)[0]
        battery = sensor.TuyaBLESensor(
            hass,
            entity.coordinator,
            entity.device,
            make_product_info(),
            battery_mapping,
        )
        _mock_state_write(battery)
        _report(entity, 80, dp_id=10, dp_type=DPType.DT_VALUE)
        battery._handle_coordinator_update()  # pylint: disable=protected-access
        assert battery.native_value == 80
        assert battery.native_unit_of_measurement == "%"
        assert get_mapped_dp_ids("wxkg", product_id) == {1, 10}
    assert event.get_mapping_by_device(entity.device) == []
    with patch.object(type(entity.device), "category", property(lambda _: "wxkg")):
        assert event.get_mapping_by_device(entity.device) == []
    assert Platform.EVENT in PLATFORMS


def _load_event(tmp_path: Any, options: dict[str, Any]) -> DeviceRegistry:
    """Validate test descriptors through the registry's public loader."""
    data = {
        "category": "test",
        "product_id": "test",
        "entities": {
            "event": [
                {
                    "dp_id": 1,
                    "dp_type": 3,
                    "device_class": "button",
                    "event_values": {"single_click": "single_click"},
                    **options,
                }
            ]
        },
    }
    (tmp_path / "test.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    with patch("custom_components.tuya_ble.device_registry._DEVICES_DIR", tmp_path):
        return DeviceRegistry.load()


@pytest.mark.parametrize(
    "options",
    [
        {"dp_id": True},
        {"dp_id": 0},
        {"dp_id": 256},
        {"dp_type": True},
        {"dp_type": 2},
        {"dp_type": "3"},
        {"dp_id": "1"},
        {"event_values": []},
        {"event_values": {}},
        {"event_values": {0: "single_click"}},
        {"dp_type": 4, "event_values": {True: "single_click"}},
        {"event_values": {"click": ""}},
        {"event_values": {"click": 1}},
        {"device_class": "invalid"},
        {"restore": True},
        {"device_class": "doorbell", "event_values": {"click": "pressed"}},
    ],
)
def test_descriptor_validation(tmp_path: Any, options: dict[str, Any]) -> None:
    """Invalid, untyped and restorable event schemas fail at registry load."""
    with pytest.raises(DeviceRegistryError):
        _load_event(tmp_path, options)


def test_enum_and_default_descriptor(tmp_path: Any) -> None:
    """Explicit numeric wire evidence can be mapped without positional guessing."""
    registry = _load_event(
        tmp_path,
        {
            "dp_type": 4,
            "event_values": {7: "long_press"},
            "translation_key": None,
            "enabled_by_default": False,
        },
    )
    descriptor = registry.get_entities("test", "test", "event")[0]
    enum_mapping = event._build_event_mapping(descriptor)  # pylint: disable=protected-access
    assert enum_mapping.event_values == {7: "long_press"}
    assert enum_mapping.description.key == "1"
    assert not enum_mapping.description.entity_registry_enabled_default
    assert get_registry().get_entities("wxkg", "kpzc6pm8", "event")


def test_valid_doorbell_category_default(tmp_path: Any) -> None:
    """Category defaults use the same schema and become product mappings."""
    (tmp_path / "_category_test.yaml").write_text(
        yaml.safe_dump({
            "entities": {
                "event": [
                    {
                        "dp_id": 24,
                        "dp_type": 1,
                        "device_class": "doorbell",
                        "translation_key": "doorbell",
                        "event_values": {True: "ring"},
                    }
                ]
            },
        }),
        encoding="utf-8",
    )
    (tmp_path / "test.yaml").write_text(
        yaml.safe_dump({"category": "test", "product_id": "test"}),
        encoding="utf-8",
    )
    with patch("custom_components.tuya_ble.device_registry._DEVICES_DIR", tmp_path):
        registry = DeviceRegistry.load()
    with patch.object(event, "get_registry", return_value=registry):
        mappings = event._build_mapping()  # pylint: disable=protected-access
    assert mappings["test"].products["test"][0].event_values == {True: "ring"}


async def test_two_reports_in_one_packet_emit_both_clicks(hass: HomeAssistant) -> None:
    """Two matching reports in one packet are not collapsed to the last one."""
    entity = await _entity(hass)
    device = entity.device
    dp_id = 1

    # Baseline the first report so the next two are both real clicks.
    add_dp(device, dp_id, DPType.DT_STRING, "single_click")
    dp = device.datapoints[dp_id]
    assert dp is not None
    device._fire_callbacks([dp])  # pylint: disable=protected-access
    assert entity.state is None

    # Deliver two reports in one dispatch, both referencing the same datapoint.
    add_dp(device, dp_id, DPType.DT_STRING, "single_click")
    add_dp(device, dp_id, DPType.DT_STRING, "double_click")
    emitted: list[str] = []
    writes = 0
    original = entity._trigger_event  # pylint: disable=protected-access

    def _capture(event_type: str) -> None:
        emitted.append(event_type)
        original(event_type)

    def _count_write() -> None:
        nonlocal writes
        writes += 1

    with (
        patch.object(entity, "_trigger_event", side_effect=_capture),
        patch.object(entity, "async_write_ha_state", side_effect=_count_write),
    ):
        device._fire_callbacks([dp, dp])  # pylint: disable=protected-access

    assert emitted == ["single_click", "double_click"]
    # Each mapped report publishes its own state change, plus the final flush.
    assert writes == len(emitted) + 1
    assert entity.state_attributes["event_type"] == "double_click"
