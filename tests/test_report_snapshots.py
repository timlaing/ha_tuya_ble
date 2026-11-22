"""Immutable report snapshots captured at BLE packet ingestion."""

# pylint: disable=protected-access, redefined-outer-name
from __future__ import annotations

import asyncio
from struct import pack

import pytest

from custom_components.tuya_ble.tuya_ble.const import (
    MAX_INPUT_LENGTH,
    TuyaBLECode,
    TuyaBLEDataPointType,
)
from custom_components.tuya_ble.tuya_ble.exceptions import TuyaBLEDataLengthError
from tests.protocol_harness import (
    ProtocolHarness,
    encrypt_payload,
    frame_packet0,
)


@pytest.fixture
async def h() -> ProtocolHarness:
    """Build a protocol harness with notifications registered."""
    harness = ProtocolHarness()
    await harness.register_notify()
    return harness


def _session_key(h: ProtocolHarness) -> bytes:
    """Return the device session key."""
    assert h.device._session_key is not None
    return h.device._session_key


async def test_repeated_dp_reports_keep_each_snapshot(h: ProtocolHarness) -> None:
    """Two reports for one id in a packet survive as distinct snapshots."""
    data = b"".join(
        pack(">BBB", 1, TuyaBLEDataPointType.DT_STRING.value, len(value)) + value
        for value in (b"single_click", b"double_click")
    )
    msg = frame_packet0(
        encrypt_payload(_session_key(h), 5, 1, 0, TuyaBLECode.FUN_RECEIVE_DP, data)
    )
    h.notify(msg)
    await asyncio.sleep(0)

    dp = h.device.datapoints[1]
    assert dp is not None
    # The live datapoint holds the final report, as it always has.
    assert dp.value == "double_click"
    # Each report is also kept as its own immutable snapshot.
    reports = dp.reports
    assert len(reports) == 2
    assert [report.report_revision for report in reports] == [1, 2]
    assert [report.reported_value for report in reports] == [
        "single_click",
        "double_click",
    ]
    assert all(report.dp_id == 1 for report in reports)
    assert all(report.dp_type == TuyaBLEDataPointType.DT_STRING for report in reports)


async def test_locally_created_datapoint_has_no_report_snapshot(
    h: ProtocolHarness,
) -> None:
    """A datapoint created for a local write does not invent a device report."""
    datapoints = h.device.datapoints
    dp = datapoints.get_or_create(7, TuyaBLEDataPointType.DT_BOOL)
    assert dp.reports == ()
    assert dp.report_revision == 0


async def test_report_log_retains_more_than_one_packet_of_reports(
    h: ProtocolHarness,
) -> None:
    """Every report in a large packet is retained, not trimmed to a small cap."""
    count = 80  # above the old 64-entry limit
    entries = b"".join(
        pack(">BBB", 3, TuyaBLEDataPointType.DT_BOOL.value, 1) + b"\x01"
        for _ in range(count)
    )
    msg = frame_packet0(
        encrypt_payload(_session_key(h), 5, 1, 0, TuyaBLECode.FUN_RECEIVE_DP, entries)
    )
    h.notify(msg)
    await asyncio.sleep(0)

    assert count < MAX_INPUT_LENGTH // 4
    dp = h.device.datapoints[3]
    assert dp is not None
    reports = dp.reports
    assert len(reports) == count
    assert [report.report_revision for report in reports] == list(range(1, count + 1))


async def test_v3_rejects_trailing_incomplete_entry(h: ProtocolHarness) -> None:
    """A trailing header with a missing value rejects the whole v3 packet."""
    # Complete DP3 entry, then a 3-byte header declaring a 4-byte value that
    # is not present. The loop stops before it, so this must still be rejected.
    data = pack(">BBB", 3, TuyaBLEDataPointType.DT_VALUE.value, 1) + b"\x07"
    data += pack(">BBB", 4, TuyaBLEDataPointType.DT_VALUE.value, 4)
    seen: list[object] = []
    h.device.register_callback(seen.extend)
    msg = frame_packet0(
        encrypt_payload(_session_key(h), 5, 1, 0, TuyaBLECode.FUN_RECEIVE_DP, data)
    )
    with pytest.raises(TuyaBLEDataLengthError):
        h.notify(msg)
    await asyncio.sleep(0)
    assert h.device.datapoints[3] is None
    assert not seen
