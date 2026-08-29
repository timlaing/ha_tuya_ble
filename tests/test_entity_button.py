"""Unit tests for the Tuya BLE button entity."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntityDescription
from homeassistant.core import HomeAssistant

from custom_components.tuya_ble import button
from custom_components.tuya_ble.device_descriptors.handlers.fingerbot.mode import (
    in_push_mode,
)
from custom_components.tuya_ble.devices import (
    TuyaBLECoordinator,
    TuyaBLEFingerbotInfo,
    TuyaBLEProductInfo,
)
from custom_components.tuya_ble.tuya_ble import (
    TuyaBLEDataPointType,
    TuyaBLEDevice,
)
from tests.conftest import add_dp, build_context, connect


def _btn_desc() -> ButtonEntityDescription:
    """Return a default button entity description."""
    return ButtonEntityDescription(key="push")


def _make_entity(
    hass: HomeAssistant,
    device: TuyaBLEDevice,
    coordinator: TuyaBLECoordinator,
    product: TuyaBLEProductInfo,
) -> button.TuyaBLEButton:
    """Build a button entity."""
    mapping = button.TuyaBLEButtonMapping(
        dp_id=2,
        description=_btn_desc(),
    )
    entity = button.TuyaBLEButton(hass, coordinator, device, product, mapping)
    entity.hass = hass
    return entity


async def test_press(hass: HomeAssistant) -> None:
    """Verify pressing the button sets the datapoint to True."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    add_dp(device, 2, TuyaBLEDataPointType.DT_BOOL, False)
    entity.press()
    await hass.async_block_till_done()
    dp = device.datapoints[2]
    assert dp is not None
    assert dp.value is True


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
    mapping = button.TuyaBLEButtonMapping(
        dp_id=2,
        description=_btn_desc(),
        is_available=lambda self_, product_: True,
    )
    entity = button.TuyaBLEButton(hass, coordinator, device, product, mapping)
    entity.hass = hass
    await connect(coordinator)
    assert entity.available is True


async def test_fingerbot_in_push_mode_no_fingerbot(hass: HomeAssistant) -> None:
    """Verify is_fingerbot_in_push_mode returns True when fingerbot is None."""
    device, coordinator, product = build_context(hass)
    mapping = button.TuyaBLEButtonMapping(
        dp_id=2,
        description=_btn_desc(),
        is_available=in_push_mode,
    )
    entity = button.TuyaBLEButton(hass, coordinator, device, product, mapping)
    entity.hass = hass
    await connect(coordinator)
    assert entity.available is True


async def test_fingerbot_in_push_mode_no_datapoint(hass: HomeAssistant) -> None:
    """Verify is_fingerbot_in_push_mode with fingerbot set but no mode dp."""

    device, coordinator, product = build_context(hass)
    product.fingerbot = TuyaBLEFingerbotInfo(
        switch=2,
        mode=8,
        up_position=5,
        down_position=6,
        hold_time=3,
        reverse_positions=0,
    )
    mapping = button.TuyaBLEButtonMapping(
        dp_id=2,
        description=_btn_desc(),
        is_available=in_push_mode,
    )
    entity = button.TuyaBLEButton(hass, coordinator, device, product, mapping)
    entity.hass = hass
    await connect(coordinator)
    assert entity.available is True
