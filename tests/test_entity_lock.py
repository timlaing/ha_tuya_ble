"""Unit tests for the Tuya BLE lock entity."""
# pylint: disable=protected-access

from __future__ import annotations

from homeassistant.components.lock import LockEntityDescription
from homeassistant.core import HomeAssistant
import pytest

from custom_components.tuya_ble import lock
from custom_components.tuya_ble.device_registry import DeviceRegistry
from custom_components.tuya_ble.devices import (
    TuyaBLECoordinator,
    TuyaBLEProductInfo,
)
from custom_components.tuya_ble.lock import TuyaBLELock
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
    dp_id: int = 47,
    door_dp_id: int | None = None,
) -> TuyaBLELock:
    """Build a lock entity."""
    mapping = lock.TuyaBLELockMapping(
        dp_id=dp_id,
        description=LockEntityDescription(key="lock"),
        door_dp_id=door_dp_id,
    )
    entity = lock.TuyaBLELock(hass, coordinator, device, product, mapping)
    entity.hass = hass
    return entity


def _set_device_credentials(
    device: TuyaBLEDevice, category: str, product_id: str
) -> None:
    """Set device credentials for category/product_id lookups."""
    device._device_info = TuyaBLEDeviceCredentials(  # noqa: SLF001
        uuid="u",
        local_key="k",
        device_id="dev",
        category=category,
        product_id=product_id,
        device_name="n",
        product_model="m",
        product_name="pm",
    )


# --- is_locked ---


async def test_is_locked_true_when_motor_off(hass: HomeAssistant) -> None:
    """Verify is_locked is True when motor value is False."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    add_dp(device, 47, TuyaBLEDataPointType.DT_BOOL, False)
    assert entity.is_locked is True


async def test_is_locked_false_when_motor_on(hass: HomeAssistant) -> None:
    """Verify is_locked is False when motor value is True."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    add_dp(device, 47, TuyaBLEDataPointType.DT_BOOL, True)
    assert entity.is_locked is False


async def test_is_locked_none_without_datapoint(hass: HomeAssistant) -> None:
    """Verify is_locked is None without a datapoint."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    assert entity.is_locked is None


async def test_is_locked_with_getter(hass: HomeAssistant) -> None:
    """Verify custom getter controls is_locked."""
    device, coordinator, product = build_context(hass)
    mapping = lock.TuyaBLELockMapping(
        dp_id=47,
        description=LockEntityDescription(key="lock"),
        getter=lambda self_, product_: True,
    )
    entity = lock.TuyaBLELock(hass, coordinator, device, product, mapping)
    entity.hass = hass
    assert entity.is_locked is True


# --- is_open ---


async def test_is_open_true(hass: HomeAssistant) -> None:
    """Verify is_open returns True when door DP is 1."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product, door_dp_id=40)
    add_dp(device, 40, TuyaBLEDataPointType.DT_BOOL, 1)
    assert entity.is_open is True


async def test_is_open_false(hass: HomeAssistant) -> None:
    """Verify is_open returns False when door DP is 0."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product, door_dp_id=40)
    add_dp(device, 40, TuyaBLEDataPointType.DT_BOOL, 0)
    assert entity.is_open is False


async def test_is_open_none_without_door_dp(hass: HomeAssistant) -> None:
    """Verify is_open is None when door_dp_id is not configured."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    assert entity.is_open is None


async def test_is_open_none_when_datapoint_missing(hass: HomeAssistant) -> None:
    """Verify is_open is None when door DP datapoint is missing."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product, door_dp_id=40)
    # No add_dp for door DP
    assert entity.is_open is None


# --- async lock / unlock ---


async def test_async_lock(hass: HomeAssistant) -> None:
    """Verify async_lock sets motor to False."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    await entity.async_lock()
    dp = device.datapoints[47]
    assert dp is not None
    assert dp.value is False


