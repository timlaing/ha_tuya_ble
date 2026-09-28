"""Unit tests for the Tuya BLE data point classes."""

from __future__ import annotations

import logging

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


def test_raw_value_defaults_to_none(datapoints: TuyaBLEDataPoints) -> None:
    """A data point created locally has no received-bytes snapshot."""
    assert make_dp(datapoints, dp_id=4).raw_value is None


def test_raw_value_keeps_original_width(datapoints: TuyaBLEDataPoints) -> None:
    """The snapshot must keep the wire bytes, not the re-serialized value."""
    datapoints.update_from_device(
        5,
        9.0,
        0,
        TuyaBLEDataPointType.DT_VALUE,
        100,
        b"\x00\x00\x00\x64",
    )
    dp = datapoints[5]
    assert dp is not None
    assert dp.value == 100
    assert dp.raw_value == b"\x00\x00\x00\x64"


def test_raw_value_reflects_latest_report(
    datapoints: TuyaBLEDataPoints,
) -> None:
    """Each device report replaces the snapshot."""
    datapoints.update_from_device(
        5, 9.0, 0, TuyaBLEDataPointType.DT_ENUM, 1, b"\x00\x01"
    )
    assert datapoints[5].raw_value == b"\x00\x01"  # type: ignore[union-attr]
    datapoints.update_from_device(5, 10.0, 0, TuyaBLEDataPointType.DT_ENUM, 2, b"\x02")
    assert datapoints[5].raw_value == b"\x02"  # type: ignore[union-attr]


def test_raw_value_survives_local_write(
    datapoints: TuyaBLEDataPoints, datapoints_owner: FakeDatapointsOwner
) -> None:
    """A local write must not overwrite the last received snapshot."""
    datapoints.update_from_device(
        5, 9.0, 0, TuyaBLEDataPointType.DT_VALUE, 100, b"\x00\x00\x00\x64"
    )
    dp = datapoints[5]
    assert dp is not None
    dp.set_value_no_notify(200)
    assert dp.value == 200
    assert dp.raw_value == b"\x00\x00\x00\x64"


def test_raw_value_widens_narrow_value_on_serialization(
    datapoints: TuyaBLEDataPoints,
) -> None:
    """Demonstrate why get_value() cannot stand in for the raw snapshot."""
    datapoints.update_from_device(
        5, 9.0, 0, TuyaBLEDataPointType.DT_VALUE, 256, b"\x01\x00"
    )
    dp = datapoints[5]
    assert dp is not None
    assert dp.raw_value == b"\x01\x00"
    assert dp.get_value() == b"\x00\x00\x01\x00"


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


# --------------------------------------------------------------------------
# Debug logging
# --------------------------------------------------------------------------


def test_update_from_device_logs_only_changes(
    datapoints: TuyaBLEDataPoints, caplog: pytest.LogCaptureFixture
) -> None:
    """A value that did not change is not traced as a device update."""
    dp = make_dp(datapoints, dp_id=5, dp_type=TuyaBLEDataPointType.DT_BOOL, value=True)

    with caplog.at_level(logging.DEBUG):
        dp.update_from_device(2.0, 0, TuyaBLEDataPointType.DT_BOOL, True)
    assert "Data point 5 changed by device" not in caplog.text

    with caplog.at_level(logging.DEBUG):
        dp.update_from_device(3.0, 0, TuyaBLEDataPointType.DT_BOOL, False)
    assert "Data point 5 changed by device: True -> False" in caplog.text


