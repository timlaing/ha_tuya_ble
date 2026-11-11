"""Unit tests for the Tuya BLE text entity."""

from __future__ import annotations

import re
from typing import Any

from homeassistant.components.text import TextEntityDescription
from homeassistant.core import HomeAssistant
import pytest

from custom_components.tuya_ble import text
from custom_components.tuya_ble.device_descriptors.handlers.fingerbot.mode import (
    in_program_mode as is_fingerbot_in_program_mode,
)
from custom_components.tuya_ble.device_descriptors.handlers.fingerbot.program import (
    get_program as get_fingerbot_program,
)
from custom_components.tuya_ble.device_descriptors.handlers.fingerbot.program import (
    set_program as set_fingerbot_program,
)
from custom_components.tuya_ble.device_registry import EntityDescriptor, get_registry
from custom_components.tuya_ble.devices import (
    TuyaBLECoordinator,
    TuyaBLEProductInfo,
)
from custom_components.tuya_ble.text import TuyaBLEText
from custom_components.tuya_ble.tuya_ble import (
    TuyaBLEDataPointType,
    TuyaBLEDevice,
)
from tests.conftest import add_dp, build_context, connect, make_product_info


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
    product = make_product_info(mode=8, switch=2)
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
    product = make_product_info(mode=8, switch=2, program=99)
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
    product = make_product_info(mode=8, switch=2, program=99)
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        setter=set_fingerbot_program,
    )
    entity.set_value("50;100/5")


# ---------------------------------------------------------------------------
# Fingerbot program round-trip (issue #66, P1 #10)
# ---------------------------------------------------------------------------

_FINGERBOT_PROGRAM_PATTERN = (
    r"^([0-9]{1,3}(/[0-9]{1,4})?(;[0-9]{1,3}(/[0-9]{1,4})?)*)?$"
)


def _program_payload(steps: list[tuple[int, int]], repeat: int = 1) -> bytes:
    """Build a fingerbot program payload from (position, delay) pairs."""
    payload = bytearray(repeat.to_bytes(2, "big"))
    payload += b"\x00"
    payload += len(steps).to_bytes(1, "big")
    for position, delay in steps:
        payload += position.to_bytes(1, "big") + delay.to_bytes(2, "big")
    return bytes(payload)


@pytest.mark.parametrize(
    ("steps", "expected"),
    [
        ([], ""),
        ([(50, 0)], "50"),
        ([(50, 5)], "50/5"),
        ([(0, 0), (100, 0)], "0;100"),
        ([(0, 0), (100, 9999)], "0;100/9999"),
        ([(255, 0), (255, 0)], "255;255"),
        ([(1, 0)] * 20, ";".join(["1"] * 20)),
    ],
)
async def test_fingerbot_program_state_is_valid(
    hass: HomeAssistant,
    steps: list[tuple[int, int]],
    expected: str,
) -> None:
    """Every program the formatter can produce must satisfy the descriptor pattern."""
    device, coordinator, product = build_context(hass)
    product = make_product_info(mode=8, switch=2, program=109)
    entity = _make_entity(
        hass, device, coordinator, product, getter=get_fingerbot_program
    )
    add_dp(device, 109, TuyaBLEDataPointType.DT_RAW, _program_payload(steps))

    value = entity.native_value

    assert value == expected
    assert re.fullmatch(_FINGERBOT_PROGRAM_PATTERN, value or "")


async def test_fingerbot_program_length_bounds(
    hass: HomeAssistant,
) -> None:
    """min_length/max_length on the descriptor reach native_min/native_max."""
    mapping = text._build_text_mapping(  # pylint: disable=protected-access
        EntityDescriptor(
            platform="text",
            dp_id=109,
            translation_key="program",
            pattern=_FINGERBOT_PROGRAM_PATTERN,
            min_length=0,
            max_length=255,
        )
    )

    assert mapping.description.native_min == 0
    assert mapping.description.native_max == 255


async def test_fingerbot_descriptors_declare_a_usable_pattern() -> None:
    """Every fingerbot descriptor's pattern accepts a full 20-step program."""
    product_programs = 0
    long_program = ";".join(["100/9999"] * 20)
    for product in get_registry().products.values():
        for descriptor in product.get("text"):
            if descriptor.translation_key != "program":
                continue
            product_programs += 1
            pattern = re.compile(descriptor.pattern or "")
            assert pattern.fullmatch(""), (
                f"{product.product_id}: empty program must be valid"
            )
            assert pattern.fullmatch("50"), (
                f"{product.product_id}: single step must be valid"
            )
            assert pattern.fullmatch("0;50/5;100/9999"), (
                f"{product.product_id}: mixed program must be valid"
            )
            assert pattern.fullmatch(long_program), (
                f"{product.product_id}: 20-step program must be valid"
            )
            assert not pattern.fullmatch("abc"), (
                f"{product.product_id}: garbage must be rejected"
            )
            assert descriptor.max_length is not None
            assert descriptor.max_length >= len(long_program)
    assert product_programs == 10


async def test_set_fingerbot_program_empty_clears_steps(
    hass: HomeAssistant,
) -> None:
    """Setting an empty program must write a zero-step payload, not a parse error."""
    device, coordinator, product = build_context(hass)
    product = make_product_info(mode=8, switch=2, program=109)
    entity = _make_entity(
        hass, device, coordinator, product, setter=set_fingerbot_program
    )
    add_dp(device, 109, TuyaBLEDataPointType.DT_RAW, _program_payload([(50, 5)]))

    entity.set_value("")
    await hass.async_block_till_done()

    program = device.datapoints[109]
    assert program is not None
    assert program.value == b"\x00\x01\x00\x00"


async def test_set_fingerbot_program_round_trip(
    hass: HomeAssistant,
) -> None:
    """Writing a program then reading it back must return the same string."""
    device, coordinator, product = build_context(hass)
    product = make_product_info(mode=8, switch=2, program=109)
    sent: list[bytes] = []

    async def send(dp_ids: list[int]) -> None:
        dp_id = dp_ids[0]
        datapoint = device.datapoints[dp_id]
        assert datapoint is not None
        value = datapoint.value
        assert isinstance(value, bytes)
        sent.append(value)

    device.send_datapoints = send  # type: ignore[method-assign,assignment]
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        getter=get_fingerbot_program,
        setter=set_fingerbot_program,
    )
    add_dp(device, 109, TuyaBLEDataPointType.DT_RAW, _program_payload([(0, 0)]))

    entity.set_value("0;50/5;100/9999")
    await hass.async_block_till_done()

    assert sent == [_program_payload([(0, 0), (50, 5), (100, 9999)])]
    assert entity.native_value == "0;50/5;100/9999"
