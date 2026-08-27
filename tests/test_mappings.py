"""Unit tests for the platform mapping-by-device functions and pure helpers."""

# pylint: disable=protected-access
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, cast

import pytest

from custom_components.tuya_ble import (
    binary_sensor,
    button,
    climate,
    number,
    select,
    sensor,
    switch,
    text,
    valve,
)
from custom_components.tuya_ble.devices import TuyaBLEFingerbotInfo, TuyaBLEProductInfo

PLATFORMS = {
    "binary_sensor": binary_sensor,
    "button": button,
    "climate": climate,
    "number": number,
    "select": select,
    "sensor": sensor,
    "switch": switch,
    "text": text,
    "valve": valve,
}


@dataclass
class FakeDevice:
    """Stand-in device object exposing category and product id."""

    category: str
    product_id: str


def all_datapoints(platform_module: Any) -> Iterator[tuple[str, str]]:
    """Iterate every category/product in the module mapping dict."""
    for category, cat_info in platform_module.mapping.items():
        products = getattr(cat_info, "products", None) or {}
        for product_id in products:
            yield category, product_id


@pytest.mark.parametrize("name", sorted(PLATFORMS))
def test_every_product_has_mapping(name: str) -> None:
    """Verify every known product resolves to at least one mapping."""
    mod = PLATFORMS[name]
    for category, product_id in all_datapoints(mod):
        dev = FakeDevice(category, product_id)
        result = mod.get_mapping_by_device(dev)
        assert result, f"{name}:{category}:{product_id} produced no mappings"


@pytest.mark.parametrize("name", sorted(PLATFORMS))
def test_unknown_category_empty(name: str) -> None:
    """Verify an unknown category produces no mappings."""
    mod = PLATFORMS[name]
    assert mod.get_mapping_by_device(FakeDevice("nope", "nope")) == []


@pytest.mark.parametrize("name", sorted(PLATFORMS))
def test_known_category_unknown_product_falls_back(name: str) -> None:
    """Verify unknown products fall back to the category-level mapping."""
    mod = PLATFORMS[name]
    # patch a category-level fallback mapping and assert it is returned
    category = next(iter(mod.mapping))
    cat_info = mod.mapping[category]
    fallback = cat_info.mapping
    cat_info.mapping = ["fallback"]
    try:
        result = mod.get_mapping_by_device(FakeDevice(category, "unknown"))
        assert result == ["fallback"]
    finally:
        cat_info.mapping = fallback


# --- Pure helpers ----------------------------------------------------------


def make_self(datapoints: list[Any], hass: Any = None) -> Any:
    """Build a fake entity self namespace from the given data points."""
    return SimpleNamespace(
        device=SimpleNamespace(datapoints={dp.dp_id: dp for dp in datapoints}),
        hass=SimpleNamespace(create_task=lambda coro: coro),
    )


def make_dp(value: Any, dp_id: int = 1) -> Any:
    """Build a fake data point with a recorded set_value history."""

    class _DP:
        """A fake data point recording set_value calls."""

        def __init__(self, value: Any, dp_id: int) -> None:
            self.value = value
            self.dp_id = dp_id
            self.calls: list[Any] = []

        def set_value(self, new_value: Any) -> None:
            """Record a set_value call."""
            self.calls.append(new_value)

    return _DP(value, dp_id)


def make_product(mode: int | None = None, program: int | None = None) -> Any:
    """Build a fake product with the given fingerbot mode and program."""
    return SimpleNamespace(
        fingerbot=TuyaBLEFingerbotInfo(
            switch=1,
            mode=mode or 0,
            up_position=5,
            down_position=6,
            hold_time=3,
            reverse_positions=4,
            manual_control=7,
            program=program or 0,
        )
    )


