"""Unit tests for the YAML device-descriptor handlers package."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.tuya_ble.device_descriptors import handlers
from custom_components.tuya_ble.device_descriptors.handlers import (
    battery,
    co2,
    rssi,
    water_valve,
)
from custom_components.tuya_ble.device_descriptors.handlers.fingerbot import (
    mode as fingerbot_mode,
)
from custom_components.tuya_ble.device_descriptors.handlers.fingerbot import (
    program as fingerbot_program,
)
from custom_components.tuya_ble.products import (
    TuyaBLEFingerbotInfo,
    TuyaBLEProductInfo,
    TuyaBLEWaterValveInfo,
)

pytestmark = pytest.mark.filterwarnings("ignore::RuntimeWarning")


def make_fake_owner(datapoints: dict[int, Any], signal: int = -55) -> Any:
    """Build a fake entity/device owner with the given datapoints."""
    called: list[object] = []

    def _create_task(coro: Any) -> None:
        called.append(coro)
        coro.close()

    hass = SimpleNamespace(task_calls=called, create_task=_create_task)
    owner = SimpleNamespace(
        hass=hass,
        rssi=signal,
        set_native_value_calls=[],
        task_calls=called,
    )

    class _Datapoints(dict[int, Any]):
        def __getitem__(self, key: int) -> Any:
            return dict.get(self, key)

    owner.datapoints = _Datapoints(datapoints)
    owner.device = owner

    def _set_native_value(value: object) -> None:
        owner.set_native_value_calls.append(value)

    owner.set_native_value = _set_native_value
    return owner


def make_datapoint(value: Any) -> Any:
    """Build a fake datapoint with a recorded setter."""
    call = {"value": value, "set_calls": []}

    async def _set_value(new_value: object) -> None:
        call["set_calls"].append(new_value)
        call["value"] = new_value

    dp = SimpleNamespace(value=value, set_value=_set_value)
    dp.value = value
    return dp


def make_fingerbot_product() -> TuyaBLEProductInfo:
    """Build product info with a fingerbot descriptor."""
    return TuyaBLEProductInfo(
        name="Fingerbot",
        fingerbot=TuyaBLEFingerbotInfo(
            switch=1,
            mode=2,
            up_position=3,
            down_position=4,
            hold_time=5,
            reverse_positions=6,
            program=7,
        ),
    )


def _fb(product: TuyaBLEProductInfo) -> TuyaBLEFingerbotInfo:
    """Return the fingerbot descriptor, asserting it is present."""
    fb = product.fingerbot
    assert fb is not None
    return fb


def test_resolve_handler_dotted_path() -> None:
    """resolve_handler returns a callable for a dotted path."""
    fn = handlers.resolve_handler("fingerbot.program.get_position")
    assert callable(fn)
    assert fn is fingerbot_program.get_position


def test_resolve_handler_bad_path_raises() -> None:
    """resolve_handler rejects paths without a module component."""
    with pytest.raises(ValueError):
        handlers.resolve_handler("no_module_here")


def test_resolve_handler_non_callable_raises() -> None:
    """resolve_handler rejects resolved attributes that are not callable."""
    with pytest.raises(TypeError):
        handlers.resolve_handler("co2._CO2_ALARM_DP_ID")


def test_resolve_handler_module_attribute_exists() -> None:
    """resolve_handler surfaces unknown attribute errors as AttributeError."""
    with pytest.raises(AttributeError):
        handlers.resolve_handler("battery.does_not_exist")


def test_battery_enum_sets_percentage() -> None:
    """battery_enum converts the enum datapoint to a percentage."""
    owner = make_fake_owner({104: make_datapoint(4)})
    battery.battery_enum(owner)
    assert owner.set_native_value_calls == [80.0]


def test_battery_enum_ignores_non_int() -> None:
    """battery_enum ignores a non-integer datapoint value."""
    owner = make_fake_owner({104: make_datapoint("high")})
    battery.battery_enum(owner)
    assert owner.set_native_value_calls == []


def test_battery_enum_missing_datapoint() -> None:
    """battery_enum handles a missing datapoint gracefully."""
    owner = make_fake_owner({})
    battery.battery_enum(owner)
    assert owner.set_native_value_calls == []


def test_co2_alarm_enabled_default_true() -> None:
    """co2.alarm_enabled defaults to True when the datapoint is absent."""
    owner = make_fake_owner({})
    product = TuyaBLEProductInfo(name="CO2")
    assert co2.alarm_enabled(owner, product)


def test_co2_alarm_enabled_true_values() -> None:
    """co2.alarm_enabled returns True for truthy values."""
    owner = make_fake_owner({13: make_datapoint(1)})
    product = TuyaBLEProductInfo(name="CO2")
    assert co2.alarm_enabled(owner, product)


def test_co2_alarm_enabled_false() -> None:
    """co2.alarm_enabled returns False for a falsy datapoint."""
    owner = make_fake_owner({13: make_datapoint(0)})
    product = TuyaBLEProductInfo(name="CO2")
    assert not co2.alarm_enabled(owner, product)


def test_rssi_sensor_sets_signal() -> None:
    """rssi sets the native value from the device rssi."""
    owner = make_fake_owner({}, signal=-70)
    rssi.rssi(owner)
    assert owner.set_native_value_calls == [-70]


def test_water_valve_in_switch_mode_true() -> None:
    """is_water_valve_in_switch_mode is True when product is a water valve."""
    entity: Any = SimpleNamespace()
    product = TuyaBLEProductInfo(
        name="Valve",
        watervalve=TuyaBLEWaterValveInfo(
            switch=1, countdown=2, weather_delay=3, smart_weather=4, use_time=5
        ),
    )
    assert water_valve.is_water_valve_in_switch_mode(entity, product)


def test_water_valve_in_switch_mode_false() -> None:
    """is_water_valve_in_switch_mode is False for non-water-valve products."""
    entity: Any = SimpleNamespace()
    product = TuyaBLEProductInfo(name="Switch")
    assert not water_valve.is_water_valve_in_switch_mode(entity, product)


def test_water_valve_set_on() -> None:
    """set_16wgjvck_water_valve sends the expected datapoints when enabled."""
    switch: Any = SimpleNamespace(sent=[])

    def _send(dp_values: list[tuple[int, object, object]]) -> None:
        switch.sent.append(dp_values)

    def _send_one(dp_id: int, dp_type: object, value: object) -> None:
        switch.sent.append([(dp_id, dp_type, value)])

    switch.send_multiple_dp_values = _send
    switch.send_dp_value = _send_one
    switch.device = SimpleNamespace(
        datapoints={
            15: make_datapoint(45),
            11: make_datapoint(20),
            2: make_datapoint(80),
        }
    )
    product = TuyaBLEProductInfo(name="Valve")
    water_valve.set_16wgjvck_water_valve(switch, product, True)
    assert switch.sent[0][0][0] == 1
    assert switch.sent[0][0][2] is True
    assert switch.sent[0][1][2] == 80
    assert switch.sent[0][2][2] == 45


def test_water_valve_set_off() -> None:
    """set_16wgjvck_water_valve sends only dp 1 off when disabled."""
    switch: Any = SimpleNamespace(sent=[])

    def _send_one(dp_id: int, dp_type: object, value: object) -> None:
        switch.sent.append([(dp_id, dp_type, value)])

    switch.send_dp_value = _send_one
    switch.device = SimpleNamespace(datapoints={})
    product = TuyaBLEProductInfo(name="Valve")
    water_valve.set_16wgjvck_water_valve(switch, product, False)
    assert len(switch.sent) == 1
    assert switch.sent[0][0][0] == 1
    assert switch.sent[0][0][2] is False


def _valve_switch_with(datapoints: dict[int, Any]) -> Any:
    """Build a water-valve switch fake wired to the given datapoints."""
    switch: Any = SimpleNamespace(sent=[])

    def _send(dp_values: list[tuple[int, object, object]]) -> None:
        switch.sent.append(dp_values)

    class _Datapoints(dict[int, Any]):
        def __getitem__(self, key: int) -> Any:
            return dict.get(self, key)

    switch.send_multiple_dp_values = _send
    switch.device = SimpleNamespace(datapoints=_Datapoints(datapoints))
    return switch


def _valve_switch_with_missing(dp_ids: list[int]) -> Any:
    """Build a water-valve switch whose listed datapoints are all None."""
    datapoints: dict[int, Any] = {i: None for i in dp_ids}
    return _valve_switch_with(datapoints)


def test_water_valve_set_on_uses_defaults_when_missing() -> None:
    """Water valve falls back to defaults when datapoints are absent."""
    product = TuyaBLEProductInfo(name="Valve")
    switch = _valve_switch_with({15: None, 11: None, 2: None})
    water_valve.set_16wgjvck_water_valve(switch, product, True)
    assert switch.sent[0][1][2] == 100
    assert switch.sent[0][2][2] == 60


def test_water_valve_set_on_uses_dp11_when_dp15_unset() -> None:
    """Water valve reads dp 11 when dp 15 is unset."""
    product = TuyaBLEProductInfo(name="Valve")
    switch = _valve_switch_with({
        15: None,
        11: make_datapoint(35),
        2: make_datapoint(90),
    })
    water_valve.set_16wgjvck_water_valve(switch, product, True)
    assert switch.sent[0][1][2] == 90
    assert switch.sent[0][2][2] == 35


def test_water_valve_set_on_zeroes_raised() -> None:
    """Water valve clamps negative dp 11 / dp 2 values to defaults."""
    product = TuyaBLEProductInfo(name="Valve")
    switch = _valve_switch_with({15: make_datapoint(-5), 2: make_datapoint(-5)})
    water_valve.set_16wgjvck_water_valve(switch, product, True)
    assert switch.sent[0][1][2] == 100
    assert switch.sent[0][2][2] == 60


def test_water_valve_set_on_unset_dp11_value() -> None:
    """Water valve uses the default when dp 11 exists with a falsy value."""
    product = TuyaBLEProductInfo(name="Valve")
    switch = _valve_switch_with({
        15: None,
        11: make_datapoint(0),
        2: make_datapoint(50),
    })
    water_valve.set_16wgjvck_water_valve(switch, product, True)
    assert switch.sent[0][2][2] == 60


# ---- fingerbot mode handlers ----


def test_in_program_mode_true() -> None:
    """in_program_mode is True when the mode datapoint equals 2."""
    product = make_fingerbot_product()
    owner = make_fake_owner({_fb(product).mode: make_datapoint(2)})
    assert fingerbot_mode.in_program_mode(owner, product)


def test_in_program_mode_false_no_datapoint() -> None:
    """in_program_mode defaults True when the datapoint is absent."""
    product = make_fingerbot_product()
    owner = make_fake_owner({})
    assert fingerbot_mode.in_program_mode(owner, product)


def test_not_in_program_mode_true() -> None:
    """not_in_program_mode is True when the mode is not 2."""
    product = make_fingerbot_product()
    owner = make_fake_owner({_fb(product).mode: make_datapoint(1)})
    assert fingerbot_mode.not_in_program_mode(owner, product)


def test_in_switch_mode_true() -> None:
    """in_switch_mode is True when the mode datapoint equals 1."""
    product = make_fingerbot_product()
    owner = make_fake_owner({_fb(product).mode: make_datapoint(1)})
    assert fingerbot_mode.in_switch_mode(owner, product)


def test_in_push_mode_true() -> None:
    """in_push_mode is True when the mode datapoint equals 0."""
    product = make_fingerbot_product()
    owner = make_fake_owner({_fb(product).mode: make_datapoint(0)})
    assert fingerbot_mode.in_push_mode(owner, product)


def test_mode_handlers_default_true_without_fingerbot() -> None:
    """Mode handlers default True for non-fingerbot products."""
    product = TuyaBLEProductInfo(name="Plain")
    owner = make_fake_owner({})
    assert fingerbot_mode.in_program_mode(owner, product)
    assert fingerbot_mode.in_switch_mode(owner, product)
    assert fingerbot_mode.in_push_mode(owner, product)
    assert fingerbot_mode.not_in_program_mode(owner, product)


def test_mode_handlers_default_true_missing_mode_datapoint() -> None:
    """Mode handlers default True when the mode datapoint is missing."""
    product = make_fingerbot_product()
    owner = make_fake_owner({})
    assert fingerbot_mode.in_program_mode(owner, product)
    assert fingerbot_mode.in_switch_mode(owner, product)
    assert fingerbot_mode.in_push_mode(owner, product)
    assert fingerbot_mode.not_in_program_mode(owner, product)


def test_repeat_count_available_finite() -> None:
    """repeat_count_available is True for a finite repeat count."""
    product = make_fingerbot_product()
    owner = make_fake_owner({
        _fb(product).mode: make_datapoint(2),
        _fb(product).program: make_datapoint(b"\x00\x0a" + b"\x00\x00\x00"),
    })
    assert fingerbot_mode.repeat_count_available(owner, product)


def test_repeat_count_available_forever() -> None:
    """repeat_count_available is False for the 0xFFFF repeat marker."""
    product = make_fingerbot_product()
    owner = make_fake_owner({
        _fb(product).mode: make_datapoint(2),
        _fb(product).program: make_datapoint(b"\xff\xff" + b"\x00\x00\x00"),
    })
    assert not fingerbot_mode.repeat_count_available(owner, product)


def test_repeat_count_available_defaults() -> None:
    """repeat_count_available defaults True without mode/program datapoints."""
    product = make_fingerbot_product()
    owner = make_fake_owner({})
    assert fingerbot_mode.repeat_count_available(owner, product)


def test_repeat_count_available_non_fingerbot() -> None:
    """repeat_count_available defaults True for non-fingerbot products."""
    product = TuyaBLEProductInfo(name="Plain")
    owner = make_fake_owner({})
    assert fingerbot_mode.repeat_count_available(owner, product)


def test_repeat_count_available_not_in_program_mode() -> None:
    """repeat_count_available is False when the mode is not program mode."""
    product = make_fingerbot_product()
    owner = make_fake_owner({_fb(product).mode: make_datapoint(1)})
    assert not fingerbot_mode.repeat_count_available(owner, product)


# ---- fingerbot program handlers ----


def test_get_repeat_count() -> None:
    """get_repeat_count returns the repeat count as a float."""
    product = make_fingerbot_product()
    owner = make_fake_owner({
        _fb(product).program: make_datapoint(b"\x00\x05\x00\x00\x00")
    })
    assert fingerbot_program.get_repeat_count(owner, product) == 5.0


def test_get_repeat_count_missing() -> None:
    """get_repeat_count returns None without the program datapoint."""
    product = make_fingerbot_product()
    owner = make_fake_owner({})
    assert fingerbot_program.get_repeat_count(owner, product) is None


def test_set_repeat_count() -> None:
    """set_repeat_count rebuilds the first two bytes and schedules a write."""
    product = make_fingerbot_product()
    data = bytearray(b"\x00\x05\x00\x00\x00")
    owner = make_fake_owner({_fb(product).program: make_datapoint(bytes(data))})
    fingerbot_program.set_repeat_count(owner, product, 7.0)
    assert len(owner.hass.task_calls) == 1


def test_get_repeat_forever_true() -> None:
    """get_repeat_forever returns True for the 0xFFFF marker."""
    product = make_fingerbot_product()
    owner = make_fake_owner({
        _fb(product).program: make_datapoint(b"\xff\xff\x00\x00\x00")
    })
    assert fingerbot_program.get_repeat_forever(owner, product) is True


def test_get_repeat_forever_false() -> None:
    """get_repeat_forever returns False for a finite repeat count."""
    product = make_fingerbot_product()
    owner = make_fake_owner({
        _fb(product).program: make_datapoint(b"\x00\x05\x00\x00\x00")
    })
    assert fingerbot_program.get_repeat_forever(owner, product) is False


def test_get_repeat_forever_missing() -> None:
    """get_repeat_forever returns None for a non-fingerbot product."""
    product = TuyaBLEProductInfo(name="Plain")
    owner = make_fake_owner({})
    assert fingerbot_program.get_repeat_forever(owner, product) is None


def test_set_repeat_forever_true() -> None:
    """set_repeat_forever writes the 0xFFFF marker when enabled."""
    product = make_fingerbot_product()
    owner = make_fake_owner({
        _fb(product).program: make_datapoint(b"\x00\x05\x00\x00\x00")
    })
    fingerbot_program.set_repeat_forever(owner, product, True)
    assert len(owner.hass.task_calls) == 1


def test_set_repeat_forever_false() -> None:
    """set_repeat_forever writes 1 when disabling the forever repeat."""
    product = make_fingerbot_product()
    owner = make_fake_owner({
        _fb(product).program: make_datapoint(b"\xff\xff\x00\x00\x00")
    })
    fingerbot_program.set_repeat_forever(owner, product, False)
    assert len(owner.hass.task_calls) == 1


def test_get_position() -> None:
    """get_position returns the third program byte as a float."""
    product = make_fingerbot_product()
    owner = make_fake_owner({_fb(product).program: make_datapoint(b"\x00\x00\x0a\x00")})
    assert fingerbot_program.get_position(owner, product) == 10.0


def test_get_position_missing() -> None:
    """get_position returns None for non-fingerbot products."""
    product = TuyaBLEProductInfo(name="Plain")
    owner = make_fake_owner({})
    assert fingerbot_program.get_position(owner, product) is None


def test_set_position() -> None:
    """set_position rebuilds the third byte and schedules a write."""
    product = make_fingerbot_product()
    owner = make_fake_owner({
        _fb(product).program: make_datapoint(b"\x00\x00\x00\x00\x00")
    })
    fingerbot_program.set_position(owner, product, 42.0)
    assert len(owner.hass.task_calls) == 1


def test_get_program_multiple_steps() -> None:
    """get_program formats multiple steps with semicolon separators."""
    product = make_fingerbot_product()
    program = b"\x00\x00\x00\x02" + b"\x05\x00\x0a" + b"\x06\x00\x64"
    owner = make_fake_owner({_fb(product).program: make_datapoint(program)})
    assert fingerbot_program.get_program(owner, product) == "5/10;6/100"


def test_get_program_missing() -> None:
    """get_program returns None for non-fingerbot products."""
    product = TuyaBLEProductInfo(name="Plain")
    owner = make_fake_owner({})
    assert fingerbot_program.get_program(owner, product) is None


def test_set_program() -> None:
    """set_program rebuilds the program string and schedules a write."""
    product = make_fingerbot_product()
    owner = make_fake_owner({_fb(product).program: make_datapoint(b"\x00\x00\x00\x00")})
    fingerbot_program.set_program(owner, product, "5/10;6")
    assert len(owner.hass.task_calls) == 1


def test_set_program_non_fingerbot_noop() -> None:
    """set_program does nothing for non-fingerbot products."""
    product = TuyaBLEProductInfo(name="Plain")
    owner = make_fake_owner({})
    fingerbot_program.set_program(owner, product, "5")
    assert owner.hass.task_calls == []


def test_program_handlers_non_fingerbot_noop() -> None:
    """Program get/set handlers no-op for non-fingerbot products."""
    product = TuyaBLEProductInfo(name="Plain")
    owner = make_fake_owner({})
    assert fingerbot_program.get_repeat_count(owner, product) is None
    assert fingerbot_program.get_repeat_forever(owner, product) is None
    assert fingerbot_program.get_position(owner, product) is None
    assert fingerbot_program.get_program(owner, product) is None
    fingerbot_program.set_repeat_count(owner, product, 3.0)
    fingerbot_program.set_repeat_forever(owner, product, True)
    fingerbot_program.set_position(owner, product, 5.0)
    assert owner.hass.task_calls == []


def test_program_handlers_non_bytes_datapoint() -> None:
    """Program get/set handlers ignore non-bytes datapoint values."""
    product = make_fingerbot_product()
    owner = make_fake_owner({_fb(product).program: make_datapoint("nope")})
    assert fingerbot_program.get_repeat_count(owner, product) is None
    assert fingerbot_program.get_repeat_forever(owner, product) is None
    assert fingerbot_program.get_position(owner, product) is None
    assert fingerbot_program.get_program(owner, product) is None
    fingerbot_program.set_repeat_count(owner, product, 3.0)
    fingerbot_program.set_repeat_forever(owner, product, True)
    fingerbot_program.set_position(owner, product, 5.0)
    assert owner.hass.task_calls == []


def test_get_program_step_with_zero_delay() -> None:
    """get_program omits the delay when a step has delay zero."""
    product = make_fingerbot_product()
    program = b"\x00\x00\x00\x02" + b"\x05\x00\x00" + b"\x06\x00\x64"
    owner = make_fake_owner({_fb(product).program: make_datapoint(program)})
    assert fingerbot_program.get_program(owner, product) == "5;6/100"
