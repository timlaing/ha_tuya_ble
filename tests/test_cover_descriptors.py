"""Behavioural tests for source-backed curtain and blind descriptors."""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, patch

from homeassistant.components.cover import ATTR_POSITION, CoverEntityFeature
from homeassistant.core import HomeAssistant
import pytest

from custom_components.tuya_ble.cover import (
    TuyaBLECategoryCoverMapping,
    TuyaBLECover,
    get_mapping_by_device,
)
from custom_components.tuya_ble.device_registry import get_mapped_dp_ids, get_registry
from custom_components.tuya_ble.entity import get_device_product_info
from custom_components.tuya_ble.sensor import TuyaBLESensor
from custom_components.tuya_ble.sensor import get_mapping_by_device as sensor_mappings
from custom_components.tuya_ble.tuya_ble import TuyaBLEDataPointType
from tests.conftest import add_dp, build_context, make_credentials

PRODUCTS = ("cq7xquhj", "y0dtvgqf", "ousymtkt", "v3fzfd2y", "4pbr8eig")


@pytest.mark.parametrize("product_id", PRODUCTS)
@pytest.mark.parametrize("position", [0, 27, 100])
async def test_cover_descriptor_positions(
    hass: HomeAssistant, product_id: str, position: int
) -> None:
    """Position endpoints and intermediate values round-trip without drift."""
    device, coordinator, _ = build_context(hass)
    device.initialize_with_credentials(
        make_credentials(category="cl", product_id=product_id)
    )
    mapping = get_mapping_by_device(device)[0]
    entity = TuyaBLECover(
        hass, coordinator, device, get_device_product_info(device), mapping
    )
    entity.hass = hass
    raw = position if product_id == "cq7xquhj" else 100 - position
    add_dp(device, mapping.position_dp_id, TuyaBLEDataPointType.DT_VALUE, raw)
    # A movement report at an endpoint must not leave the motor moving past it.
    state = 2 if product_id == "cq7xquhj" else 0
    add_dp(device, 1, mapping.state_dp_type, state)
    await entity.async_added_to_hass()
    with patch.object(entity, "async_write_ha_state"):
        coordinator.async_update_listeners()
    assert entity.current_cover_position == position
    assert entity.is_closed is (position == 0)
    assert entity.is_opening is (position != 100)

    state = 0 if product_id == "cq7xquhj" else 2
    add_dp(device, 1, mapping.state_dp_type, state)
    with patch.object(entity, "async_write_ha_state"):
        coordinator.async_update_listeners()
    assert entity.is_closing is (position != 0)
    with patch.object(device, "send_datapoints", new_callable=AsyncMock) as send:
        await entity.async_set_cover_position(**{ATTR_POSITION: position})
    send.assert_awaited_once_with([mapping.position_set_dp_id])
    target = device.datapoints[mapping.position_set_dp_id]
    assert target is not None
    assert target.value == raw
    assert target.dp_type == TuyaBLEDataPointType.DT_VALUE


@pytest.mark.parametrize("product_id", PRODUCTS)
@pytest.mark.parametrize("state", [0, 1, 2, 3])
async def test_cover_descriptor_movement_reports(
    hass: HomeAssistant, product_id: str, state: int
) -> None:
    """Invert reported open/close, but not stop or an unknown/continue report."""
    device, coordinator, _ = build_context(hass)
    device.initialize_with_credentials(
        make_credentials(category="cl", product_id=product_id)
    )
    mapping = get_mapping_by_device(device)[0]
    entity = TuyaBLECover(
        hass, coordinator, device, get_device_product_info(device), mapping
    )
    entity.hass = hass
    add_dp(device, mapping.position_dp_id, TuyaBLEDataPointType.DT_VALUE, 50)
    add_dp(device, 1, mapping.state_dp_type, state)
    await entity.async_added_to_hass()
    with patch.object(entity, "async_write_ha_state"):
        coordinator.async_update_listeners()
    opening, closing = (2, 0) if product_id == "cq7xquhj" else (0, 2)
    assert entity.is_opening is (state == opening)
    assert entity.is_closing is (state == closing)


@pytest.mark.parametrize("product_id", PRODUCTS)
@pytest.mark.parametrize("position", [None, 0, 37, 100])
async def test_cover_descriptor_commands(
    hass: HomeAssistant, product_id: str, position: int | None
) -> None:
    """Missing reports are safe; writes use source polarity and preserve HA intent."""
    device, coordinator, _ = build_context(hass)
    device.initialize_with_credentials(
        make_credentials(category="cl", product_id=product_id)
    )
    mapping = get_mapping_by_device(device)[0]
    entity = TuyaBLECover(
        hass, coordinator, device, get_device_product_info(device), mapping
    )
    entity.hass = hass
    await entity.async_added_to_hass()
    with (
        patch.object(entity, "async_write_ha_state"),
        patch.object(device, "send_datapoints", new_callable=AsyncMock) as send,
    ):
        coordinator.async_update_listeners()
        assert not entity.is_opening
        assert not entity.is_closing
        assert entity.current_cover_position == 0
        if position is not None:
            raw = position if product_id == "cq7xquhj" else 100 - position
            add_dp(device, mapping.position_dp_id, TuyaBLEDataPointType.DT_VALUE, raw)
            coordinator.async_update_listeners()
        await entity.async_open_cover()
        control = device.datapoints[1]
        assert control is not None
        assert control.dp_type == mapping.state_dp_type
        assert control.value == (2 if product_id == "cq7xquhj" else 0)
        assert entity.is_opening is (position != 100)
        assert not entity.is_closing
        await entity.async_close_cover()
        assert control.value == (0 if product_id == "cq7xquhj" else 2)
        assert not entity.is_opening
        assert entity.is_closing is (position not in (None, 0))
        await entity.async_stop_cover()
        assert control.value == 1
        assert not entity.is_opening
        assert not entity.is_closing
    assert send.await_count == 3