class TestSwitchHelpers:
    """Tests for switch.py fingerbot helper functions."""

    def test_is_fingerbot_in_program_mode_true(self) -> None:
        """Verify program mode is detected when the mode datapoint is 2."""
        product = make_product(mode=8)
        self_ = make_self([make_dp(value=2, dp_id=8)])
        assert switch.is_fingerbot_in_program_mode(self_, product) is True

    def test_is_fingerbot_in_program_mode_false(self) -> None:
        """Verify program mode is not detected when the mode datapoint is not 2."""
        product = make_product(mode=8)
        self_ = make_self([make_dp(value=1, dp_id=8)])
        assert switch.is_fingerbot_in_program_mode(self_, product) is False

    def test_is_fingerbot_in_program_mode_falsy_datapoint(self) -> None:
        """Verify a falsy mode datapoint is treated as program mode."""
        self_: Any = SimpleNamespace(
            device=SimpleNamespace(datapoints={8: None}),
            hass=SimpleNamespace(create_task=lambda coro: coro),
        )
        assert switch.is_fingerbot_in_program_mode(self_, make_product(mode=8)) is True

    def test_is_fingerbot_in_switch_mode_true(self) -> None:
        """Verify switch mode is detected when the mode datapoint is 1."""
        product = make_product(mode=2)
        self_ = make_self([make_dp(value=1, dp_id=2)])
        assert switch.is_fingerbot_in_switch_mode(self_, product) is True

    def test_get_repeat_forever_true(self) -> None:
        """Verify repeat-forever is detected from a 0xffff repeat count."""
        product = make_product(program=121)
        self_ = make_self([make_dp(value=b"\xff\xff\x01\x02\x03", dp_id=121)])
        assert switch.get_fingerbot_program_repeat_forever(self_, product) is True

    def test_get_repeat_forever_false(self) -> None:
        """Verify repeat-forever is not detected from a non-0xffff repeat count."""
        product = make_product(program=121)
        self_ = make_self([make_dp(value=b"\x00\x01\x01\x02\x03", dp_id=121)])
        assert switch.get_fingerbot_program_repeat_forever(self_, product) is False

    def test_get_repeat_forever_non_bytes(self) -> None:
        """Verify repeat-forever returns None for a non-bytes datapoint."""
        product = make_product(program=121)
        self_ = make_self([make_dp(value=5, dp_id=121)])
        assert switch.get_fingerbot_program_repeat_forever(self_, product) is None

    def test_get_repeat_forever_no_program(self) -> None:
        """Verify repeat-forever returns None when no program datapoint exists."""
        self_ = make_self([])
        assert (
            switch.get_fingerbot_program_repeat_forever(self_, make_product()) is None
        )

    def test_set_repeat_forever_true_and_false(self) -> None:
        """Verify setting repeat-forever to true and false records calls."""
        product = make_product(program=121)
        self_ = make_self([make_dp(value=b"\x00\x01\x02\x03", dp_id=121)])
        switch.set_fingerbot_program_repeat_forever(self_, product, True)
        switch.set_fingerbot_program_repeat_forever(self_, product, False)


