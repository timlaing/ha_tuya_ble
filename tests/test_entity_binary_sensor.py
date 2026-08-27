"""Unit tests for the Tuya BLE binary sensor entity."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant

from custom_components.tuya_ble import binary_sensor
from custom_components.tuya_ble.devices import (
    TuyaBLECoordinator,
    TuyaBLEProductInfo,
)
from custom_components.tuya_ble.tuya_ble import (
    TuyaBLEDataPointType,
    TuyaBLEDevice,
)
from tests.conftest import add_dp, build_context, connect


def _make_entity(
    hass: HomeAssistant,
    device: TuyaBLEDevice,
    coordinator: TuyaBLECoordinator,
    product: TuyaBLEProductInfo,
) -> binary_sensor.TuyaBLEBinarySensor:
    """Build a binary sensor entity."""
    mapping = binary_sensor.TuyaBLEBinarySensorMapping(
        dp_id=11,
        description=BinarySensorEntityDescription(key="battery"),
    )
    entity = binary_sensor.TuyaBLEBinarySensor(
        hass, coordinator, device, product, mapping
    )
    entity.hass = hass
    return entity


async def test_handle_update(hass: HomeAssistant) -> None:
    """Verify coordinator updates flow through to the binary sensor."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    await entity.async_added_to_hass()
    add_dp(device, 11, TuyaBLEDataPointType.DT_BOOL, True)
    coordinator.async_set_updated_data({})
    await hass.async_block_till_done()
    assert entity.is_on is True


async def test_handle_update_getter(hass: HomeAssistant) -> None:
    """Verify a custom getter drives the binary sensor state."""
    device, coordinator, product = build_context(hass)
    mapping = binary_sensor.TuyaBLEBinarySensorMapping(
        dp_id=11,
        description=BinarySensorEntityDescription(key="battery"),
        getter=lambda self_: setattr(self_, "_attr_is_on", True),
    )
    entity = binary_sensor.TuyaBLEBinarySensor(
        hass, coordinator, device, product, mapping
    )
    await entity.async_added_to_hass()
    coordinator.async_set_updated_data({})
    await hass.async_block_till_done()
    assert entity.is_on is True


async def test_handle_update_no_datapoint(hass: HomeAssistant) -> None:
    """Verify binary sensor stays None when the mapped datapoint is absent."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    await entity.async_added_to_hass()
    coordinator.async_set_updated_data({})
    await hass.async_block_till_done()
    assert entity.is_on is None


async def test_available(hass: HomeAssistant) -> None:
    """Verify availability follows the coordinator connection state."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    assert entity.available is False
    await connect(coordinator)
    assert entity.available is True


async def test_available_with_is_available(hass: HomeAssistant) -> None:
    """Verify the is_available callback is invoked when set."""
    device, coordinator, product = build_context(hass)
    mapping = binary_sensor.TuyaBLEBinarySensorMapping(
        dp_id=11,
        description=BinarySensorEntityDescription(key="battery"),
        is_available=lambda self_, product_: True,
    )
    entity = binary_sensor.TuyaBLEBinarySensor(
        hass, coordinator, device, product, mapping
    )
    entity.hass = hass
    await connect(coordinator)
    assert entity.available is True
