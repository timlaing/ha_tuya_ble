"""Label-only changes preserve shipped entity identity and registry overrides."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant.common import (
    MockConfigEntry,
    MockEntityPlatform,
)

from custom_components.tuya_ble import number, select, switch
from custom_components.tuya_ble.const import DOMAIN
from custom_components.tuya_ble.entity import get_device_product_info
from custom_components.tuya_ble.tuya_ble import TuyaBLEDataPointType
from tests.conftest import add_dp, build_context, connect, make_credentials

_LABELS = [
    ("number", 103, "touch_duration_1", "Touch duration 1"),
    ("number", 104, "touch_duration_2", "Touch duration 2"),
    ("number", 117, "touch_off_duration_1", "Off touch duration 1"),
    ("number", 118, "touch_off_duration_2", "Off touch duration 2"),
    ("number", 119, "double_tap_interval_1", "Double-tap interval 1"),
    ("number", 120, "double_tap_interval_2", "Double-tap interval 2"),
    ("select", 101, "fingerbot_mode_1", "Mode 1"),
    ("select", 102, "fingerbot_mode_2", "Mode 2"),
    ("switch", 105, "touch_switch_1", "Touch input 1"),
    ("switch", 106, "touch_switch_2", "Touch input 2"),
    ("switch", 107, "switch_invert_1", "Invert switch 1"),
    ("switch", 108, "switch_invert_2", "Invert switch 2"),
]


@pytest.mark.parametrize(("platform_name", "dp_id", "key", "label"), _LABELS)
def test_touch_mapping_labels_keep_keys(
    hass: HomeAssistant, platform_name: str, dp_id: int, key: str, label: str
) -> None:
    """Public platform mappings retain the original keys for each data point."""
    device, coordinator, _product = build_context(hass)
    device.initialize_with_credentials(
        make_credentials(category="kg", product_id="bs3ubslo")
    )
    product = get_device_product_info(device)
    module, entity_class = {
        "number": (number, number.TuyaBLENumber),
        "select": (select, select.TuyaBLESelect),
        "switch": (switch, switch.TuyaBLESwitch),
    }[platform_name]
    mapping = next(m for m in module.get_mapping_by_device(device) if m.dp_id == dp_id)
    entity = entity_class(hass, coordinator, device, product, mapping)
    assert entity.entity_description.name == label
    assert entity.entity_description.key == key
    assert entity.translation_key == key
    assert entity.unique_id == f"device123-{key}"


@pytest.mark.parametrize("custom_name", [None, "My custom duration"])
@pytest.mark.parametrize("connected", [False, True])
async def test_touch_registry_reload_and_number_service(
    hass: HomeAssistant, custom_name: str | None, connected: bool
) -> None:
    """Existing IDs/names survive reload and services retain validation/scaling."""
    device, coordinator, product = build_context(hass)
    device.initialize_with_credentials(
        make_credentials(category="kg", product_id="bs3ubslo")
    )
    product = get_device_product_info(device)
    add_dp(device, 103, TuyaBLEDataPointType.DT_VALUE, 500)
    datapoint = device.datapoints[103]
    assert datapoint is not None
    if connected:
        await connect(coordinator)
    assert await async_setup_component(hass, "number", {})
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registered = registry.async_get_or_create(
        "number",
        DOMAIN,
        "device123-touch_duration_1",
        suggested_object_id="existing_touch_duration",
        original_name="1-04 Touch duration",
        config_entry=entry,
    )
    registry.async_update_entity(registered.entity_id, name=custom_name)
    for _reload in range(2):
        platform = MockEntityPlatform(hass, domain="number", platform_name=DOMAIN)
        platform.config_entry = entry
        entity = number.TuyaBLENumber(
            hass, coordinator, device, product, number.get_mapping_by_device(device)[0]
        )
        await platform.async_add_entities([entity])
        await hass.async_block_till_done()
        assert entity.entity_id == registered.entity_id
        current = registry.async_get(registered.entity_id)
        assert current is not None
        assert current.unique_id == registered.unique_id
        assert current.name == custom_name
        state = hass.states.get(registered.entity_id)
        assert state is not None
        # HA 2026.11 prefixes a registry override with the device name for
        # entities that opt into has_entity_name.
        assert state.attributes["friendly_name"] == (
            f"Device {custom_name}" if custom_name else "Device Touch duration 1"
        )
        if connected:
            with pytest.raises(HomeAssistantError):
                await hass.services.async_call(
                    "number",
                    "set_value",
                    {"entity_id": registered.entity_id, "value": 0.01},
                    blocking=True,
                )
            assert datapoint.value == 500
            await hass.services.async_call(
                "number",
                "set_value",
                {"entity_id": registered.entity_id, "value": 0.7},
                blocking=True,
            )
            await hass.async_block_till_done()
            assert datapoint.value == 700
            add_dp(device, 103, TuyaBLEDataPointType.DT_VALUE, 500)
        else:
            assert state.state == "unavailable"
            await hass.services.async_call(
                "number",
                "set_value",
                {"entity_id": registered.entity_id, "value": 0.7},
                blocking=True,
            )
            assert datapoint.value == 500
        await platform.async_reset()
        await hass.async_block_till_done()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject duplicate keys at every JSON nesting level."""
    result = dict(pairs)
    assert len(result) == len(pairs), "Duplicate JSON key"
    return result


@pytest.mark.parametrize("filename", ["strings.json", "translations/en.json"])
def test_english_labels_and_exception_object(filename: str) -> None:
    """English labels agree and no duplicate exception object hides messages."""
    path = Path(number.__file__).parent / filename
    data = json.loads(
        path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object
    )
    expected = {
        "select": {"beep_volume": "Lock volume"},
        "sensor": {
            "wrong_finger": "Wrong fingerprint",
            "wrong_password": "Wrong password",
            "low_battery": "Low battery",
        },
        "switch": {
            "programming_mode": "Programming mode",
            "programming_switch": "Programming switch",
            "child_lock": "Child lock",
            "window_check": "Window check",
            "lock_motor_state": "Motor state",
        },
    }
    for platform_name, labels in expected.items():
        for key, label in labels.items():
            assert data["entity"][platform_name][key]["name"] == label
    assert set(data["exceptions"]) == {
        "invalid_fan_speed",
        "button_press_failed",
        "option_not_settable",
        "ble_unlock_check_missing",
        "ble_unlock_check_invalid",
    }
