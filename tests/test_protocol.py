"""Unit tests for the Tuya BLE protocol mixin, driven via public methods."""

# pylint: disable=protected-access, redefined-outer-name
from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
import logging
import struct
from struct import pack
from typing import Any
from unittest.mock import patch

from bleak.exc import BleakError
import pytest

from custom_components.tuya_ble.tuya_ble import TuyaBLEDataPoint
from custom_components.tuya_ble.tuya_ble.const import (
    INPUT_REASSEMBLY_TIMEOUT,
    MAX_INPUT_LENGTH,
    TuyaBLECode,
    TuyaBLEDataPointType,
)
from custom_components.tuya_ble.tuya_ble.exceptions import (
    TuyaBLEDataCRCError,
    TuyaBLEDataFormatError,
    TuyaBLEDataLengthError,
    TuyaBLEDeviceError,
)
from custom_components.tuya_ble.tuya_ble.protocol_mixin import (
    BLE_CONNECTION_EXCEPTIONS,
    BLEAK_EXCEPTIONS,
    TuyaBLEProtocol,
)
from tests.conftest import (
    FakeBleakClient,
    FakeBLEManager,
    make_credentials,
    make_device,
)
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
    seen: list[TuyaBLEDataPoint] = []
    h.device.register_callback(seen.extend)
    data = pack(">HB", 0, 1) + _dp_data()
    msg = frame_packet0(
        encrypt_payload(session_key(h), 5, 1, 0, TuyaBLECode.FUN_RECEIVE_SIGN_DP, data)
    )
    h.notify(msg)
    await asyncio.sleep(0)
    assert len(h.writes()) >= 1
    assert {dp.dp_id for dp in seen} == {1, 2, 3, 4, 5}


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


def _leading_zero_dp() -> bytes:
    """Return a DT_VALUE data point whose value carries leading zero bytes."""
    return pack(">BBB", 3, TuyaBLEDataPointType.DT_VALUE.value, 4) + b"\x00\x00\x00\x64"


async def test_receive_dp_debug_log_keeps_original_width(
    h: ProtocolHarness, caplog: pytest.LogCaptureFixture
) -> None:
    """The receive trace must show the exact wire bytes, not the decoded value."""
    msg = frame_packet0(
        encrypt_payload(
            session_key(h), 5, 1, 0, TuyaBLECode.FUN_RECEIVE_DP, _leading_zero_dp()
        )
    )
    with caplog.at_level(logging.DEBUG):
        h.notify(msg)
        await asyncio.sleep(0)
    assert "raw=00000064" in caplog.text
    assert "decoded=100" in caplog.text


def _receive_variant(code: TuyaBLECode, flags: int) -> bytes:
    """Return a receive payload carrying one datapoint for the given command."""
    dp = _leading_zero_dp()
    if code is TuyaBLECode.FUN_RECEIVE_DP:
        return dp
    if code is TuyaBLECode.FUN_RECEIVE_SIGN_DP:
        return pack(">HB", 7, flags) + dp
    if code is TuyaBLECode.FUN_RECEIVE_TIME_DP:
        return pack(">B", 0) + b"1700000000123" + b"\x00\x00\x00" + dp
    return pack(">HB", 7, flags) + pack(">B", 0) + b"1700000000123" + dp


@pytest.mark.parametrize(
    ("code", "flags"),
    [
        (TuyaBLECode.FUN_RECEIVE_DP, 0),
        (TuyaBLECode.FUN_RECEIVE_SIGN_DP, 0x11),
        (TuyaBLECode.FUN_RECEIVE_TIME_DP, 0),
        (TuyaBLECode.FUN_RECEIVE_SIGN_TIME_DP, 0x22),
    ],
)
async def test_receive_debug_log_format_is_uniform(
    h: ProtocolHarness,
    caplog: pytest.LogCaptureFixture,
    code: TuyaBLECode,
    flags: int,
) -> None:
    """Every receive command must produce the same raw-bytes trace format."""
    msg = frame_packet0(
        encrypt_payload(session_key(h), 5, 1, 0, code, _receive_variant(code, flags))
    )
    with caplog.at_level(logging.DEBUG):
        h.notify(msg)
        await asyncio.sleep(0)
    assert f"id=3 type=DT_VALUE flags=0x{flags:02x} raw=00000064 decoded=100" in (
        caplog.text
    )


