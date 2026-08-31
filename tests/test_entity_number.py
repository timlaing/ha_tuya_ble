"""Unit tests for the Tuya BLE number entity."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from homeassistant.components.number import NumberEntityDescription, NumberMode
from homeassistant.core import HomeAssistant

from custom_components.tuya_ble import number
from custom_components.tuya_ble.device_descriptors.handlers.fingerbot.mode import (
    in_program_mode as is_fingerbot_in_program_mode,
)
from custom_components.tuya_ble.device_descriptors.handlers.fingerbot.mode import (
    in_push_mode as is_fingerbot_in_push_mode,
)
from custom_components.tuya_ble.device_descriptors.handlers.fingerbot.mode import (
    not_in_program_mode as is_fingerbot_not_in_program_mode,
)
from custom_components.tuya_ble.device_descriptors.handlers.fingerbot.mode import (
    repeat_count_available as is_fingerbot_repeat_count_available,
)
from custom_components.tuya_ble.device_descriptors.handlers.fingerbot.program import (
    get_position as get_fingerbot_program_position,
)
from custom_components.tuya_ble.device_descriptors.handlers.fingerbot.program import (
    get_repeat_count as get_fingerbot_program_repeat_count,
)
from custom_components.tuya_ble.device_descriptors.handlers.fingerbot.program import (
    set_position as set_fingerbot_program_position,
)
from custom_components.tuya_ble.device_descriptors.handlers.fingerbot.program import (
    set_repeat_count as set_fingerbot_program_repeat_count,
)
from custom_components.tuya_ble.devices import (
    TuyaBLECoordinator,
    TuyaBLEFingerbotInfo,
    TuyaBLEProductInfo,
)
from custom_components.tuya_ble.number import TuyaBLENumber
from custom_components.tuya_ble.tuya_ble import (
    TuyaBLEDataPointType,
    TuyaBLEDevice,
)
from tests.conftest import add_dp, build_context, connect

# Short aliases for fingerbot helpers (keeps lines under pylint limit).
_not_in_prog = is_fingerbot_not_in_program_mode
_in_push = is_fingerbot_in_push_mode
_repeat_avail = is_fingerbot_repeat_count_available
_get_repeat = get_fingerbot_program_repeat_count
_set_repeat = set_fingerbot_program_repeat_count
_get_pos = get_fingerbot_program_position
_set_pos = set_fingerbot_program_position


def _make_entity(
    hass: HomeAssistant,
    device: TuyaBLEDevice,
    coordinator: TuyaBLECoordinator,
    product: TuyaBLEProductInfo,
    **kwargs: Any,
) -> TuyaBLENumber:
    """Build a number entity from a mapping built from kwargs."""
    fields: dict[str, Any] = {
        "dp_id": 9,
        "description": NumberEntityDescription(
            key="n", native_min_value=0, native_max_value=100
        ),
        "mode": NumberMode.BOX,
    }
    fields.update(kwargs)
    mapping = number.TuyaBLENumberMapping(**fields)
    entity = number.TuyaBLENumber(hass, coordinator, device, product, mapping)
    entity.hass = hass
    return entity


async def test_native_value_datapoint(hass: HomeAssistant) -> None:
    """Verify native_value reflects a value datapoint."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    add_dp(device, 9, TuyaBLEDataPointType.DT_VALUE, 50)
    assert entity.native_value == 50.0


async def test_native_value_min(hass: HomeAssistant) -> None:
    """Verify native_value defaults to the configured minimum."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    assert entity.native_value == 0.0


async def test_native_value_getter(hass: HomeAssistant) -> None:
    """Verify a custom getter produces native_value."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        getter=lambda self_, product_: 7.5,
    )
    assert entity.native_value == 7.5


async def test_set_native_value(hass: HomeAssistant) -> None:
    """Verify set_native_value writes the raw value to the datapoint."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    entity.set_native_value(25.0)
    await hass.async_block_till_done()
    dp = device.datapoints[9]
    assert dp is not None
    assert dp.value == 25


async def test_set_native_value_setter(hass: HomeAssistant) -> None:
    """Verify a custom setter receives the value."""
    device, coordinator, product = build_context(hass)
    calls: list[float] = []

    def setter(
        self_: TuyaBLENumber, product_: TuyaBLEProductInfo, value: float
    ) -> None:
        calls.append(value)

    entity = _make_entity(hass, device, coordinator, product, setter=setter)
    entity.set_native_value(10.0)
    assert calls == [10.0]


async def test_set_native_value_coefficient(hass: HomeAssistant) -> None:
    """Verify set_native_value applies the coefficient."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product, coefficient=10.0)
    entity.set_native_value(2.5)
    await hass.async_block_till_done()
    dp = device.datapoints[9]
    assert dp is not None
    assert dp.value == 25


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
        program=99,
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