def test_datapoint_creation_does_not_log_a_change(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Creating a data point pre-sets the value, so no change is reported."""
    dps = TuyaBLEDataPoints(FakeDatapointsOwner())  # type: ignore[arg-type]
    with caplog.at_level(logging.DEBUG):
        make_dp(dps, dp_id=9, dp_type=TuyaBLEDataPointType.DT_BOOL, value=True)
    assert "changed by device" not in caplog.text


def test_get_value_unhandled_type_is_warned(
    datapoints: TuyaBLEDataPoints, caplog: pytest.LogCaptureFixture
) -> None:
    """An unknown data point type warns and serializes to an empty value."""
    dp = make_dp(datapoints, dp_id=3, dp_type=TuyaBLEDataPointType.DT_RAW, value=b"")

    with caplog.at_level(logging.DEBUG):
        dp._type = "not-a-real-type"  # type: ignore[assignment] # pylint: disable=protected-access
        assert dp.get_value() == b""

    assert "Unhandled data point type not-a-real-type for data point 3" in caplog.text
    assert caplog.records[0].levelno == logging.WARNING


def test_set_value_no_notify_is_logged(
    datapoints: TuyaBLEDataPoints, caplog: pytest.LogCaptureFixture
) -> None:
    """A local set that does not notify the device is traced."""
    dp = make_dp(datapoints, dp_id=4, dp_type=TuyaBLEDataPointType.DT_BOOL, value=False)

    with caplog.at_level(logging.DEBUG):
        dp.set_value_no_notify(True)

    assert "Data point 4 set locally without notifying the device: True" in caplog.text
    assert dp.changed_by_device is False


def test_set_enum_value_logs_int_branch(
    datapoints: TuyaBLEDataPoints, caplog: pytest.LogCaptureFixture
) -> None:
    """An integer enum assignment records that the int branch resolved."""
    dp = make_dp(datapoints, dp_id=1, dp_type=TuyaBLEDataPointType.DT_ENUM, value=0)
    with caplog.at_level(logging.DEBUG):
        dp._set_enum_value(3)  # pylint: disable=protected-access
    assert "Data point 1 enum set from int: 3" in caplog.text
    assert dp.value == 3


def test_set_enum_value_logs_numeric_string_branch(
    datapoints: TuyaBLEDataPoints, caplog: pytest.LogCaptureFixture
) -> None:
    """A numeric string enum assignment resolves to an int and is traced."""
    dp = make_dp(datapoints, dp_id=1, dp_type=TuyaBLEDataPointType.DT_ENUM, value=0)
    with caplog.at_level(logging.DEBUG):
        dp._set_enum_value("4")  # pylint: disable=protected-access
    assert "Data point 1 enum set from numeric string: 4" in caplog.text
    assert dp.value == 4


def test_set_enum_value_logs_name_string_branch(
    datapoints: TuyaBLEDataPoints, caplog: pytest.LogCaptureFixture
) -> None:
    """A non-numeric string enum assignment is kept verbatim and is traced."""
    dp = make_dp(datapoints, dp_id=1, dp_type=TuyaBLEDataPointType.DT_ENUM, value=0)
    with caplog.at_level(logging.DEBUG):
        dp._set_enum_value("auto")  # pylint: disable=protected-access
    assert "Data point 1 enum set from name string: auto" in caplog.text
    assert dp.value == "auto"


def test_get_or_create_logs_creation(
    datapoints: TuyaBLEDataPoints, caplog: pytest.LogCaptureFixture
) -> None:
    """Creating a new data point is traced; returning an existing one is not."""
    with caplog.at_level(logging.DEBUG):
        datapoints.get_or_create(6, TuyaBLEDataPointType.DT_BOOL)
    assert "Creating new data point 6 (TuyaBLEDataPointType.DT_BOOL)" in caplog.text

    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        datapoints.get_or_create(6, TuyaBLEDataPointType.DT_BOOL)
    assert "Creating new data point 6" not in caplog.text


async def test_batch_update_logs_open_and_flush(
    datapoints: TuyaBLEDataPoints,
    datapoints_owner: FakeDatapointsOwner,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Opening a batch and flushing the queued ids are both traced."""
    dp = datapoints.get_or_create(1, TuyaBLEDataPointType.DT_BOOL)

    with caplog.at_level(logging.DEBUG):
        datapoints.begin_update()
    assert "Batch update opened, nesting depth 1" in caplog.text

    with caplog.at_level(logging.DEBUG):
        await dp.set_value(True)
    assert "Data point 1 queued in batch update (nesting depth 1)" in caplog.text

    with caplog.at_level(logging.DEBUG):
        await datapoints.end_update()
    assert "Batch update closed, flushing data points: [1]" in caplog.text
    assert datapoints_owner.sent == [[1]]


async def test_nested_batch_update_logs_depth(
    datapoints: TuyaBLEDataPoints,
    datapoints_owner: FakeDatapointsOwner,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A nested batch close that does not flush reports the reduced depth."""
    dp = datapoints.get_or_create(1, TuyaBLEDataPointType.DT_BOOL)
    datapoints.begin_update()
    datapoints.begin_update()

    with caplog.at_level(logging.DEBUG):
        await dp.set_value(True)
    assert "queued in batch update (nesting depth 2)" in caplog.text

    with caplog.at_level(logging.DEBUG):
        await datapoints.end_update()
    assert "Batch update closed, nesting depth 1" in caplog.text
    assert datapoints_owner.sent == []

    await datapoints.end_update()
    assert datapoints_owner.sent == [[1]]


async def test_update_from_user_logs_immediate_send(
    datapoints: TuyaBLEDataPoints,
    datapoints_owner: FakeDatapointsOwner,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A write outside a batch is traced as sent immediately."""
    dp = datapoints.get_or_create(2, TuyaBLEDataPointType.DT_BOOL)
    with caplog.at_level(logging.DEBUG):
        await dp.set_value(True)
    assert "Data point 2 sent immediately" in caplog.text
    assert datapoints_owner.sent == [[2]]


def test_collection_update_logs_creation(
    datapoints: TuyaBLEDataPoints, caplog: pytest.LogCaptureFixture
) -> None:
    """The first device update for an id is traced as a creation, not a change."""
    with caplog.at_level(logging.DEBUG):
        datapoints.update_from_device(8, 1000.0, 0, TuyaBLEDataPointType.DT_BOOL, True)
    assert "Data point 8 created from device update" in caplog.text
    assert "changed by device" not in caplog.text