async def test_receive_debug_log_suppressed_when_debug_disabled(
    h: ProtocolHarness, caplog: pytest.LogCaptureFixture
) -> None:
    """The raw-bytes trace must not be emitted when DEBUG is off."""
    msg = frame_packet0(
        encrypt_payload(
            session_key(h), 5, 1, 0, TuyaBLECode.FUN_RECEIVE_DP, _leading_zero_dp()
        )
    )
    logger = "custom_components.tuya_ble.tuya_ble.protocol_mixin"
    with caplog.at_level(logging.INFO, logger=logger):
        h.notify(msg)
        await asyncio.sleep(0)
    assert "raw=" not in caplog.text
    assert "Received DP" not in caplog.text


async def test_received_raw_bytes_reach_the_data_point(h: ProtocolHarness) -> None:
    """The parser must hand the untouched wire bytes to the data point."""
    msg = frame_packet0(
        encrypt_payload(
            session_key(h), 5, 1, 0, TuyaBLECode.FUN_RECEIVE_DP, _leading_zero_dp()
        )
    )
    h.notify(msg)
    await asyncio.sleep(0)
    dp = h.device.datapoints[3]
    assert dp is not None
    assert dp.value == 100
    assert dp.raw_value == b"\x00\x00\x00\x64"


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


async def test_safe_notification_handler_discards_length_error(
    h: ProtocolHarness, caplog: pytest.LogCaptureFixture
) -> None:
    """Do not leak malformed device frames into the BLE transport callback."""
    raw = pack(">IIHH", 1, 0, TuyaBLECode.FUN_SENDER_DEVICE_STATUS.value, 50)
    raw += b"\x00"
    while len(raw) % 16 != 0:
        raw += b"\x00"
    msg = frame_packet0(encrypt_raw(session_key(h), 5, bytes(raw)))

    h.device._safe_notification_handler(None, bytearray(msg))

    assert h.device._input_buffer is None
    assert "Ignoring malformed BLE notification" in caplog.text


async def test_safe_notification_handler_discards_non_aligned_payload(
    h: ProtocolHarness, caplog: pytest.LogCaptureFixture
) -> None:
    """A device-reported length that isn't block-aligned must not leak a ValueError."""
    raw = pack(">IIHH", 1, 0, TuyaBLECode.FUN_SENDER_DEVICE_STATUS.value, 0)
    raw += b"\x00" * 8
    payload = b"\x05" + b"\x00" * 16 + bytes(raw)[:8]
    msg = frame_packet0(payload)

    h.device._safe_notification_handler(None, bytearray(msg))

    assert h.device._input_buffer is None
    assert "Ignoring malformed BLE notification" in caplog.text


async def test_safe_notification_handler_discards_short_decrypted_buffer(
    h: ProtocolHarness, caplog: pytest.LogCaptureFixture
) -> None:
    """A decrypted buffer shorter than the header must not leak a struct.error."""
    payload = b"\x05" + b"\x00" * 16
    msg = frame_packet0(payload)

    h.device._safe_notification_handler(None, bytearray(msg))

    assert h.device._input_buffer is None
    assert "Ignoring malformed BLE notification" in caplog.text


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


# ---------------------------------------------------------------------------
# Regression tests for the P0/P1 protocol fixes (issue #66)
# ---------------------------------------------------------------------------


async def test_exact_length_frame_crc_is_verified(h: ProtocolHarness) -> None:
    """A frame of exactly ``length + 12 + 2`` bytes must still have its CRC checked.

    The integrity check used to be guarded by ``raw_length > data_end_pos``, so a
    frame that needed no padding - and so had exactly 2 bytes of CRC and nothing
    else - fell through it and was accepted unverified.
    """
    # 2-byte payload: header(12) + data(2) + crc(2) = 16, already block-aligned.
    good = make_raw(1, 0, TuyaBLECode.FUN_SENDER_DEVICE_STATUS.value, b"XX")
    assert len(good) == 14 + 2

    parsed = h.device._validate_and_parse_packet(good)
    assert parsed is not None
    assert parsed[3] == b"XX"

    bad = make_raw(
        1, 0, TuyaBLECode.FUN_SENDER_DEVICE_STATUS.value, b"XX", crc_override=0
    )
    assert len(bad) == 14 + 2
    with pytest.raises(TuyaBLEDataCRCError):
        h.device._validate_and_parse_packet(bad)