async def test_async_unlock(hass: HomeAssistant) -> None:
    """Verify async_unlock sets motor to True."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    await entity.async_unlock()
    dp = device.datapoints[47]
    assert dp is not None
    assert dp.value is True


# --- sync stubs ---


async def test_sync_lock(hass: HomeAssistant) -> None:
    """Verify sync lock() does not raise."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    entity.lock()


async def test_sync_open(hass: HomeAssistant) -> None:
    """Verify sync open() does not raise."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    entity.open()


async def test_sync_unlock(hass: HomeAssistant) -> None:
    """Verify sync unlock() does not raise."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    entity.unlock()


# --- available ---


async def test_available(hass: HomeAssistant) -> None:
    """Verify availability follows the coordinator connection state."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    assert entity.available is False
    await connect(coordinator)
    assert entity.available is True


async def test_available_with_is_available(hass: HomeAssistant) -> None:
    """Verify is_available callback can override availability."""
    device, coordinator, product = build_context(hass)
    mapping = lock.TuyaBLELockMapping(
        dp_id=47,
        description=LockEntityDescription(key="lock"),
        is_available=lambda self_, product_: False,
    )
    entity = lock.TuyaBLELock(hass, coordinator, device, product, mapping)
    entity.hass = hass
    await connect(coordinator)
    assert entity.available is False


async def test_available_with_is_available_true(hass: HomeAssistant) -> None:
    """Verify is_available callback returning True keeps entity available."""
    device, coordinator, product = build_context(hass)
    mapping = lock.TuyaBLELockMapping(
        dp_id=47,
        description=LockEntityDescription(key="lock"),
        is_available=lambda self_, product_: True,
    )
    entity = lock.TuyaBLELock(hass, coordinator, device, product, mapping)
    entity.hass = hass
    await connect(coordinator)
    assert entity.available is True


# --- get_mapping_by_device ---


async def test_get_mapping_by_device_ms(hass: HomeAssistant) -> None:
    """Verify get_mapping_by_device returns mappings for ms category."""
    device, _coordinator, _product = build_context(hass)
    _set_device_credentials(device, "ms", "ludzroix")
    mappings = lock.get_mapping_by_device(device)
    assert len(mappings) == 1
    assert mappings[0].dp_id == 47


async def test_get_mapping_by_device_jtmspro(hass: HomeAssistant) -> None:
    """Verify get_mapping_by_device returns mappings for jtmspro category."""
    device, _coordinator, _product = build_context(hass)
    _set_device_credentials(device, "jtmspro", "xicdxood")
    mappings = lock.get_mapping_by_device(device)
    assert len(mappings) == 1
    assert mappings[0].dp_id == 47


async def test_get_mapping_by_device_unknown(hass: HomeAssistant) -> None:
    """Verify get_mapping_by_device returns empty for unknown category."""
    device, _coordinator, _product = build_context(hass)
    _set_device_credentials(device, "unknown_cat", "unknown_prod")
    mappings = lock.get_mapping_by_device(device)
    assert mappings == []


async def test_get_mapping_by_device_category_no_product(hass: HomeAssistant) -> None:
    """Verify get_mapping_by_device returns empty when product not in category."""
    device, _coordinator, _product = build_context(hass)
    _set_device_credentials(device, "ms", "nonexistent_product")
    mappings = lock.get_mapping_by_device(device)
    assert len(mappings) == 1
    assert mappings[0].dp_id == 47


async def test_get_mapping_by_device_jtmspro_no_product(hass: HomeAssistant) -> None:
    """Verify jtmspro without category default returns empty for unknown product."""
    device, _coordinator, _product = build_context(hass)
    _set_device_credentials(device, "jtmspro", "nonexistent_product")
    assert lock.get_mapping_by_device(device) == []


def test_build_mapping_skips_category_defaults_without_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Categories with defaults for other platforms are skipped quietly."""
    registry = DeviceRegistry()
    registry._category_defaults["ggq"] = {  # noqa: SLF001
        "sensor": [],
    }
    monkeypatch.setattr(lock, "get_registry", lambda: registry)
    assert not lock._build_mapping()  # noqa: SLF001
