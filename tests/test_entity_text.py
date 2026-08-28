"""Unit tests for the Tuya BLE text entity."""

from __future__ import annotations

from typing import Any

from homeassistant.components.text import TextEntityDescription
from homeassistant.core import HomeAssistant

from custom_components.tuya_ble import text
from custom_components.tuya_ble.devices import (
    TuyaBLECoordinator,
    TuyaBLEFingerbotInfo,
    TuyaBLEProductInfo,
)
from custom_components.tuya_ble.fingerbot import (
    get_fingerbot_program,
    is_fingerbot_in_program_mode,
    set_fingerbot_program,
)
from custom_components.tuya_ble.text import TuyaBLEText
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
    **kwargs: Any,
) -> TuyaBLEText:
    """Build a text entity from a mapping built from kwargs."""
    fields: dict[str, Any] = {
        "dp_id": 5,
        "description": TextEntityDescription(key="program"),
    }
    fields.update(kwargs)
    mapping = text.TuyaBLETextMapping(**fields)
    entity = text.TuyaBLEText(hass, coordinator, device, product, mapping)
    entity.hass = hass
    return entity


async def test_native_value_datapoint(hass: HomeAssistant) -> None:
    """Verify native_value reflects a string datapoint."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    add_dp(device, 5, TuyaBLEDataPointType.DT_STRING, "abc")
    assert entity.native_value == "abc"


async def test_native_value_default(hass: HomeAssistant) -> None:
    """Verify native_value falls back to the default value."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product, default_value="fallback")
    assert entity.native_value == "fallback"


async def test_native_value_getter(hass: HomeAssistant) -> None:
    """Verify a custom getter produces native_value."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        getter=lambda self_, product_: "x/y",
    )
    assert entity.native_value == "x/y"


async def test_set_value_datapoint(hass: HomeAssistant) -> None:
    """Verify set_value writes to the datapoint."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    entity.set_value("hello")
    await hass.async_block_till_done()
    dp = device.datapoints[5]
    assert dp is not None
    assert dp.value == "hello"


async def test_set_value_setter(hass: HomeAssistant) -> None:
    """Verify a custom setter receives the value."""
    device, coordinator, product = build_context(hass)
    calls: list[str] = []

    def setter(self_: TuyaBLEText, product_: TuyaBLEProductInfo, value: str) -> None:
        calls.append(value)

    entity = _make_entity(hass, device, coordinator, product, setter=setter)
    entity.set_value("hi")
    assert calls == ["hi"]


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
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        is_available=lambda self_, product_: True,
    )
    await connect(coordinator)
    assert entity.available is True


async def test_fingerbot_in_program_mode_no_fingerbot(
    hass: HomeAssistant,
) -> None:
    """Verify is_fingerbot_in_program_mode returns True when fingerbot is None."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        is_available=is_fingerbot_in_program_mode,
    )
    await connect(coordinator)
    assert entity.available is True


async def test_fingerbot_in_program_mode_no_datapoint(
    hass: HomeAssistant,
) -> None:
    """Verify is_fingerbot_in_program_mode with fingerbot set but no mode dp."""

    device, coordinator, product = build_context(hass)
    product.fingerbot = TuyaBLEFingerbotInfo(
        switch=2,
        mode=8,
        up_position=5,
        down_position=6,
        hold_time=3,
        reverse_positions=0,
    )
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        is_available=is_fingerbot_in_program_mode,
    )
    await connect(coordinator)
    assert entity.available is True


async def test_get_fingerbot_program_no_fingerbot(hass: HomeAssistant) -> None:
    """Verify get_fingerbot_program returns None when fingerbot is None."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        getter=get_fingerbot_program,
    )
    assert entity.native_value is None


async def test_set_fingerbot_program_no_fingerbot(hass: HomeAssistant) -> None:
    """Verify set_fingerbot_program exits early when no fingerbot."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        setter=set_fingerbot_program,
    )
    entity.set_value("50;100/5")


async def test_get_fingerbot_program_no_datapoint(hass: HomeAssistant) -> None:
    """Verify get_fingerbot_program returns None when program dp is absent."""

    device, coordinator, product = build_context(hass)
    product.fingerbot = TuyaBLEFingerbotInfo(
        switch=2,
        mode=8,
        up_position=5,
        down_position=6,
        hold_time=3,
        reverse_positions=0,
        program=99,
    )
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        getter=get_fingerbot_program,
    )
    assert entity.native_value is None


async def test_set_fingerbot_program_no_datapoint(hass: HomeAssistant) -> None:
    """Verify set_fingerbot_program exits early when program dp is absent."""

    device, coordinator, product = build_context(hass)
    product.fingerbot = TuyaBLEFingerbotInfo(
        switch=2,
        mode=8,
        up_position=5,
        down_position=6,
        hold_time=3,
        reverse_positions=0,
        program=99,
    )
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        setter=set_fingerbot_program,
    )
    entity.set_value("50;100/5")