async def test_validate_packet_requires_crc(h: ProtocolHarness) -> None:
    """A frame too short to hold its CRC is rejected as a length error."""
    raw = pack(">IIHH", 1, 0, TuyaBLECode.FUN_SENDER_DEVICE_STATUS.value, 2) + b"XX"
    with pytest.raises(TuyaBLEDataLengthError):
        h.device._validate_and_parse_packet(raw)


async def test_validate_packet_shorter_than_header(h: ProtocolHarness) -> None:
    """A decrypted buffer shorter than the 12-byte header is rejected."""
    with pytest.raises(TuyaBLEDataLengthError):
        h.device._validate_and_parse_packet(b"\x00" * 11)


async def test_announced_length_over_maximum_is_rejected(
    h: ProtocolHarness, caplog: pytest.LogCaptureFixture
) -> None:
    """A device announcing an absurd length must not start an unbounded buffer."""
    with caplog.at_level(logging.WARNING):
        h.device._safe_notification_handler(
            None,
            bytearray(pack_varint(0) + pack_varint(0x0FFFFFFF) + pack(">B", 2 << 4)),
        )

    assert h.device._input_buffer is None
    assert "exceeds" in caplog.text


async def test_maximum_length_is_accepted(h: ProtocolHarness) -> None:
    """The upper bound itself is a legal announcement."""
    encrypted = encrypt_payload(
        session_key(h), 5, 1, 0, TuyaBLECode.FUN_RECEIVE_DP, b"\x00" * 4
    )
    p0 = (
        pack_varint(0)
        + pack_varint(MAX_INPUT_LENGTH)
        + pack(">B", 2 << 4)
        + encrypted[:5]
    )
    h.device._notification_handler(None, bytearray(p0))
    assert h.device._input_expected_length == MAX_INPUT_LENGTH


async def test_stale_partial_reassembly_is_dropped(
    h: ProtocolHarness, caplog: pytest.LogCaptureFixture
) -> None:
    """A fragment that never completes must be discarded, not kept forever."""
    encrypted = encrypt_payload(
        session_key(h), 5, 1, 0, TuyaBLECode.FUN_RECEIVE_DP, b"\x00" * 40
    )
    h.notify(pack_varint(0) + pack_varint(len(encrypted)) + pack(">B", 2 << 4))
    assert h.device._input_buffer is not None
    assert h.device._input_expected_packet_num == 1

    # Simulate the deadline having passed while the device went silent.
    h.device._input_reassembly_deadline -= INPUT_REASSEMBLY_TIMEOUT + 1
    with caplog.at_level(logging.WARNING):
        h.device._notification_handler(None, bytearray(pack_varint(1) + b"\x00" * 4))

    assert h.device._input_buffer is None
    assert "Timed out waiting for packet" in caplog.text


async def test_duplicate_packet_num_resyncs(
    h: ProtocolHarness, caplog: pytest.LogCaptureFixture
) -> None:
    """A duplicate packet 0 must reset rather than fall through and append."""
    encrypted = encrypt_payload(
        session_key(h), 5, 1, 0, TuyaBLECode.FUN_RECEIVE_DP, b"\x00" * 40
    )
    h.notify(pack_varint(0) + pack_varint(len(encrypted)) + pack(">B", 2 << 4))
    assert h.device._input_expected_packet_num == 1

    with caplog.at_level(logging.DEBUG):
        h.device._notification_handler(None, bytearray(pack_varint(0) + b"\x00" * 4))

    assert h.device._input_buffer is None
    assert "Received fresh packet 0, restarting notification reassembly" in caplog.text


