"""Unit tests for the Tuya BLE cover entity."""
# pylint: disable=protected-access

from __future__ import annotations

from unittest.mock import patch

from homeassistant.components.cover import (
    ATTR_POSITION,
    ATTR_TILT_POSITION,
    CoverDeviceClass,
    CoverEntityDescription,
    CoverEntityFeature,
)
from homeassistant.core import HomeAssistant

from custom_components.tuya_ble import cover
from custom_components.tuya_ble.cover import TuyaBLECover
from custom_components.tuya_ble.devices import (
    TuyaBLECoordinator,
    TuyaBLEProductInfo,
)
from custom_components.tuya_ble.tuya_ble import (
    TuyaBLEDataPointType,
    TuyaBLEDevice,
)
from custom_components.tuya_ble.tuya_ble.manager import TuyaBLEDeviceCredentials
from tests.conftest import add_dp, build_context, connect


def _make_entity(
    hass: HomeAssistant,
    device: TuyaBLEDevice,
    coordinator: TuyaBLECoordinator,
    product: TuyaBLEProductInfo,
    state_dp_id: int = 1,
    position_set_dp_id: int = 2,
    position_dp_id: int = 3,
    tilt_dp_id: int = 0,
) -> TuyaBLECover:
    """Build a cover entity."""
    mapping = cover.TuyaBLECoverMapping(
        description=CoverEntityDescription(key="test_cover"),
        state_dp_id=state_dp_id,
        position_set_dp_id=position_set_dp_id,
        position_dp_id=position_dp_id,
        tilt_dp_id=tilt_dp_id,
    )
    entity = cover.TuyaBLECover(hass, coordinator, device, product, mapping)
    entity.hass = hass
    return entity


def _set_device_credentials(
    device: TuyaBLEDevice, category: str, product_id: str
) -> None:
    """Set device credentials for category/product_id lookups."""
    device._device_info = TuyaBLEDeviceCredentials(
        uuid="u",
        local_key="k",
        device_id="dev",
        category=category,
        product_id=product_id,
        device_name="n",
        product_model="m",
        product_name="pm",
    )


# --- Position / state reading ---


async def test_is_closed_true(hass: HomeAssistant) -> None:
    """Verify is_closed is True when position is 0."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    add_dp(device, 3, TuyaBLEDataPointType.DT_VALUE, 100)  # raw 100 -> HA 0
    entity._update_state_from_datapoints()
    assert entity.is_closed is True


async def test_is_closed_false(hass: HomeAssistant) -> None:
    """Verify is_closed is False when position > 0."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    add_dp(device, 3, TuyaBLEDataPointType.DT_VALUE, 50)
    entity._update_state_from_datapoints()
    assert entity.is_closed is False


async def test_current_cover_position(hass: HomeAssistant) -> None:
    """Verify position is inverted from raw value."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    add_dp(device, 3, TuyaBLEDataPointType.DT_VALUE, 30)  # raw 30 -> HA 70
    entity._update_state_from_datapoints()
    assert entity.current_cover_position == 70


async def test_state_dp_opening(hass: HomeAssistant) -> None:
    """Verify state DP 0 sets is_opening."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    add_dp(device, 1, TuyaBLEDataPointType.DT_VALUE, 0)
    entity._update_state_from_datapoints()
    assert entity.is_opening is True


async def test_state_dp_closing(hass: HomeAssistant) -> None:
    """Verify state DP 2 sets is_closing."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    add_dp(device, 1, TuyaBLEDataPointType.DT_VALUE, 2)
    entity._update_state_from_datapoints()
    assert entity.is_closing is True


async def test_state_dp_stop(hass: HomeAssistant) -> None:
    """Verify state DP 1 clears opening/closing."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    add_dp(device, 1, TuyaBLEDataPointType.DT_VALUE, 1)
    entity._update_state_from_datapoints()
    assert entity.is_opening is False
    assert entity.is_closing is False


