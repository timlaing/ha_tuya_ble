"""Unit tests for the Tuya BLE protocol mixin, driven via public methods."""

# pylint: disable=protected-access, redefined-outer-name
from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from struct import pack
from typing import Any

import pytest

from custom_components.tuya_ble.tuya_ble import TuyaBLEDataPoint
from custom_components.tuya_ble.tuya_ble.const import (
    TuyaBLECode,
    TuyaBLEDataPointType,
)
from custom_components.tuya_ble.tuya_ble.exceptions import (
    TuyaBLEDataCRCError,
    TuyaBLEDataFormatError,
    TuyaBLEDataLengthError,
    TuyaBLEDeviceError,
)
from custom_components.tuya_ble.tuya_ble.protocol_mixin import TuyaBLEProtocol
from tests.protocol_harness import (
    ProtocolHarness,
    encrypt_payload,
    encrypt_raw,
    frame_packet0,
    make_raw,
    pack_varint,
)


def session_key(h: ProtocolHarness) -> bytes:
    """Return the device session key."""
    assert h.device._session_key is not None
    return h.device._session_key


def login_key(h: ProtocolHarness) -> bytes:
    """Return the device login key."""
    assert h.device._login_key is not None
    return h.device._login_key


async def conclude(coro: Coroutine[Any, Any, None], feed: Callable[[], None]) -> None:
    """Run a coroutine while feeding notifications into the device."""
    task = asyncio.ensure_future(coro)
    await asyncio.sleep(0)
    feed()
    await task


@pytest.fixture
async def h() -> ProtocolHarness:
    """Build a protocol harness with notifications registered."""
    harness = ProtocolHarness()
    await harness.register_notify()
    return harness


async def test_update_sends_device_status(h: ProtocolHarness) -> None:
    """Update should send a device status packet."""
    resp = frame_packet0(
        encrypt_payload(
            session_key(h), 5, 1, 1, TuyaBLECode.FUN_SENDER_DEVICE_STATUS, b"\x00"
        )
    )
    await conclude(h.device.update(), lambda: h.notify(resp))
    assert len(h.writes()) >= 1


async def test_pair_sends_pairing_request(h: ProtocolHarness) -> None:
    """Pair should send a pairing request packet."""
    resp = frame_packet0(
        encrypt_payload(session_key(h), 5, 1, 1, TuyaBLECode.FUN_SENDER_PAIR, b"\x00")
    )
    await conclude(h.device.pair(), lambda: h.notify(resp))
    assert len(h.writes()) >= 1


async def test_packet_encrypted_and_fragmented(h: ProtocolHarness) -> None:
    """Sent packets should be encrypted and fragmented."""
    resp = frame_packet0(
        encrypt_payload(
            session_key(h), 5, 1, 1, TuyaBLECode.FUN_SENDER_DEVICE_STATUS, b"\x00"
        )
    )
    await conclude(h.device.update(), lambda: h.notify(resp))
    first = h.writes()[0]
    assert first[0] == 0
    assert any(w != first for w in h.writes()[1:])


async def test_send_datapoints_v3(h: ProtocolHarness) -> None:
    """Datapoints should be sendable for protocol version 3."""
    h.device._protocol_version = 3
    dp = h.device.get_or_create_datapoint(1, TuyaBLEDataPointType.DT_BOOL, False)
    resp = frame_packet0(
        encrypt_payload(session_key(h), 5, 1, 1, TuyaBLECode.FUN_SENDER_DPS, b"\x00")
    )
    await conclude(dp.set_value(True), lambda: h.notify(resp))
    joined = b"".join(h.writes())
    assert join_dps(joined)


async def test_send_datapoints_wrong_protocol_raises(h: ProtocolHarness) -> None:
    """Sending datapoints with an unsupported protocol should raise."""
    h.device._protocol_version = 2
    dp = h.device.get_or_create_datapoint(1, TuyaBLEDataPointType.DT_BOOL, False)
    with pytest.raises(TuyaBLEDeviceError):
        await dp.set_value(True)


def join_dps(data: bytes) -> bool:
    """Return whether the joined packet is non-trivial in size."""
    return len(data) > 5


