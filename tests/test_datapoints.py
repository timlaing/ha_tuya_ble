"""Unit tests for the Tuya BLE data point classes."""

from __future__ import annotations

import pytest

from custom_components.tuya_ble.tuya_ble.const import TuyaBLEDataPointType
from custom_components.tuya_ble.tuya_ble.datapoints import (
    TuyaBLEDataPoint,
    TuyaBLEDataPoints,
)
from custom_components.tuya_ble.tuya_ble.exceptions import (
    TuyaBLEDataFormatError,
    TuyaBLEEnumValueError,
)
from tests.conftest import FakeDatapointsOwner


def make_dp(
    owner: TuyaBLEDataPoints,
    dp_id: int = 1,
    dp_type: TuyaBLEDataPointType = TuyaBLEDataPointType.DT_RAW,
    value: bytes | bool | int | str | None = None,
) -> TuyaBLEDataPoint:
    """Build a TuyaBLEDataPoint wired to the given owner."""
    return TuyaBLEDataPoint(
        owner,
        dp_id,
        timestamp=1.0,
        flags=0,
        dp_type=dp_type,
        value=value if value is not None else b"",
    )


def test_properties(datapoints: TuyaBLEDataPoints) -> None:
    """Assert the data point exposes its stored properties."""
    dp = make_dp(datapoints, dp_id=7, dp_type=TuyaBLEDataPointType.DT_BOOL, value=True)
    assert dp.dp_id == 7
    assert dp.timestamp == 1.0
    assert dp.flags == 0
    assert dp.dp_type == TuyaBLEDataPointType.DT_BOOL
    assert dp.value is True
    assert dp.changed_by_device is False


def test_update_from_device_changed(datapoints: TuyaBLEDataPoints) -> None:
    """Assert a device update changes the value and flags."""
    dp = make_dp(datapoints, dp_type=TuyaBLEDataPointType.DT_BOOL, value=False)
    dp.update_from_device(5.0, 1, TuyaBLEDataPointType.DT_BOOL, True)
    assert dp.timestamp == 5.0
    assert dp.flags == 1
    assert dp.value is True
    assert dp.changed_by_device is True


def test_update_from_device_unchanged(datapoints: TuyaBLEDataPoints) -> None:
    """Assert an equal device update does not mark the value as changed."""
    dp = make_dp(datapoints, dp_type=TuyaBLEDataPointType.DT_BOOL, value=True)
    dp.update_from_device(5.0, 1, TuyaBLEDataPointType.DT_BOOL, True)
    assert dp.changed_by_device is False


def test_constructor_uses_update_from_device(datapoints: TuyaBLEDataPoints) -> None:
    """Assert the constructor applies the update_from_device logic."""
    dp = TuyaBLEDataPoint(datapoints, 1, 2.0, 3, TuyaBLEDataPointType.DT_VALUE, 42)
    assert dp.timestamp == 2.0
    assert dp.flags == 3
    assert dp.value == 42


@pytest.mark.parametrize(
    ("dp_type", "value", "expected"),
    [
        (TuyaBLEDataPointType.DT_RAW, b"\x01\x02", b"\x01\x02"),
        (TuyaBLEDataPointType.DT_BITMAP, b"\xff\x00", b"\xff\x00"),
        (TuyaBLEDataPointType.DT_BOOL, True, b"\x01"),
        (TuyaBLEDataPointType.DT_BOOL, False, b"\x00"),
        (TuyaBLEDataPointType.DT_VALUE, 0, b"\x00\x00\x00\x00"),
        (TuyaBLEDataPointType.DT_VALUE, -1, b"\xff\xff\xff\xff"),
        (TuyaBLEDataPointType.DT_VALUE, 258, b"\x00\x00\x01\x02"),
        (TuyaBLEDataPointType.DT_ENUM, 1, b"\x01"),
        (TuyaBLEDataPointType.DT_ENUM, 0x0100, b"\x01\x00"),
        (TuyaBLEDataPointType.DT_ENUM, 0x010000, b"\x00\x01\x00\x00"),
        (TuyaBLEDataPointType.DT_STRING, "hi", b"hi"),
    ],
)
def test_get_value(
    datapoints: TuyaBLEDataPoints,
    dp_type: TuyaBLEDataPointType,
    value: bytes | bool | int | str,
    expected: bytes,
) -> None:
    """Assert get_value serializes each data point type to bytes."""
    dp = make_dp(datapoints, dp_type=dp_type, value=value)
    assert dp.get_value() == expected