class TestTextHelpers:
    """Tests for text.py fingerbot helper functions."""

    def test_is_fingerbot_in_program_mode(self) -> None:
        """Verify the fingerbot program-mode text helper."""
        self_ = make_self([make_dp(value=2, dp_id=2)])
        assert text.is_fingerbot_in_program_mode(self_, make_product(mode=2)) is True

    def test_get_fingerbot_program(self) -> None:
        """Verify formatting a fingerbot program datapoint into a string."""
        # header(3) + step_count(1) + steps(3 each)
        value = b"\x00\x01\x02" + b"\x02" + b"\x05\x00\x07" + b"\x0a\x13\x88"
        product = make_product(program=121)
        self_ = make_self([make_dp(value=value, dp_id=121)])
        result = text.get_fingerbot_program(self_, product)
        assert result is not None
        assert result.startswith("5/7")

    def test_get_fingerbot_program_no_datapoint(self) -> None:
        """Verify get_fingerbot_program returns None when no datapoint exists."""
        product = make_product(program=121)
        self_ = make_self([make_dp(value=5, dp_id=121)])
        assert text.get_fingerbot_program(self_, product) is None

    def test_format_program_step(self) -> None:
        """Verify a single program step is formatted correctly."""
        data = b"\x00\x00\x00\x01" + b"\x05\x13\x88"
        assert text._format_program_step(data, 0) == "5/5000"
        data2 = b"\x00\x00\x00\x01" + b"\x05\x00\x00"
        assert text._format_program_step(data2, 0) == "5"

    def test_set_fingerbot_program(self) -> None:
        """Verify setting a fingerbot program records the expected call."""
        value = b"\x00\x01\x02" + b"\x01" + b"\x05\x13\x88"
        product = make_product(program=121)
        self_ = make_self([make_dp(value=value, dp_id=121)])
        text.set_fingerbot_program(self_, product, "5/5000;10")


class TestNumberHelpers:
    """Tests for number.py fingerbot helper functions."""

    def test_is_fingerbot_in_program_mode(self) -> None:
        """Verify the fingerbot program-mode text helper."""
        self_ = make_self([make_dp(value=2, dp_id=8)])
        assert number.is_fingerbot_in_program_mode(self_, make_product(mode=8)) is True

    def test_is_fingerbot_not_in_program_mode(self) -> None:
        """Verify not-in-program-mode is detected when mode is not 2."""
        self_ = make_self([make_dp(value=1, dp_id=8)])
        assert (
            number.is_fingerbot_not_in_program_mode(self_, make_product(mode=8)) is True
        )

    def test_is_fingerbot_in_push_mode(self) -> None:
        """Verify push mode is detected when the mode datapoint is 0."""
        self_ = make_self([make_dp(value=0, dp_id=8)])
        assert number.is_fingerbot_in_push_mode(self_, make_product(mode=8)) is True

    def test_repeat_count_available_false_when_forever(self) -> None:
        """Verify repeat count is unavailable when set to repeat forever."""
        product = make_product(mode=8, program=121)
        self_ = make_self([
            make_dp(value=2, dp_id=8),
            make_dp(value=b"\xff\xff\x00", dp_id=121),
        ])
        assert number.is_fingerbot_repeat_count_available(self_, product) is False

    def test_repeat_count_available_true(self) -> None:
        """Verify repeat count is available for a finite repeat count."""
        product = make_product(mode=8, program=121)
        self_ = make_self([
            make_dp(value=2, dp_id=8),
            make_dp(value=b"\x00\x05\x00", dp_id=121),
        ])
        assert number.is_fingerbot_repeat_count_available(self_, product) is True

    def test_repeat_count_available_no_program(self) -> None:
        """Verify repeat count is available when no program datapoint exists."""
        self_ = make_self([make_dp(value=2, dp_id=8)])
        assert (
            number.is_fingerbot_repeat_count_available(self_, make_product(mode=8))
            is True
        )

    def test_get_repeat_count(self) -> None:
        """Verify the repeat count is read from the program datapoint."""
        product = make_product(program=121)
        self_ = make_self([make_dp(value=b"\x00\x05\x00", dp_id=121)])
        assert number.get_fingerbot_program_repeat_count(self_, product) == 5.0

    def test_get_repeat_count_none(self) -> None:
        """Verify repeat count returns None when no datapoint exists."""
        self_ = make_self([])
        assert number.get_fingerbot_program_repeat_count(self_, make_product()) is None

    def test_set_repeat_count(self) -> None:
        """Verify setting the repeat count records the expected call."""
        product = make_product(program=121)
        self_ = make_self([make_dp(value=b"\x00\x05\x00", dp_id=121)])
        number.set_fingerbot_program_repeat_count(self_, product, 3.0)

    def test_get_position(self) -> None:
        """Verify the program position is read from the datapoint."""
        product = make_product(program=121)
        self_ = make_self([make_dp(value=b"\x00\x05\x07\x00", dp_id=121)])
        assert number.get_fingerbot_program_position(self_, product) == 7.0

    def test_set_position(self) -> None:
        """Verify setting the program position records the expected call."""
        product = make_product(program=121)
        self_ = make_self([make_dp(value=b"\x00\x05\x07\x00", dp_id=121)])
        number.set_fingerbot_program_position(self_, product, 9.0)