def _dp_data() -> bytes:
    out = bytearray()
    for dp_id, dp_type, value in [
        (1, TuyaBLEDataPointType.DT_RAW, b"\x01\x02"),
        (2, TuyaBLEDataPointType.DT_BOOL, b"\x01"),
        (3, TuyaBLEDataPointType.DT_VALUE, b"\x00\x00\x00\x05"),
        (4, TuyaBLEDataPointType.DT_ENUM, b"\x00\x01"),
        (5, TuyaBLEDataPointType.DT_STRING, b"hi"),
    ]:
        out += pack(">BBB", dp_id, dp_type.value, len(value))
        out += value
    return bytes(out)


async def test_receive_dp_all_types(h: ProtocolHarness) -> None:
    """Receiving datapoints of every type should update the device."""
    seen: list[TuyaBLEDataPoint] = []
    h.device.register_callback(seen.extend)
    msg = frame_packet0(
        encrypt_payload(session_key(h), 5, 1, 0, TuyaBLECode.FUN_RECEIVE_DP, _dp_data())
    )
    h.notify(msg)
    await asyncio.sleep(0)
    assert {dp.dp_id for dp in seen} == {1, 2, 3, 4, 5}
    dp3 = h.device.datapoints[3]
    assert dp3 is not None
    assert dp3.value == 5
    dp5 = h.device.datapoints[5]
    assert dp5 is not None
    assert dp5.value == "hi"
    dp2 = h.device.datapoints[2]
    assert dp2 is not None
    assert dp2.value is True


async def test_receive_sign_dp(h: ProtocolHarness) -> None:
    """Receiving signed datapoints should be handled."""
    data = pack(">HB", 0, 1) + _dp_data()
    msg = frame_packet0(
        encrypt_payload(session_key(h), 5, 1, 0, TuyaBLECode.FUN_RECEIVE_SIGN_DP, data)
    )
    h.notify(msg)
    await asyncio.sleep(0)
    assert len(h.writes()) >= 1


async def test_receive_time_dp_type0(h: ProtocolHarness) -> None:
    """Receiving a string-encoded time datapoint should respond."""
    timestamp_bytes = b"1700000000123"
    data = pack(">B", 0) + timestamp_bytes + b"\x00\x00\x00" + _dp_data()
    msg = frame_packet0(
        encrypt_payload(session_key(h), 5, 1, 0, TuyaBLECode.FUN_RECEIVE_TIME_DP, data)
    )
    h.notify(msg)
    await asyncio.sleep(0)
    assert len(h.writes()) >= 1


async def test_receive_time_dp_type1(h: ProtocolHarness) -> None:
    """Receiving an int-encoded time datapoint should be handled."""
    data = pack(">BI", 1, 1700000000) + _dp_data()
    msg = frame_packet0(
        encrypt_payload(session_key(h), 5, 1, 0, TuyaBLECode.FUN_RECEIVE_TIME_DP, data)
    )
    h.notify(msg)
    await asyncio.sleep(0)


async def test_receive_sign_time_dp(h: ProtocolHarness) -> None:
    """Receiving signed time datapoints should be handled."""
    data = pack(">HB", 0, 1) + pack(">B", 0) + b"1700000000123" + _dp_data()
    msg = frame_packet0(
        encrypt_payload(
            session_key(h), 5, 1, 0, TuyaBLECode.FUN_RECEIVE_SIGN_TIME_DP, data
        )
    )
    h.notify(msg)
    await asyncio.sleep(0)
    assert len(h.writes()) >= 1


def _device_info_data() -> bytes:
    data = bytearray(46)
    data[0] = 1
    data[1] = 2
    data[2] = 3
    data[3] = 0
    data[4] = 5
    data[5] = 1
    data[6:12] = b"abcdef"
    data[12] = 9
    data[13] = 8
    data[14:46] = b"B" * 32
    return bytes(data)


async def test_device_info_response(h: ProtocolHarness) -> None:
    """A device info response should populate the device attributes."""
    data = _device_info_data()
    msg = frame_packet0(
        encrypt_payload(login_key(h), 4, 1, 0, TuyaBLECode.FUN_SENDER_DEVICE_INFO, data)
    )
    h.notify(msg)
    assert h.device.device_version == "1.2"
    assert h.device.protocol_version == "3.0"
    assert h.device.hardware_version == "9.8"
    assert h.device._protocol_version == 3
    assert h.device._flags == 5
    assert h.device._is_bound is True
    assert h.device._session_key is not None
    assert h.device._auth_key == b"B" * 32


async def test_device_info_too_short(h: ProtocolHarness) -> None:
    """A truncated device info response should raise a length error."""
    msg = frame_packet0(
        encrypt_payload(
            login_key(h), 4, 1, 0, TuyaBLECode.FUN_SENDER_DEVICE_INFO, b"\x00"
        )
    )
    with pytest.raises(TuyaBLEDataLengthError):
        h.notify(msg)