@pytest.mark.parametrize(
    ("dp_type", "value", "expected"),
    [
        (TuyaBLEDataPointType.DT_RAW, b"\x01", b"\x01"),
        (TuyaBLEDataPointType.DT_RAW, "abc", b"abc"),
        (TuyaBLEDataPointType.DT_BITMAP, b"\x01", b"\x01"),
        (TuyaBLEDataPointType.DT_BOOL, "x", True),
        (TuyaBLEDataPointType.DT_BOOL, 0, False),
        (TuyaBLEDataPointType.DT_VALUE, "42", 42),
        (TuyaBLEDataPointType.DT_ENUM, "3", 3),
        (TuyaBLEDataPointType.DT_STRING, 42, "42"),
    ],
)
async def test_set_value(
    datapoints: TuyaBLEDataPoints,
    datapoints_owner: FakeDatapointsOwner,
    dp_type: TuyaBLEDataPointType,
    value: bytes | str | int | bool,
    expected: bytes | str | int | bool,
) -> None:
    """Assert set_value coerces the value and sends the data point id."""
    dp = make_dp(datapoints, dp_type=dp_type, value=b"\x00")
    await dp.set_value(value)
    assert dp.value == expected
    assert dp.changed_by_device is False
    assert datapoints_owner.sent == [[dp.dp_id]]


async def test_set_value_enum_negative_raises(datapoints: TuyaBLEDataPoints) -> None:
    """Assert a negative enum value raises TuyaBLEEnumValueError."""
    dp = make_dp(datapoints, dp_type=TuyaBLEDataPointType.DT_ENUM, value=1)
    with pytest.raises(TuyaBLEEnumValueError):
        await dp.set_value(-1)


def test_enum_positive_zero_set(datapoints: TuyaBLEDataPoints) -> None:
    """Assert the data point keeps its enum type after an update."""
    dp = make_dp(datapoints, dp_type=TuyaBLEDataPointType.DT_ENUM, value=1)
    assert dp.dp_type == TuyaBLEDataPointType.DT_ENUM


def test_len_and_getitem(datapoints: TuyaBLEDataPoints) -> None:
    """Assert the collection supports len and missing-key lookups."""
    datapoints.get_or_create(1, TuyaBLEDataPointType.DT_BOOL)
    assert len(datapoints) == 1
    assert datapoints[1] is not None
    assert datapoints[99] is None


def test_get_or_create_existing(datapoints: TuyaBLEDataPoints) -> None:
    """Assert get_or_create returns the already-created data point."""
    first = datapoints.get_or_create(1, TuyaBLEDataPointType.DT_BOOL)
    second = datapoints.get_or_create(1, TuyaBLEDataPointType.DT_BOOL)
    assert first is second


def test_get_or_create_default_empty(datapoints: TuyaBLEDataPoints) -> None:
    """Assert a new data point defaults to an empty bytes value."""
    dp = datapoints.get_or_create(2, TuyaBLEDataPointType.DT_RAW)
    assert dp.value == b""


def test_get_or_create_with_value(datapoints: TuyaBLEDataPoints) -> None:
    """Assert a new data point with a given value stores that value."""
    dp = datapoints.get_or_create(3, TuyaBLEDataPointType.DT_BOOL, True)
    assert dp.value is True


def test_has_id(datapoints: TuyaBLEDataPoints) -> None:
    """Assert has_id matches on id alone or id plus type."""
    datapoints.get_or_create(1, TuyaBLEDataPointType.DT_BOOL)
    assert datapoints.has_id(1) is True
    assert datapoints.has_id(2) is False
    assert datapoints.has_id(1, TuyaBLEDataPointType.DT_BOOL) is True
    assert datapoints.has_id(1, TuyaBLEDataPointType.DT_VALUE) is False


def test_update_from_device_new(datapoints: TuyaBLEDataPoints) -> None:
    """Assert a device update for an unknown id creates a data point."""
    datapoints.update_from_device(5, 9.0, 2, TuyaBLEDataPointType.DT_VALUE, 33)
    dp = datapoints[5]
    assert dp is not None
    assert dp.value == 33
    assert dp.timestamp == 9.0
    assert dp.flags == 2


def test_update_from_device_existing(datapoints: TuyaBLEDataPoints) -> None:
    """Assert a device update for a known id replaces its value."""
    datapoints.update_from_device(5, 9.0, 2, TuyaBLEDataPointType.DT_VALUE, 33)
    datapoints.update_from_device(5, 10.0, 3, TuyaBLEDataPointType.DT_VALUE, 44)
    assert datapoints[5].value == 44  # type: ignore[union-attr]
    assert len(datapoints) == 1


async def test_begin_end_update_sends_deferred(
    datapoints: TuyaBLEDataPoints, datapoints_owner: FakeDatapointsOwner
) -> None:
    """Assert writes during an update are sent once the update ends."""
    dp = datapoints.get_or_create(1, TuyaBLEDataPointType.DT_BOOL)
    datapoints.begin_update()
    await dp.set_value(True)
    assert datapoints_owner.sent == []
    await datapoints.end_update()
    assert datapoints_owner.sent == [[1]]