async def test_fresh_packet_zero_restarts_and_dispatches(
    h: ProtocolHarness,
) -> None:
    """A fresh packet 0 mid-reassembly restarts and the new message completes."""
    seen: list[TuyaBLEDataPoint] = []
    h.device.register_callback(seen.extend)

    stale = encrypt_payload(
        session_key(h), 5, 1, 0, TuyaBLECode.FUN_RECEIVE_DP, b"\x00" * 40
    )
    h.notify(pack_varint(0) + pack_varint(len(stale)) + pack(">B", 2 << 4))
    assert h.device._input_buffer is not None
    assert h.device._input_expected_packet_num == 1

    encrypted = encrypt_payload(
        session_key(h),
        5,
        2,
        0,
        TuyaBLECode.FUN_RECEIVE_DP,
        pack(">BBB", 1, TuyaBLEDataPointType.DT_BOOL.value, 1) + b"\x01",
    )
    chunks = _split_encrypted(encrypted)
    h.notify(
        pack_varint(0) + pack_varint(len(encrypted)) + pack(">B", 2 << 4) + chunks[0]
    )
    for num, chunk in enumerate(chunks[1:], start=1):
        h.notify(pack_varint(num) + chunk)

    assert len(seen) == 1


async def test_failed_send_does_not_leak_response_future(
    h: ProtocolHarness,
) -> None:
    """A failed send must not leave its future in ``_input_expected_responses``."""
    h.device._input_expected_responses = {}

    async def fail(*_args: object, **_kwargs: object) -> None:
        raise BleakError("test")

    with (
        patch.object(h.device, "_int_send_packet_while_connected", side_effect=fail),
        pytest.raises(BleakError),
    ):
        await h.device._send_packet_while_connected(
            TuyaBLECode.FUN_SENDER_DEVICE_STATUS, b"", 0, True
        )

    assert h.device._input_expected_responses == {}


async def test_failed_packet_build_does_not_leak_response_future(
    h: ProtocolHarness,
) -> None:
    """A ``struct.error`` while framing must not leak the registered future."""
    h.device._input_expected_responses = {}

    with (
        patch.object(
            h.device,
            "_build_packets",
            side_effect=struct.error("out of range"),
        ),
        pytest.raises(struct.error),
    ):
        await h.device._send_packet_while_connected(
            TuyaBLECode.FUN_SENDER_DEVICE_STATUS, b"", 0, True
        )

    assert h.device._input_expected_responses == {}


async def test_stop_fails_pending_response_futures(h: ProtocolHarness) -> None:
    """stop() must not leave a sender waiting for its full response timeout."""
    h.device._client = None
    h.device._expected_disconnect = False
    pending: asyncio.Future[int] = asyncio.Future()
    h.device._input_expected_responses[7] = pending

    await h.device.stop()

    assert h.device._input_expected_responses == {}
    assert pending.done()


async def test_bleak_exceptions_excludes_attribute_error() -> None:
    """A programming error must not be classified as a transient BLE failure."""
    assert AttributeError not in BLEAK_EXCEPTIONS
    assert AttributeError not in BLE_CONNECTION_EXCEPTIONS
    assert BleakError in BLE_CONNECTION_EXCEPTIONS


async def test_attribute_error_is_not_swallowed_by_connect() -> None:
    """A real bug in a connect step must surface instead of retrying 100 times."""
    dev = make_device(manager=FakeBLEManager(make_credentials()))
    dev._client = FakeBleakClient(is_connected=True)  # type: ignore[assignment]
    dev._is_paired = True

    async def fail(*_args: object, **_kwargs: object) -> None:
        raise AttributeError("typo")

    with (
        patch.object(dev, "_try_establish_connection", side_effect=fail),
        pytest.raises(AttributeError),
    ):
        await dev._connect_with_retries()


def test_lost_reassembly_buffer_is_reported(
    h: ProtocolHarness, caplog: pytest.LogCaptureFixture
) -> None:
    """A vanished reassembly buffer must be reported, not raised on."""
    # A packet_num > 0 with no buffer is only reachable if the buffer was dropped
    # out-of-band; verify the guard reports it instead of appending to None.
    h.device._input_expected_packet_num = 1
    h.device._input_buffer = None
    h.device._input_reassembly_deadline = float("inf")

    with caplog.at_level(logging.WARNING):
        h.device._notification_handler(None, bytearray(pack_varint(1) + b"\x00" * 4))

    assert "Buffer not initialized" in caplog.text
    assert h.device._input_buffer is None
