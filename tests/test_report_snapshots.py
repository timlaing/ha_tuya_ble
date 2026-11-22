"""Immutable report snapshots captured at BLE packet ingestion."""

# pylint: disable=protected-access, redefined-outer-name
from __future__ import annotations

import asyncio
from struct import pack

import pytest

from custom_components.tuya_ble.tuya_ble.const import (
    TuyaBLECode,
    TuyaBLEDataPointType,
)
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