async def test_fingerbot_not_in_program_mode_no_fingerbot(
    hass: HomeAssistant,
) -> None:
    """Verify is_fingerbot_not_in_program_mode returns True when no fingerbot."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        is_available=is_fingerbot_not_in_program_mode,
    )
    await connect(coordinator)
    assert entity.available is True


async def test_fingerbot_in_push_mode_no_fingerbot(
    hass: HomeAssistant,
) -> None:
    """Verify is_fingerbot_in_push_mode returns True when fingerbot is None."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        is_available=is_fingerbot_in_push_mode,
    )
    await connect(coordinator)
    assert entity.available is True


async def test_fingerbot_repeat_count_available_no_fingerbot(
    hass: HomeAssistant,
) -> None:
    """Verify is_fingerbot_repeat_count_available returns True when no fingerbot."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        is_available=is_fingerbot_repeat_count_available,
    )
    await connect(coordinator)
    assert entity.available is True


async def test_get_fingerbot_program_repeat_count_no_fingerbot(
    hass: HomeAssistant,
) -> None:
    """Verify get_fingerbot_program_repeat_count returns None with no fingerbot."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        getter=get_fingerbot_program_repeat_count,
    )
    assert entity.native_value is None


async def test_get_fingerbot_program_position_no_fingerbot(
    hass: HomeAssistant,
) -> None:
    """Verify get_fingerbot_program_position returns None with no fingerbot."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        getter=get_fingerbot_program_position,
    )
    assert entity.native_value is None


async def test_set_fingerbot_program_repeat_count_no_fingerbot(
    hass: HomeAssistant,
) -> None:
    """Verify set_fingerbot_program_repeat_count exits early with no fingerbot."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        setter=set_fingerbot_program_repeat_count,
    )
    entity.set_native_value(5)


