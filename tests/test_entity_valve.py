"""Unit tests for the Tuya BLE valve entity."""
# pylint: disable=unexpected-keyword-arg

from __future__ import annotations

from homeassistant.components.valve.entity import ValveEntityDescription
from homeassistant.core import HomeAssistant

from custom_components.tuya_ble import valve
from custom_components.tuya_ble.devices import (
    TuyaBLECoordinator,
    TuyaBLEProductInfo,
)
from custom_components.tuya_ble.tuya_ble import (
    TuyaBLEDataPointType,
    TuyaBLEDevice,
)
from custom_components.tuya_ble.valve import TuyaBLEValve
from tests.conftest import add_dp, build_context, connect


def _make_entity(
    hass: HomeAssistant,
    device: TuyaBLEDevice,
    coordinator: TuyaBLECoordinator,
    product: TuyaBLEProductInfo,
) -> TuyaBLEValve:
    """Build a valve entity."""
    mapping = valve.TuyaBLEValveMapping(
        dp_id=1,
        description=ValveEntityDescription(key="valve"),
    )
    entity = valve.TuyaBLEValve(hass, coordinator, device, product, mapping)
    entity.hass = hass
    return entity


async def test_is_closed(hass: HomeAssistant) -> None:
    """Verify is_closed is the inverse of the bool datapoint."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    add_dp(device, 1, TuyaBLEDataPointType.DT_BOOL, True)
    assert entity.is_closed is False
    add_dp(device, 1, TuyaBLEDataPointType.DT_BOOL, False)
    assert entity.is_closed is True


async def test_is_closed_none(hass: HomeAssistant) -> None:
    """Verify is_closed is None without a datapoint."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    assert entity.is_closed is None


async def test_is_closed_getter(hass: HomeAssistant) -> None:
    """Verify a custom getter controls is_closed."""
    device, coordinator, product = build_context(hass)
    entity = valve.TuyaBLEValve(
        hass,
        coordinator,
        device,
        product,
        valve.TuyaBLEValveMapping(
            dp_id=1,
            description=ValveEntityDescription(key="v"),
            getter=lambda self_, product_: True,
        ),
    )
    assert entity.is_closed is True


async def test_open_close(hass: HomeAssistant) -> None:
    """Verify open_valve and close_valve write the bool datapoint."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    entity.hass = hass
    entity.open_valve()
    await hass.async_block_till_done()
    dp = device.datapoints[1]
    assert dp is not None
    assert dp.value is True
    entity.close_valve()
    await hass.async_block_till_done()
    dp = device.datapoints[1]
    assert dp is not None
    assert dp.value is False


async def test_stop_valve(hass: HomeAssistant) -> None:
    """Verify stop_valve closes the valve."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    entity.hass = hass
    add_dp(device, 1, TuyaBLEDataPointType.DT_BOOL, True)
    entity.stop_valve()
    await hass.async_block_till_done()
    dp = device.datapoints[1]
    assert dp is not None
    assert dp.value is False


async def test_open_setter(hass: HomeAssistant) -> None:
    """Verify a custom setter receives the open/close values."""
    device, coordinator, product = build_context(hass)
    calls: list[bool] = []

    def setter(self_: TuyaBLEValve, product_: TuyaBLEProductInfo, value: bool) -> None:
        calls.append(value)

    entity = valve.TuyaBLEValve(
        hass,
        coordinator,
        device,
        product,
        valve.TuyaBLEValveMapping(
            dp_id=1,
            description=ValveEntityDescription(key="v"),
            setter=setter,
        ),
    )
    entity.open_valve()
    entity.close_valve()
    assert calls == [True, False]


async def test_set_valve_position(hass: HomeAssistant) -> None:
    """Verify set_valve_position is a no-op on this entity."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    entity.set_valve_position(50)


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
    mapping = valve.TuyaBLEValveMapping(
        dp_id=1,
        description=ValveEntityDescription(key="valve"),
        is_available=lambda self_, product_: True,
    )
    entity = valve.TuyaBLEValve(hass, coordinator, device, product, mapping)
    entity.hass = hass
    await connect(coordinator)
    assert entity.available is True