async def test_position_closed_sets_is_closed(hass: HomeAssistant) -> None:
    """Verify position 0 sets is_closed and clears is_closing."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    add_dp(device, 1, TuyaBLEDataPointType.DT_VALUE, 2)
    add_dp(device, 3, TuyaBLEDataPointType.DT_VALUE, 100)  # raw 100 -> HA 0
    entity._update_state_from_datapoints()
    assert entity.is_closed is True
    assert entity.is_closing is False


async def test_position_open_clears_opening(hass: HomeAssistant) -> None:
    """Verify position 100 clears is_opening."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    add_dp(device, 1, TuyaBLEDataPointType.DT_VALUE, 0)
    add_dp(device, 3, TuyaBLEDataPointType.DT_VALUE, 0)  # raw 0 -> HA 100
    entity._update_state_from_datapoints()
    assert entity.is_opening is False


# --- Tilt ---


async def test_tilt_position_mapping(hass: HomeAssistant) -> None:
    """Verify tilt position is mapped from device range 1-10 to HA 0-100."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product, tilt_dp_id=101)
    add_dp(device, 101, TuyaBLEDataPointType.DT_VALUE, 1)  # 1 -> 0%
    entity._update_state_from_datapoints()
    assert entity.current_cover_tilt_position == 0
    add_dp(device, 101, TuyaBLEDataPointType.DT_VALUE, 10)  # 10 -> 100%
    entity._update_state_from_datapoints()
    assert entity.current_cover_tilt_position == 100


async def test_tilt_no_dp(hass: HomeAssistant) -> None:
    """Verify tilt position stays 0 when tilt DP is not configured."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    entity._update_state_from_datapoints()
    assert entity.current_cover_tilt_position == 0


# --- Features / device class ---


async def test_supported_features(hass: HomeAssistant) -> None:
    """Verify supported features include OPEN, CLOSE, STOP, SET_POSITION."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    features = entity.supported_features
    assert features & CoverEntityFeature.OPEN
    assert features & CoverEntityFeature.CLOSE
    assert features & CoverEntityFeature.STOP
    assert features & CoverEntityFeature.SET_POSITION
    assert not features & CoverEntityFeature.SET_TILT_POSITION


async def test_supported_features_with_tilt(hass: HomeAssistant) -> None:
    """Verify SET_TILT_POSITION is added when tilt_dp_id is set."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product, tilt_dp_id=101)
    features = entity.supported_features
    assert features & CoverEntityFeature.SET_TILT_POSITION


async def test_device_class(hass: HomeAssistant) -> None:
    """Verify device class is SHADE."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    assert entity.device_class == CoverDeviceClass.SHADE


async def test_device_class_with_tilt(hass: HomeAssistant) -> None:
    """Verify device class is SHADE with tilt."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product, tilt_dp_id=101)
    assert entity.device_class == CoverDeviceClass.SHADE


# --- Apply cover state (force-update logic) ---


async def test_apply_cover_state_open(hass: HomeAssistant) -> None:
    """Verify _apply_cover_state sets is_opening for state 0."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    add_dp(device, 3, TuyaBLEDataPointType.DT_VALUE, 50)
    entity._update_state_from_datapoints()
    entity._apply_cover_state(0)
    assert entity.is_opening is True
    assert entity.is_closing is False
    assert entity.is_closed is False


async def test_apply_cover_state_close(hass: HomeAssistant) -> None:
    """Verify _apply_cover_state sets is_closing for state 2."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    add_dp(device, 3, TuyaBLEDataPointType.DT_VALUE, 50)
    entity._update_state_from_datapoints()
    entity._apply_cover_state(2)
    assert entity.is_closing is True
    assert entity.is_opening is False


async def test_apply_cover_state_stop(hass: HomeAssistant) -> None:
    """Verify _apply_cover_state clears flags for state 1."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    add_dp(device, 3, TuyaBLEDataPointType.DT_VALUE, 50)
    entity._update_state_from_datapoints()
    entity._apply_cover_state(1)
    assert entity.is_opening is False
    assert entity.is_closing is False


async def test_apply_cover_state_open_at_full(hass: HomeAssistant) -> None:
    """Verify _apply_cover_state does not set is_opening when already at 100."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    add_dp(device, 3, TuyaBLEDataPointType.DT_VALUE, 0)  # raw 0 -> HA 100
    entity._update_state_from_datapoints()
    entity._apply_cover_state(0)
    assert entity.is_opening is False