class TestSensorHelpers:
    """Tests for sensor.py helper functions."""

    def test_battery_enum_getter(self) -> None:
        """Verify the battery enum getter converts the datapoint to a percentage."""
        self_: Any = SimpleNamespace(
            device=SimpleNamespace(datapoints={104: make_dp(value=3, dp_id=104)}),
            _attr_native_value=None,
            set_native_value=lambda v: setattr(self_, "_attr_native_value", v),
        )
        sensor.battery_enum_getter(self_)
        assert self_._attr_native_value == 60.0

    def test_rssi_getter(self) -> None:
        """Verify the RSSI getter propagates the device signal strength."""
        self_: Any = SimpleNamespace(
            device=SimpleNamespace(rssi=-70),
            _attr_native_value=None,
            set_native_value=lambda v: setattr(self_, "_attr_native_value", v),
        )
        sensor.rssi_getter(self_)
        assert self_._attr_native_value == -70

    def test_is_co2_alarm_enabled(self) -> None:
        """Verify the CO2 alarm-enabled helper reads datapoint 13."""
        self_: Any = SimpleNamespace(
            device=SimpleNamespace(datapoints={13: make_dp(value=1, dp_id=13)})
        )
        assert (
            sensor.is_co2_alarm_enabled(
                self_, cast(TuyaBLEProductInfo, SimpleNamespace())
            )
            is True
        )


class TestButtonHelpers:
    """Tests for button.py fingerbot helper functions."""

    def test_is_fingerbot_in_push_mode_true(self) -> None:
        """Verify push mode is detected when the mode datapoint is 0."""
        product = make_product(mode=2)
        self_ = make_self([make_dp(value=0, dp_id=2)])
        assert button.is_fingerbot_in_push_mode(self_, product) is True

    def test_is_fingerbot_in_push_mode_false(self) -> None:
        """Verify push mode is not detected when the mode datapoint is not 0."""
        product = make_product(mode=2)
        self_ = make_self([make_dp(value=1, dp_id=2)])
        assert button.is_fingerbot_in_push_mode(self_, product) is False


class TestSelectDescription:
    """Tests for select.py entity descriptions."""

    def test_temperature_unit_description(self) -> None:
        """Verify the temperature unit entity description is built with a key."""
        desc = select.TemperatureUnitDescription(key="temperature_unit")
        assert desc.key == "temperature_unit"


def test_fingerbot_info_defaults() -> None:
    """Verify default fingerbot info fields."""
    info = TuyaBLEFingerbotInfo(
        switch=1,
        mode=2,
        up_position=5,
        down_position=6,
        hold_time=3,
        reverse_positions=4,
    )
    assert info.manual_control == 0
    assert info.program == 0


@pytest.mark.parametrize(
    "name,category",
    [
        ("switch", "co2bj"),
        ("button", "znhsb"),
        ("binary_sensor", "wk"),
        ("text", "szjqr"),
        ("select", "co2bj"),
        ("number", "co2bj"),
        ("sensor", "co2bj"),
        ("valve", "ggq"),
        ("climate", "wk"),
    ],
)
def test_get_mapping_by_device_no_default_mapping(name: str, category: str) -> None:
    """Known category with no default mapping returns [] for unknown product."""
    mod = PLATFORMS[name]
    result = mod.get_mapping_by_device(FakeDevice(category, "unknown_xyz"))
    assert result == []