async def test_set_fingerbot_program_position_no_fingerbot(
    hass: HomeAssistant,
) -> None:
    """Verify set_fingerbot_program_position exits early with no fingerbot."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        setter=set_fingerbot_program_position,
    )
    entity.set_native_value(50)


# ---------------------------------------------------------------------------
# Pure unit tests for fingerbot helper functions (no HA loop required)
# ---------------------------------------------------------------------------


class _FakeDatapointDict(dict[int, Any]):
    """Dict that returns None for missing keys (matches real TuyaBLEDataPoints)."""

    def __missing__(self, key: int) -> None:
        return None


def _make_dp(dp_id: int, value: Any) -> SimpleNamespace:
    """Build a fake datapoint."""
    return SimpleNamespace(dp_id=dp_id, value=value, set_value=lambda v: None)


def _make_number(dp_dict: dict[int, Any]) -> Any:
    """Build a fake number entity with the given datapoints."""
    return SimpleNamespace(
        device=SimpleNamespace(datapoints=_FakeDatapointDict(dp_dict)),
        hass=SimpleNamespace(create_task=lambda coro: None),
    )


def _make_product(
    mode_dp: int | None = None,
    program_dp: int | None = None,
) -> Any:
    """Build a fake product info with optional fingerbot."""
    fingerbot = None
    if mode_dp is not None or program_dp is not None:
        fingerbot = SimpleNamespace(mode=mode_dp, program=program_dp)
    return SimpleNamespace(fingerbot=fingerbot)


def test_no_fingerbot_returns_true() -> None:
    """Return True when product has no fingerbot."""
    ent = _make_number({})
    product = _make_product()
    assert _not_in_prog(ent, product) is True


def test_fingerbot_no_mode_dp_returns_true() -> None:
    """Return True when fingerbot mode datapoint is missing."""
    ent = _make_number({})
    product = _make_product(mode_dp=1)
    assert _not_in_prog(ent, product) is True


def test_fingerbot_mode_not_program() -> None:
    """Return True when mode is not program (value != 2)."""
    ent = _make_number({1: _make_dp(1, 0)})
    product = _make_product(mode_dp=1)
    assert _not_in_prog(ent, product) is True


def test_fingerbot_in_program_mode() -> None:
    """Return False when mode is program (value == 2)."""
    ent = _make_number({1: _make_dp(1, 2)})
    product = _make_product(mode_dp=1)
    assert _not_in_prog(ent, product) is False


def test_in_push_no_fingerbot_returns_true() -> None:
    """Return True when product has no fingerbot."""
    ent = _make_number({})
    product = _make_product()
    assert _in_push(ent, product) is True


def test_in_push_fingerbot_no_mode_dp_returns_true() -> None:
    """Return True when fingerbot mode datapoint is missing."""
    ent = _make_number({})
    product = _make_product(mode_dp=1)
    assert _in_push(ent, product) is True


def test_fingerbot_mode_is_push() -> None:
    """Return True when mode is push (value == 0)."""
    ent = _make_number({1: _make_dp(1, 0)})
    product = _make_product(mode_dp=1)
    assert _in_push(ent, product) is True


def test_fingerbot_mode_not_push() -> None:
    """Return False when mode is not push (value != 0)."""
    ent = _make_number({1: _make_dp(1, 2)})
    product = _make_product(mode_dp=1)
    assert _in_push(ent, product) is False


def test_repeat_avail_no_fingerbot_returns_true() -> None:
    """Return True when product has no fingerbot."""
    ent = _make_number({})
    product = _make_product()
    assert _repeat_avail(ent, product) is True


def test_no_program_dp() -> None:
    """Return True when program datapoint is missing."""
    ent = _make_number({1: _make_dp(1, 2)})
    product = _make_product(mode_dp=1, program_dp=2)
    assert _repeat_avail(ent, product) is True


def test_mode_not_program() -> None:
    """Return False when mode is not program."""
    ent = _make_number({1: _make_dp(1, 0)})
    product = _make_product(mode_dp=1, program_dp=2)
    assert _repeat_avail(ent, product) is False


def test_program_dp_not_bytes() -> None:
    """Return True when program datapoint is not bytes."""
    ent = _make_number(
        {1: _make_dp(1, 2), 2: _make_dp(2, 42)},
    )
    product = _make_product(mode_dp=1, program_dp=2)
    assert _repeat_avail(ent, product) is True


def test_program_dp_repeat_forever() -> None:
    """Return False when repeat count is 0xFFFF."""
    program_bytes = b"\xff\xff\x50"
    ent = _make_number(
        {1: _make_dp(1, 2), 2: _make_dp(2, program_bytes)},
    )
    product = _make_product(mode_dp=1, program_dp=2)
    assert _repeat_avail(ent, product) is False


def test_program_dp_has_repeat_count() -> None:
    """Return True when program has a finite repeat count."""
    program_bytes = b"\x00\x03\x50"
    ent = _make_number(
        {1: _make_dp(1, 2), 2: _make_dp(2, program_bytes)},
    )
    product = _make_product(mode_dp=1, program_dp=2)
    assert _repeat_avail(ent, product) is True


def test_no_fingerbot_returns_none() -> None:
    """Return None when product has no fingerbot."""
    ent = _make_number({})
    product = _make_product()
    assert _get_repeat(ent, product) is None


def test_program_dp_not_bytes_returns_none() -> None:
    """Return None when program datapoint is not bytes."""
    ent = _make_number({2: _make_dp(2, 42)})
    product = _make_product(program_dp=2)
    assert _get_repeat(ent, product) is None


def test_program_dp_bytes_returns_count() -> None:
    """Return repeat count from program bytes."""
    program_bytes = b"\x00\x05\x50"
    ent = _make_number({2: _make_dp(2, program_bytes)})
    product = _make_product(program_dp=2)
    assert _get_repeat(ent, product) == 5.0


def test_no_fingerbot_noop() -> None:
    """No-op when product has no fingerbot."""
    ent = _make_number({})
    product = _make_product()
    _set_repeat(ent, product, 3.0)


def test_program_dp_not_bytes_noop() -> None:
    """No-op when program datapoint is not bytes."""
    ent = _make_number({2: _make_dp(2, 42)})
    product = _make_product(program_dp=2)
    _set_repeat(ent, product, 3.0)


def test_program_dp_bytes_sets_count() -> None:
    """Set repeat count in program bytes."""
    program_bytes = b"\x00\x05\x50"
    ent = _make_number({2: _make_dp(2, program_bytes)})
    product = _make_product(program_dp=2)
    _set_repeat(ent, product, 10.0)


def test_get_position_no_fingerbot_returns_none() -> None:
    """Return None when product has no fingerbot."""
    ent = _make_number({})
    product = _make_product()
    assert _get_pos(ent, product) is None


def test_get_position_program_not_bytes_returns_none() -> None:
    """Return None when program datapoint is not bytes."""
    ent = _make_number({2: _make_dp(2, 42)})
    product = _make_product(program_dp=2)
    assert _get_pos(ent, product) is None


def test_program_dp_bytes_returns_position() -> None:
    """Return position from program bytes."""
    program_bytes = b"\x00\x05\x32"
    ent = _make_number({2: _make_dp(2, program_bytes)})
    product = _make_product(program_dp=2)
    assert _get_pos(ent, product) == 50.0


def test_set_position_no_fingerbot_noop() -> None:
    """No-op when product has no fingerbot."""
    ent = _make_number({})
    product = _make_product()
    _set_pos(ent, product, 50.0)


def test_set_position_program_not_bytes_noop() -> None:
    """No-op when program datapoint is not bytes."""
    ent = _make_number({2: _make_dp(2, 42)})
    product = _make_product(program_dp=2)
    _set_pos(ent, product, 50.0)


def test_program_dp_bytes_sets_position() -> None:
    """Set position in program bytes."""
    program_bytes = b"\x00\x05\x32"
    ent = _make_number({2: _make_dp(2, program_bytes)})
    product = _make_product(program_dp=2)
    _set_pos(ent, product, 80.0)
