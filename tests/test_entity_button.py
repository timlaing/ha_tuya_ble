"""Unit tests for the Tuya BLE button entity."""

from __future__ import annotations

import asyncio
from typing import cast
from unittest.mock import patch

from bleak.exc import BleakError
from homeassistant.components.button import ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.tuya_ble import button
from custom_components.tuya_ble.const import DOMAIN
from custom_components.tuya_ble.device_descriptors.handlers.fingerbot.mode import (
    in_push_mode,
)
from custom_components.tuya_ble.devices import (
    TuyaBLECoordinator,
    TuyaBLEProductInfo,
)
from custom_components.tuya_ble.tuya_ble import (
    TuyaBLEDataPointType,
    TuyaBLEDevice,
)
from custom_components.tuya_ble.tuya_ble.exceptions import (
    TuyaBLEDeviceError,
    TuyaBLEResponseTimeoutError,
)
from tests.conftest import (
    EntityDevice,
    add_dp,
    build_context,
    connect,
    make_product_info,
)


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
    await entity.async_press()
    dp = device.datapoints[2]
    assert dp is not None
    assert dp.value is True
    assert cast(EntityDevice, device).sent == [[2]]


async def test_press_waits_for_the_write(hass: HomeAssistant) -> None:
    """The press stays pending until the BLE write has finished."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    started = asyncio.Event()
    finish = asyncio.Event()

    async def send(_dp_ids: list[int]) -> None:
        started.set()
        await finish.wait()

    with patch.object(device, "send_datapoints", side_effect=send):
        press = asyncio.create_task(entity.async_press())
        await started.wait()
        assert not press.done()
        finish.set()
        await press


@pytest.mark.parametrize(
    "error",
    [
        BleakError("write failed"),
        TuyaBLEResponseTimeoutError("FUN_SENDER_DPS"),
        TuyaBLEDeviceError(1),
        TimeoutError(),
    ],
    ids=["write", "timeout", "rejected", "connect-timeout"],
)
async def test_press_failure_raises_home_assistant_error(
    hass: HomeAssistant, error: Exception
) -> None:
    """A failed or unacknowledged press is reported and rolled back."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    add_dp(device, 2, TuyaBLEDataPointType.DT_BOOL, False)
    with (
        patch.object(device, "send_datapoints", side_effect=error),
        pytest.raises(HomeAssistantError) as raised,
    ):
        await entity.async_press()
    assert raised.value.translation_domain == DOMAIN
    assert raised.value.translation_key == "button_press_failed"
    assert raised.value.__cause__ is error
    dp = device.datapoints[2]
    assert dp is not None
    assert dp.value is False


@pytest.mark.parametrize("press_value", [True, False])
async def test_fixed_press_value_failure_raises_and_rolls_back(
    hass: HomeAssistant, press_value: bool
) -> None:
    """A fixed-value press is awaited too, and its failure is reported."""
    device, coordinator, product = build_context(hass)
    mapping = button.TuyaBLEButtonMapping(
        dp_id=2, description=_btn_desc(), press_value=press_value
    )
    entity = button.TuyaBLEButton(hass, coordinator, device, product, mapping)
    entity.hass = hass
    add_dp(device, 2, TuyaBLEDataPointType.DT_BOOL, not press_value)
    with (
        patch.object(device, "send_datapoints", side_effect=BleakError("failed")),
        pytest.raises(HomeAssistantError),
    ):
        await entity.async_press()
    dp = device.datapoints[2]
    assert dp is not None
    assert dp.value is (not press_value)
    await entity.async_press()
    assert dp.value is press_value
    assert cast(EntityDevice, device).sent == [[2]]


async def test_press_cancellation_propagates(hass: HomeAssistant) -> None:
    """Cancelling a press is not reported as a device failure."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    add_dp(device, 2, TuyaBLEDataPointType.DT_BOOL, False)
    started = asyncio.Event()

    async def send(_dp_ids: list[int]) -> None:
        started.set()
        await asyncio.Event().wait()

    with patch.object(device, "send_datapoints", side_effect=send):
        press = asyncio.create_task(entity.async_press())
        await started.wait()
        press.cancel()
        with pytest.raises(asyncio.CancelledError):
            await press
    dp = device.datapoints[2]
    assert dp is not None
    assert dp.value is False


async def test_rapid_presses_each_send_their_own_toggle(hass: HomeAssistant) -> None:
    """A press made while another is in flight sends the next toggle."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    add_dp(device, 2, TuyaBLEDataPointType.DT_BOOL, False)
    first_started = asyncio.Event()
    finish_first = asyncio.Event()
    sent: list[object] = []

    async def send(_dp_ids: list[int]) -> None:
        dp = device.datapoints[2]
        assert dp is not None
        sent.append(dp.value)
        if len(sent) == 1:
            first_started.set()
            await finish_first.wait()

    with patch.object(device, "send_datapoints", side_effect=send):
        first = asyncio.create_task(entity.async_press())
        await first_started.wait()
        second = asyncio.create_task(entity.async_press())
        await asyncio.sleep(0)
        finish_first.set()
        await asyncio.gather(first, second)
    assert sent == [True, False]


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
    product = make_product_info(mode=8, switch=2)
    mapping = button.TuyaBLEButtonMapping(
        dp_id=2,
        description=_btn_desc(),
        is_available=in_push_mode,
    )
    entity = button.TuyaBLEButton(hass, coordinator, device, product, mapping)
    entity.hass = hass
    await connect(coordinator)
    assert entity.available is True