async def test_pair_result_ok(h: ProtocolHarness) -> None:
    """A successful pairing result should mark the device as paired."""
    msg = frame_packet0(
        encrypt_payload(session_key(h), 5, 1, 0, TuyaBLECode.FUN_SENDER_PAIR, b"\x00")
    )
    h.notify(msg)
    assert h.device._is_paired is True


async def test_pair_already_paired(h: ProtocolHarness) -> None:
    """An already-paired result should leave the device paired."""
    msg = frame_packet0(
        encrypt_payload(session_key(h), 5, 1, 0, TuyaBLECode.FUN_SENDER_PAIR, b"\x02")
    )
    h.notify(msg)
    assert h.device._is_paired is True


async def test_pair_failed(h: ProtocolHarness) -> None:
    """A failed pairing result should clear the paired flag."""
    h.device._is_paired = True
    msg = frame_packet0(
        encrypt_payload(session_key(h), 5, 1, 0, TuyaBLECode.FUN_SENDER_PAIR, b"\x01")
    )
    h.notify(msg)
    assert h.device._is_paired is False


async def test_pair_bad_length(h: ProtocolHarness) -> None:
    """A pairing response with a bad length should raise."""
    msg = frame_packet0(
        encrypt_payload(
            session_key(h), 5, 1, 0, TuyaBLECode.FUN_SENDER_PAIR, b"\x00\x00"
        )
    )
    with pytest.raises(TuyaBLEDataLengthError):
        h.notify(msg)


async def test_time1_request(h: ProtocolHarness) -> None:
    """A time1 request should produce a response."""
    msg = frame_packet0(
        encrypt_payload(session_key(h), 5, 1, 0, TuyaBLECode.FUN_RECEIVE_TIME1_REQ, b"")
    )
    h.notify(msg)
    await asyncio.sleep(0)
    assert len(h.writes()) >= 1


async def test_time2_request(h: ProtocolHarness) -> None:
    """A time2 request should produce a response."""
    msg = frame_packet0(
        encrypt_payload(session_key(h), 5, 1, 0, TuyaBLECode.FUN_RECEIVE_TIME2_REQ, b"")
    )
    h.notify(msg)
    await asyncio.sleep(0)
    assert len(h.writes()) >= 1


async def test_unknown_code_ignored(h: ProtocolHarness) -> None:
    """An unknown code should be ignored."""
    # Unpack into a code we do not handle: use a garbage code value.
    raw = make_raw(1, 0, 0x9999, b"\x00")
    msg = frame_packet0(encrypt_raw(session_key(h), 5, raw))
    h.notify(msg)
    # No crash = unhandled code silently ignored.


async def test_crc_error(h: ProtocolHarness) -> None:
    """A bad CRC should raise a CRC error."""
    raw = make_raw(
        1, 0, TuyaBLECode.FUN_SENDER_DEVICE_STATUS.value, b"\x00", crc_override=0
    )
    msg = frame_packet0(encrypt_raw(session_key(h), 5, raw))
    with pytest.raises(TuyaBLEDataCRCError):
        h.notify(msg)


async def test_length_error(h: ProtocolHarness) -> None:
    """A bad length field should raise a length error."""
    raw = pack(">IIHH", 1, 0, TuyaBLECode.FUN_SENDER_DEVICE_STATUS.value, 50)
    raw += b"\x00"
    while len(raw) % 16 != 0:
        raw += b"\x00"
    msg = frame_packet0(encrypt_raw(session_key(h), 5, bytes(raw)))
    with pytest.raises(TuyaBLEDataLengthError):
        h.notify(msg)


def _split_encrypted(encrypted: bytes, chunk: int = 12) -> list[bytes]:
    """Split encrypted bytes into chunks."""
    return [encrypted[i : i + chunk] for i in range(0, len(encrypted), chunk)]


async def test_multiple_packets(h: ProtocolHarness) -> None:
    """Fragmented packets should be reassembled into one notification."""
    seen: list[TuyaBLEDataPoint] = []
    h.device.register_callback(seen.extend)
    encrypted = encrypt_payload(
        session_key(h),
        5,
        1,
        0,
        TuyaBLECode.FUN_RECEIVE_DP,
        pack(">BBB", 1, TuyaBLEDataPointType.DT_BOOL.value, 1) + b"\x01",
    )
    chunks = _split_encrypted(encrypted)
    p0 = pack_varint(0) + pack_varint(len(encrypted)) + pack(">B", 2 << 4) + chunks[0]
    h.notify(p0)
    for num, chunk in enumerate(chunks[1:], start=1):
        h.notify(pack_varint(num) + chunk)
    assert len(seen) == 1