async def test_end_update_noop_when_not_started(
    datapoints: TuyaBLEDataPoints, datapoints_owner: FakeDatapointsOwner
) -> None:
    """Assert ending an update that was never started has no effect."""
    await datapoints.end_update()
    assert datapoints_owner.sent == []


async def test_nested_begin_end(
    datapoints: TuyaBLEDataPoints, datapoints_owner: FakeDatapointsOwner
) -> None:
    """Assert nested update scopes only send once the outer update ends."""
    dp = datapoints.get_or_create(1, TuyaBLEDataPointType.DT_BOOL)
    datapoints.begin_update()
    datapoints.begin_update()
    await dp.set_value(True)
    await datapoints.end_update()
    assert datapoints_owner.sent == []
    await datapoints.end_update()
    assert datapoints_owner.sent == [[1]]


async def test_update_from_user_immediate(
    datapoints: TuyaBLEDataPoints, datapoints_owner: FakeDatapointsOwner
) -> None:
    """Assert writes outside an update are sent immediately."""
    dp = datapoints.get_or_create(1, TuyaBLEDataPointType.DT_BOOL)
    await dp.set_value(True)
    assert datapoints_owner.sent == [[1]]


async def test_updated_datapoints_dedupe(
    datapoints: TuyaBLEDataPoints, datapoints_owner: FakeDatapointsOwner
) -> None:
    """Assert repeated writes to one id collapse into a single entry."""
    dp1 = datapoints.get_or_create(1, TuyaBLEDataPointType.DT_BOOL)
    dp2 = datapoints.get_or_create(2, TuyaBLEDataPointType.DT_VALUE, 5)
    datapoints.begin_update()
    await dp1.set_value(True)
    await dp2.set_value(6)
    await dp1.set_value(False)
    await datapoints.end_update()
    assert datapoints_owner.sent == [[2, 1]]


def test_get_value_returns_encoded_string() -> None:
    """DT_STRING get_value returns value.encode()."""
    dps = TuyaBLEDataPoints(FakeDatapointsOwner())  # type: ignore[arg-type]
    dp = dps.get_or_create(1, TuyaBLEDataPointType.DT_STRING, "hello")
    assert dp.get_value() == b"hello"


async def test_set_value_converts_to_string() -> None:
    """DT_STRING set_value converts value via str()."""
    dps = TuyaBLEDataPoints(FakeDatapointsOwner())  # type: ignore[arg-type]
    dp = dps.get_or_create(1, TuyaBLEDataPointType.DT_STRING, "old")
    await dp.set_value(123)
    assert dp.value == "123"


def test_get_value_dt_raw_wrong_type(datapoints: TuyaBLEDataPoints) -> None:
    """DT_RAW get_value raises TuyaBLEDataFormatError when value is not bytes."""
    dp = make_dp(datapoints, dp_type=TuyaBLEDataPointType.DT_RAW, value="not_bytes")
    with pytest.raises(TuyaBLEDataFormatError):
        dp.get_value()


def test_get_value_dt_bitmap_wrong_type(datapoints: TuyaBLEDataPoints) -> None:
    """DT_BITMAP get_value raises TuyaBLEDataFormatError for non-bytes."""
    dp = make_dp(datapoints, dp_type=TuyaBLEDataPointType.DT_BITMAP, value="not_bytes")
    with pytest.raises(TuyaBLEDataFormatError):
        dp.get_value()


def test_get_value_dt_value_wrong_type(datapoints: TuyaBLEDataPoints) -> None:
    """DT_VALUE get_value raises TuyaBLEDataFormatError when value is not int."""
    dp = make_dp(datapoints, dp_type=TuyaBLEDataPointType.DT_VALUE, value="not_int")
    with pytest.raises(TuyaBLEDataFormatError):
        dp.get_value()


def test_get_value_dt_enum_wrong_type(datapoints: TuyaBLEDataPoints) -> None:
    """DT_ENUM get_value raises TuyaBLEDataFormatError when value is not int."""
    dp = make_dp(datapoints, dp_type=TuyaBLEDataPointType.DT_ENUM, value="not_int")
    with pytest.raises(TuyaBLEDataFormatError):
        dp.get_value()


def test_get_value_dt_string_wrong_type(datapoints: TuyaBLEDataPoints) -> None:
    """DT_STRING get_value raises TuyaBLEDataFormatError when value is not str."""
    dp = make_dp(datapoints, dp_type=TuyaBLEDataPointType.DT_STRING, value=123)
    with pytest.raises(TuyaBLEDataFormatError):
        dp.get_value()


def test_set_value_dt_enum_wrong_type_raises(datapoints: TuyaBLEDataPoints) -> None:
    """DT_ENUM set_value raises TuyaBLEEnumValueError for non-int/str."""
    dp = make_dp(datapoints, dp_type=TuyaBLEDataPointType.DT_ENUM, value=1)
    with pytest.raises(TuyaBLEEnumValueError):
        dp._set_enum_value(b"\x01")  # pylint: disable=protected-access