@pytest.mark.parametrize(
    ("product_id", "dp_ids", "battery"),
    [
        ("cq7xquhj", {1, 2, 3, 13}, True),
        ("y0dtvgqf", {1, 2, 3}, False),
        ("ousymtkt", {1, 8, 9, 13}, True),
        ("v3fzfd2y", {1, 2, 3, 13}, True),
    ],
)
def test_cover_descriptor_scope(
    product_id: str, dp_ids: set[int], battery: bool
) -> None:
    """Only verified control/position/battery DPs are mapped, never calibration."""
    registry = get_registry()
    assert get_mapped_dp_ids("cl", product_id) == dp_ids
    descriptors = registry.products[("cl", product_id)]
    assert descriptors.device_name
    assert descriptors.model_name
    sensors = descriptors.get("sensor")
    assert len(sensors) == int(battery)
    if battery:
        assert sensors[0].dp_id == 13
        assert sensors[0].translation_key == "battery"
    cover = descriptors.get("cover")[0]
    assert cover.extra.get("inverted", False) is (product_id == "cq7xquhj")
    assert cover.dp_type == (4 if product_id == "y0dtvgqf" else None)


@pytest.mark.parametrize("product_id", ["cq7xquhj", "ousymtkt", "v3fzfd2y"])
async def test_cover_descriptor_battery(hass: HomeAssistant, product_id: str) -> None:
    """Battery reads come from the mapped report, never a cover command."""
    device, coordinator, _ = build_context(hass)
    device.initialize_with_credentials(
        make_credentials(category="cl", product_id=product_id)
    )
    mapping = sensor_mappings(device)[0]
    entity = TuyaBLESensor(
        hass, coordinator, device, get_device_product_info(device), mapping
    )
    entity.hass = hass
    assert entity.native_value is None
    await entity.async_added_to_hass()
    add_dp(device, 13, TuyaBLEDataPointType.DT_VALUE, 73)
    with patch.object(entity, "async_write_ha_state"):
        coordinator.async_update_listeners()
    assert entity.native_value == 73


async def test_inverted_cover_report_supersedes_pending_command(
    hass: HomeAssistant,
) -> None:
    """A fresh closing report overrides an inverted opening command before ACK."""
    device, coordinator, _ = build_context(hass)
    device.initialize_with_credentials(
        make_credentials(category="cl", product_id="cq7xquhj")
    )
    mapping = get_mapping_by_device(device)[0]
    entity = TuyaBLECover(
        hass, coordinator, device, get_device_product_info(device), mapping
    )
    entity.hass = hass
    await entity.async_added_to_hass()
    add_dp(device, 1, mapping.state_dp_type, 1)
    add_dp(device, 3, TuyaBLEDataPointType.DT_VALUE, 40)
    started, acknowledge = asyncio.Event(), asyncio.Event()

    async def send(_dp_ids: list[int]) -> None:
        started.set()
        await acknowledge.wait()

    with (
        patch.object(device, "send_datapoints", new=AsyncMock(side_effect=send)),
        patch.object(entity, "async_write_ha_state"),
    ):
        coordinator.async_update_listeners()
        command = asyncio.create_task(entity.async_open_cover())
        await started.wait()
        control = device.datapoints[1]
        assert control is not None
        assert control.value == 2
        add_dp(device, 13, TuyaBLEDataPointType.DT_VALUE, 73)
        coordinator.async_update_listeners()
        assert not entity.is_opening
        add_dp(device, 1, mapping.state_dp_type, 0)
        coordinator.async_update_listeners()
        assert entity.is_closing
        assert not entity.is_opening
        acknowledge.set()
        await command
        assert entity.is_closing
        assert not entity.is_opening
        assert entity.current_cover_position == 40


async def test_cover_missing_optional_datapoints(hass: HomeAssistant) -> None:
    """Unmapped services and absent optional tilt reports preserve defaults."""
    device, coordinator, _ = build_context(hass)
    device.initialize_with_credentials(
        make_credentials(category="cl", product_id="cq7xquhj")
    )
    mapping = replace(
        get_mapping_by_device(device)[0],
        state_dp_id=0,
        position_set_dp_id=0,
        tilt_dp_id=101,
    )
    entity = TuyaBLECover(
        hass, coordinator, device, get_device_product_info(device), mapping
    )
    entity.hass = hass
    await entity.async_added_to_hass()
    with patch.object(entity, "async_write_ha_state"):
        coordinator.async_update_listeners()
    assert entity.current_cover_tilt_position == 0
    assert entity.supported_features == CoverEntityFeature.SET_TILT_POSITION
    with patch.dict(
        "custom_components.tuya_ble.cover.mapping",
        {"cl": TuyaBLECategoryCoverMapping(products={})},
    ):
        assert get_mapping_by_device(device) == []