async def test_missing_packet_resets(
    h: ProtocolHarness, caplog: pytest.LogCaptureFixture
) -> None:
    """A missing intermediate packet should reset the input buffer."""
    encrypted = encrypt_payload(
        session_key(h), 5, 1, 0, TuyaBLECode.FUN_RECEIVE_DP, b"\x00" * 4
    )
    chunks = _split_encrypted(encrypted)
    p0 = pack_varint(0) + pack_varint(len(encrypted)) + pack(">B", 2 << 4) + chunks[0]
    h.notify(p0)
    # Skip packet 1, send packet 2 -> "Missing packet".
    h.notify(pack_varint(2) + b"\x00" * 4)
    assert h.device._input_buffer is None


async def test_unexpected_length_resets(h: ProtocolHarness) -> None:
    """An unexpected packet length should reset the input buffer."""
    encrypted = encrypt_payload(
        session_key(h), 5, 1, 0, TuyaBLECode.FUN_RECEIVE_DP, b"\x00" * 4
    )
    chunks = _split_encrypted(encrypted)
    p0 = pack_varint(0) + pack_varint(4) + pack(">B", 2 << 4) + chunks[0] + b"\x00" * 30
    h.notify(p0)
    assert h.device._input_buffer is None


async def test_send_packet_when_expected_disconnect(h: ProtocolHarness) -> None:
    """No packet should be sent when a disconnect is expected."""
    h.device._expected_disconnect = True
    await h.device.update()
    assert h.writes() == []


async def test_unpack_int_out_of_data(h: ProtocolHarness) -> None:
    """Unpacking an int from empty data should raise."""
    with pytest.raises(TuyaBLEDataFormatError):
        h.notify(b"\x80")


async def test_unpack_int_too_many_bytes(h: ProtocolHarness) -> None:
    """Unpacking an oversized varint should raise."""
    with pytest.raises(TuyaBLEDataFormatError):
        h.notify(b"\x80\x80\x80\x80\x80")


async def test_parse_timestamp_format_error(h: ProtocolHarness) -> None:
    """A malformed timestamp should raise a format error."""
    data = pack(">B", 9) + b"\x00" * 4
    msg = frame_packet0(
        encrypt_payload(session_key(h), 5, 1, 0, TuyaBLECode.FUN_RECEIVE_TIME_DP, data)
    )
    with pytest.raises(TuyaBLEDataFormatError):
        h.notify(msg)


async def test_parse_timestamp_length_error(h: ProtocolHarness) -> None:
    """A short timestamp should raise a length error."""
    data = b"\x00\x01"  # type 0 but not enough bytes for 13-char timestamp
    msg = frame_packet0(
        encrypt_payload(session_key(h), 5, 1, 0, TuyaBLECode.FUN_RECEIVE_TIME_DP, data)
    )
    with pytest.raises(TuyaBLEDataLengthError):
        h.notify(msg)


async def test_datapoints_invalid_type(h: ProtocolHarness) -> None:
    """An invalid datapoint type byte should raise a format error."""
    # type byte > DT_BITMAP
    data = pack(">BBB", 1, 9, 1) + b"\x00"
    msg = frame_packet0(
        encrypt_payload(session_key(h), 5, 1, 0, TuyaBLECode.FUN_RECEIVE_DP, data)
    )
    with pytest.raises(TuyaBLEDataFormatError):
        h.notify(msg)


async def test_datapoints_data_length_error(h: ProtocolHarness) -> None:
    """A datapoint value shorter than its length field should raise."""
    # claims 10 bytes value but only 1 present
    data = pack(">BBB", 1, TuyaBLEDataPointType.DT_RAW.value, 10) + b"\x00"
    msg = frame_packet0(
        encrypt_payload(session_key(h), 5, 1, 0, TuyaBLECode.FUN_RECEIVE_DP, data)
    )
    with pytest.raises(TuyaBLEDataLengthError):
        h.notify(msg)