async def test_apply_cover_state_close_at_zero(hass: HomeAssistant) -> None:
    """Verify _apply_cover_state sets is_closed when at position 0."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    add_dp(device, 3, TuyaBLEDataPointType.DT_VALUE, 100)  # raw 100 -> HA 0
    entity._update_state_from_datapoints()
    entity._apply_cover_state(2)
    assert entity.is_closed is True
    assert entity.is_closing is False


# --- No DP paths ---


async def test_no_state_dp(hass: HomeAssistant) -> None:
    """Verify no crash when state_dp_id is 0."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product, state_dp_id=0)
    entity._update_state_from_datapoints()
    assert not entity.is_opening


async def test_no_position_dp(hass: HomeAssistant) -> None:
    """Verify no crash when position_dp_id is 0."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product, position_dp_id=0)
    entity._update_state_from_datapoints()
    assert entity.current_cover_position == 0


# --- get_mapping_by_device ---


async def test_get_mapping_by_device_cl(hass: HomeAssistant) -> None:
    """Verify get_mapping_by_device returns mappings for cl category."""
    device, _coordinator, _product = build_context(hass)
    _set_device_credentials(device, "cl", "4pbr8eig")
    mappings = cover.get_mapping_by_device(device)
    assert len(mappings) == 1
    assert mappings[0].state_dp_id == 1


async def test_get_mapping_by_device_cl_curtain(hass: HomeAssistant) -> None:
    """Verify curtain controller mapping."""
    device, _coordinator, _product = build_context(hass)
    _set_device_credentials(device, "cl", "kcy0x4pi")
    mappings = cover.get_mapping_by_device(device)
    assert len(mappings) == 1
    assert mappings[0].tilt_dp_id == 0


async def test_get_mapping_by_device_cl_venetian(hass: HomeAssistant) -> None:
    """Verify venetian blind motor mapping has tilt."""
    device, _coordinator, _product = build_context(hass)
    _set_device_credentials(device, "cl", "dy4dh1q0")
    mappings = cover.get_mapping_by_device(device)
    assert len(mappings) == 1
    assert mappings[0].tilt_dp_id == 101


async def test_get_mapping_by_device_unknown(hass: HomeAssistant) -> None:
    """Verify get_mapping_by_device returns empty for unknown category."""
    device, _coordinator, _product = build_context(hass)
    _set_device_credentials(device, "unknown_cat", "unknown_prod")
    mappings = cover.get_mapping_by_device(device)
    assert mappings == []


async def test_get_mapping_by_device_category_with_no_products(
    hass: HomeAssistant,
) -> None:
    """Verify get_mapping_by_device returns empty when category has no products."""
    device, _coordinator, _product = build_context(hass)
    device._device_info = None
    mappings = cover.get_mapping_by_device(device)
    assert mappings == []


async def test_get_mapping_by_device_fallback(hass: HomeAssistant) -> None:
    """Verify get_mapping_by_device returns category.mapping when product not found."""
    device, _coordinator, _product = build_context(hass)
    _set_device_credentials(device, "cl", "nonexistent_product")
    mappings = cover.get_mapping_by_device(device)
    assert len(mappings) == 1
    assert mappings[0].state_dp_id == 1


# --- Availability ---


async def test_available(hass: HomeAssistant) -> None:
    """Verify availability follows the coordinator connection state."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    assert entity.available is False
    await connect(coordinator)
    assert entity.available is True


# --- async_set_cover_position ---


async def test_async_set_cover_position(hass: HomeAssistant) -> None:
    """Verify async_set_cover_position sends inverted position."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    await entity.async_set_cover_position(**{ATTR_POSITION: 70})
    dp = device.datapoints[2]
    assert dp is not None
    assert dp.value == 30  # 100 - 70


async def test_async_set_cover_position_no_set_dp(hass: HomeAssistant) -> None:
    """Verify async_set_cover_position is a no-op when position_set_dp_id is 0."""
    device, coordinator, product = build_context(hass)
    mapping = cover.TuyaBLECoverMapping(
        description=CoverEntityDescription(key="test_cover"),
        position_set_dp_id=0,
    )
    entity = cover.TuyaBLECover(hass, coordinator, device, product, mapping)
    entity.hass = hass
    await entity.async_set_cover_position(**{ATTR_POSITION: 50})
    assert entity.current_cover_position == 0


# --- Tilt async methods ---


async def test_async_open_cover_tilt(hass: HomeAssistant) -> None:
    """Verify async_open_cover_tilt sends 10 to tilt DP."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product, tilt_dp_id=101)
    await entity.async_open_cover_tilt()
    dp = device.datapoints[101]
    assert dp is not None
    assert dp.value == 10