async def test_device_status_response_bad_length(h: ProtocolHarness) -> None:
    """A device status response with a bad length should raise."""
    resp = frame_packet0(
        encrypt_payload(
            session_key(h),
            5,
            1,
            1,
            TuyaBLECode.FUN_SENDER_DEVICE_STATUS,
            b"\x00\x00",
        )
    )
    with pytest.raises(TuyaBLEDataLengthError):
        h.notify(resp)


async def test_unexpected_packet_number(h: ProtocolHarness) -> None:
    """A re-sent packet number should reset the input buffer."""
    # First frame packet0 with expected length, then a lower packet num.
    encrypted = encrypt_payload(
        session_key(h), 5, 1, 0, TuyaBLECode.FUN_RECEIVE_DP, b"\x00" * 4
    )
    p0 = pack_varint(0) + pack_varint(len(encrypted)) + pack(">B", 2 << 4) + encrypted
    h.notify(p0)
    # packet 0 again -> packet_num < expected -> cleans input
    h.notify(p0)
    assert h.device._input_buffer is None


async def test_get_key_invalid_security_flag(h: ProtocolHarness) -> None:
    """An invalid security flag should raise a format error."""
    encrypted = encrypt_payload(
        session_key(h), 9, 1, 0, TuyaBLECode.FUN_SENDER_DEVICE_STATUS, b"\x00"
    )
    msg = frame_packet0(encrypted)
    with pytest.raises(TuyaBLEDataFormatError):
        h.notify(msg)


def test_small_value() -> None:
    """Values < 128 encode to a single byte."""
    assert TuyaBLEProtocol._pack_int(0) == bytearray(b"\x00")
    assert TuyaBLEProtocol._pack_int(1) == bytearray(b"\x01")
    assert TuyaBLEProtocol._pack_int(127) == bytearray(b"\x7f")


def test_two_byte_value() -> None:
    """Values 128-16383 encode to two bytes."""
    assert TuyaBLEProtocol._pack_int(128) == bytearray(b"\x80\x01")
    assert TuyaBLEProtocol._pack_int(200) == bytearray(b"\xc8\x01")
    assert TuyaBLEProtocol._pack_int(16383) == bytearray(b"\xff\x7f")


def test_three_byte_value() -> None:
    """Values 16384+ encode to three bytes."""
    assert TuyaBLEProtocol._pack_int(16384) == bytearray(b"\x80\x80\x01")
    assert TuyaBLEProtocol._pack_int(200000) == bytearray(b"\xc0\x9a\x0c")


def test_time_type_zero() -> None:
    """Time type 0 decodes 13-digit ms string divided by 1000."""
    h = ProtocolHarness()
    # 1700000000000 ms = 1700000000.0 s
    data = b"\x00" + b"1700000000000"
    ts, end = h.device._parse_timestamp(data, 0)
    assert ts == 1700000000.0
    assert end == 14


def test_time_type_one() -> None:
    """Time type 1 decodes 4-byte big-endian seconds."""
    h = ProtocolHarness()
    data = b"\x01" + b"\x00\x00\x00\x05"
    ts, end = h.device._parse_timestamp(data, 0)
    assert ts == 5.0
    assert end == 5


def test_time_type_unknown_raises() -> None:
    """Unknown time type raises TuyaBLEDataFormatError."""
    h = ProtocolHarness()
    data = b"\x99" + b"\x00" * 13
    with pytest.raises(TuyaBLEDataFormatError):
        h.device._parse_timestamp(data, 0)


def test_short_data_raises() -> None:
    """Data shorter than claimed length raises TuyaBLEDataLengthError."""
    h = ProtocolHarness()
    with pytest.raises(TuyaBLEDataLengthError):
        h.device._parse_timestamp(b"\x00", 0)


async def test_handler_tracks_task() -> None:
    """A time1 request handler tracks its send-response task."""
    h = ProtocolHarness()
    h.device._send_response_tasks = set()
    h.device._handle_time1_request(1)
    assert len(h.device._send_response_tasks) == 1
    for task in list(h.device._send_response_tasks):
        await task
    for _ in range(5):
        await asyncio.sleep(0)
    assert len(h.device._send_response_tasks) == 0


async def test_stop_cancels_tracked_response_tasks() -> None:
    """stop() cancels all outstanding send-response tasks."""
    h = ProtocolHarness()
    h.device._client = None
    h.device._send_response_tasks.add(asyncio.create_task(asyncio.sleep(60)))
    await h.device.stop()
    assert all(task.cancelled() for task in h.device._send_response_tasks)