async def test_async_close_cover_tilt(hass: HomeAssistant) -> None:
    """Verify async_close_cover_tilt sends 1 to tilt DP."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product, tilt_dp_id=101)
    await entity.async_close_cover_tilt()
    dp = device.datapoints[101]
    assert dp is not None
    assert dp.value == 1


async def test_async_set_cover_tilt_position(hass: HomeAssistant) -> None:
    """Verify async_set_cover_tilt_position maps 0-100 to 1-10."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product, tilt_dp_id=101)
    await entity.async_set_cover_tilt_position(**{ATTR_TILT_POSITION: 50})
    dp = device.datapoints[101]
    assert dp is not None
    assert dp.value == round(50 / 100 * 9 + 1)


async def test_async_open_cover_tilt_no_tilt(hass: HomeAssistant) -> None:
    """Verify async_open_cover_tilt is a no-op when tilt_dp_id is 0."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    await entity.async_open_cover_tilt()
    assert device.datapoints[101] is None


async def test_async_close_cover_tilt_no_tilt(hass: HomeAssistant) -> None:
    """Verify async_close_cover_tilt is a no-op when tilt_dp_id is 0."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    await entity.async_close_cover_tilt()
    assert device.datapoints[101] is None


async def test_async_set_cover_tilt_position_no_tilt(hass: HomeAssistant) -> None:
    """Verify async_set_cover_tilt_position is a no-op when tilt_dp_id is 0."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    await entity.async_set_cover_tilt_position(**{ATTR_TILT_POSITION: 50})
    assert device.datapoints[101] is None


# --- _update_cover_state (async open/close/stop) ---


async def test_async_open_cover(hass: HomeAssistant) -> None:
    """Verify async_open_cover sends state 0."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    with patch.object(entity, "async_write_ha_state"):
        await entity.async_open_cover()
    dp = device.datapoints[1]
    assert dp is not None
    assert dp.dp_type == TuyaBLEDataPointType.DT_VALUE


async def test_async_close_cover(hass: HomeAssistant) -> None:
    """Verify async_close_cover sends state 2."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    with patch.object(entity, "async_write_ha_state"):
        await entity.async_close_cover()
    dp = device.datapoints[1]
    assert dp is not None
    assert dp.value == 2


async def test_async_stop_cover(hass: HomeAssistant) -> None:
    """Verify async_stop_cover sends state 1."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    with patch.object(entity, "async_write_ha_state"):
        await entity.async_stop_cover()
    dp = device.datapoints[1]
    assert dp is not None
    assert dp.value == 1


async def test_update_cover_state_no_state_dp(hass: HomeAssistant) -> None:
    """Verify _update_cover_state is a no-op when state_dp_id is 0."""
    device, coordinator, product = build_context(hass)
    mapping = cover.TuyaBLECoverMapping(
        description=CoverEntityDescription(key="test_cover"),
        state_dp_id=0,
    )
    entity = cover.TuyaBLECover(hass, coordinator, device, product, mapping)
    entity.hass = hass
    await entity._update_cover_state(0)
    assert device.datapoints[1] is None


# --- Sync stubs ---


async def test_sync_open_cover(hass: HomeAssistant) -> None:
    """Verify sync open_cover() does not raise."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    entity.open_cover()


async def test_sync_close_cover(hass: HomeAssistant) -> None:
    """Verify sync close_cover() does not raise."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    entity.close_cover()


async def test_sync_stop_cover(hass: HomeAssistant) -> None:
    """Verify sync stop_cover() does not raise."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    entity.stop_cover()
