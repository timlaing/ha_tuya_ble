"""Protocol 4 (FD50) handshake and datapoint reports, driven over fake clients.

Byte layouts follow the Tuya BLE SDK mirror pinned in
``docs/BLE_ADVERTISEMENT_DECODING.md`` (revision 815c071):
``tuya_ble_data_handler.c`` for the device-info, pair, status-query and report
acknowledgement handlers, ``tuya_ble_api.c`` for the 0x8006/0x8007 report
envelopes. All vectors are synthetic.
"""

# pylint: disable=protected-access, redefined-outer-name
from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
import hashlib
import logging
from struct import pack
from unittest.mock import patch

from bleak.exc import BleakError
import pytest

from custom_components.tuya_ble.tuya_ble import TuyaBLEDataPoint, TuyaBLEDevice
from custom_components.tuya_ble.tuya_ble.const import (
    CHARACTERISTIC_NOTIFY,
    CHARACTERISTIC_NOTIFY_V4,
    CHARACTERISTIC_WRITE,
    CHARACTERISTIC_WRITE_V4,
    TuyaBLECode,
    TuyaBLEDataPointType,
)
from custom_components.tuya_ble.tuya_ble.manager import TuyaBLEDeviceCredentials
from tests.conftest import FakeBleakClient, make_credentials, make_device
from tests.protocol_harness import (
    ProtocolHarness,
    decode_frame,
    encrypt_payload,
    fragment,
    unpack_varint,
    v4_advertisement,
)

V4_CHARACTERISTICS = (CHARACTERISTIC_NOTIFY_V4, CHARACTERISTIC_WRITE_V4)
CLASSIC_CHARACTERISTICS = (CHARACTERISTIC_NOTIFY, CHARACTERISTIC_WRITE)

DP_RECEIVE_V4 = TuyaBLECode.FUN_RECEIVE_DP_V4
DP_RECEIVE_TIME_V4 = TuyaBLECode.FUN_RECEIVE_TIME_DP_V4


def klv(dp_id: int, dp_type: TuyaBLEDataPointType, value: bytes) -> bytes:
    """Encode one protocol-4 datapoint with its 2-byte big-endian length."""
    return pack(">BBH", dp_id, dp_type.value, len(value)) + value


def report(
    body: bytes, *, version: int = 0, sn: int = 7, type_byte: int = 0, mode: int = 0
) -> bytes:
    """Prefix a report body with the 7-byte protocol-4 report header."""
    return pack(">BIBB", version, sn, type_byte, mode) + body


def soil_sensor_datapoints() -> bytes:
    """Return a synthetic soil-sensor report body: moisture, temp, battery."""
    return (
        klv(3, TuyaBLEDataPointType.DT_VALUE, pack(">i", 41))
        + klv(5, TuyaBLEDataPointType.DT_VALUE, pack(">i", 215))
        + klv(14, TuyaBLEDataPointType.DT_ENUM, b"\x01")
        + klv(15, TuyaBLEDataPointType.DT_VALUE, pack(">i", 87))
    )


def session_key(h: ProtocolHarness) -> bytes:
    """Return the device session key."""
    assert h.device._session_key is not None
    return h.device._session_key


def deliver(h: ProtocolHarness, code: TuyaBLECode, data: bytes, seq: int = 1) -> None:
    """Deliver one encrypted, fragmented frame from the device."""
    encrypted = encrypt_payload(session_key(h), 5, seq, 0, code, data)
    for packet in fragment(encrypted, protocol_version=4):
        h.notify(packet)


async def drain_responses(device: TuyaBLEDevice) -> None:
    """Wait for every outstanding acknowledgement task to finish."""
    await asyncio.gather(*tuple(device._send_response_tasks))


def written_frames(h: ProtocolHarness) -> list[tuple[int, int, int, bytes, int]]:
    """Decode every frame written to the fake client."""
    frames: list[tuple[int, int, int, bytes, int]] = []
    current: list[bytes] = []
    for packet in h.writes():
        packet_num, _ = unpack_varint(packet, 0)
        if packet_num == 0 and current:
            frames.append(decode_frame({5: session_key(h)}, current))
            current = []
        current.append(packet)
    if current:
        frames.append(decode_frame({5: session_key(h)}, current))
    return frames


@pytest.fixture
async def h() -> ProtocolHarness:
    """Return a paired protocol-4 harness wired like a real connection.

    Notifications go through the same guarded handler ``start_notify``
    registers in production, so a malformed report is logged and dropped
    rather than raised into the test.
    """
    harness = ProtocolHarness(protocol_version=4)
    await harness.client.start_notify(
        CHARACTERISTIC_NOTIFY_V4, harness.device._safe_notification_handler
    )
    return harness


# ---------------------------------------------------------------------------
# Handshake requests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("protocol_version", "payload"),
    [(4, b"\x00\x14"), (3, b""), (2, b"")],
)
async def test_device_info_request_payload(
    protocol_version: int, payload: bytes
) -> None:
    """Protocol 4 sends the 2-byte data length; v2/v3 keep the empty request."""
    harness = ProtocolHarness(protocol_version=protocol_version)
    sent: list[tuple[TuyaBLECode, bytes]] = []

    async def send(
        code: TuyaBLECode, data: bytes, response_to: int, wait: bool
    ) -> bool:
        sent.append((code, data))
        return True

    with patch.object(harness.device, "_send_packet_while_connected", side_effect=send):
        assert await harness.device._try_send_device_info()
    assert sent == [(TuyaBLECode.FUN_SENDER_DEVICE_INFO, payload)]


@pytest.mark.parametrize(
    ("offered", "notify_char", "write_char"),
    [
        (V4_CHARACTERISTICS, CHARACTERISTIC_NOTIFY_V4, CHARACTERISTIC_WRITE_V4),
        (CLASSIC_CHARACTERISTICS, CHARACTERISTIC_NOTIFY, CHARACTERISTIC_WRITE),
        (
            (CHARACTERISTIC_NOTIFY_V4, *CLASSIC_CHARACTERISTICS),
            CHARACTERISTIC_NOTIFY,
            CHARACTERISTIC_WRITE,
        ),
        (
            (CHARACTERISTIC_WRITE_V4, *CLASSIC_CHARACTERISTICS),
            CHARACTERISTIC_NOTIFY,
            CHARACTERISTIC_WRITE,
        ),
    ],
)
async def test_v4_characteristics_follow_gatt_table(
    offered: Iterable[str], notify_char: str, write_char: str
) -> None:
    """The FD50 pair is used only when the device offers both characteristics."""
    harness = ProtocolHarness(protocol_version=4)
    client = FakeBleakClient(characteristics=offered)
    harness.device._client = client  # type: ignore[assignment]

    assert await harness.device._try_start_notifications()
    await harness.device._send_response(DP_RECEIVE_V4, b"", 1)
    await harness.device._execute_disconnect()

    assert client.notify_char == notify_char
    assert set(client.write_targets) == {write_char}
    assert client.stopped == [notify_char]


@pytest.mark.parametrize("protocol_version", [2, 3])
async def test_classic_protocols_never_look_up_characteristics(
    protocol_version: int,
) -> None:
    """v2/v3 keep the classic pair even when the FD50 pair is offered."""
    harness = ProtocolHarness(protocol_version=protocol_version)
    client = FakeBleakClient(
        characteristics=(*V4_CHARACTERISTICS, *CLASSIC_CHARACTERISTICS)
    )
    harness.device._client = client  # type: ignore[assignment]

    assert await harness.device._try_start_notifications()
    await harness.device._send_response(TuyaBLECode.FUN_RECEIVE_DP, b"", 1)

    assert not client.services.lookups
    assert client.notify_char == CHARACTERISTIC_NOTIFY
    assert set(client.write_targets) == {CHARACTERISTIC_WRITE}


async def test_characteristic_selection_is_redone_per_connection() -> None:
    """A later connection without the FD50 pair falls back to the classic one."""
    harness = ProtocolHarness(protocol_version=4)
    harness.device._client = FakeBleakClient(  # type: ignore[assignment]
        characteristics=V4_CHARACTERISTICS
    )
    assert await harness.device._try_start_notifications()

    classic = FakeBleakClient()
    harness.device._client = classic  # type: ignore[assignment]
    assert await harness.device._try_start_notifications()

    assert classic.notify_char == CHARACTERISTIC_NOTIFY


async def test_characteristic_lookup_failure_retries_connection() -> None:
    """A GATT lookup error is a failed attempt, like a failed start_notify."""
    harness = ProtocolHarness(protocol_version=4)
    client = FakeBleakClient()
    harness.device._client = client  # type: ignore[assignment]

    with patch.object(
        client.services, "get_characteristic", side_effect=BleakError("no services")
    ):
        assert not await harness.device._try_start_notifications()

    assert harness.device._client is None
    assert client.notify_handler is None


async def test_v4_selection_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    """The chosen characteristic pair appears in the debug log."""
    harness = ProtocolHarness(protocol_version=4)
    harness.device._client = FakeBleakClient(  # type: ignore[assignment]
        characteristics=V4_CHARACTERISTICS
    )
    with caplog.at_level(logging.DEBUG):
        assert await harness.device._try_start_notifications()
    assert (
        f"Using notify {CHARACTERISTIC_NOTIFY_V4} and write "
        f"{CHARACTERISTIC_WRITE_V4} for protocol 4"
    ) in caplog.text


# ---------------------------------------------------------------------------
# 0x8006 / 0x8007 datapoint reports
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("type_byte", "mode"), [(0, 0), (1, 2)])
async def test_v4_report_updates_datapoints_and_acknowledges(
    h: ProtocolHarness, type_byte: int, mode: int
) -> None:
    """A report stores every datapoint and is answered with header + status 0."""
    seen: list[list[TuyaBLEDataPoint]] = []
    h.device.register_callback(seen.append)
    data = report(
        soil_sensor_datapoints(), sn=0x01020304, type_byte=type_byte, mode=mode
    )

    deliver(h, DP_RECEIVE_V4, data, seq=9)
    await drain_responses(h.device)

    assert [[dp.dp_id for dp in batch] for batch in seen] == [[3, 5, 14, 15]]
    values = {dp.dp_id: dp.value for dp in seen[0]}
    assert values == {3: 41, 5: 215, 14: 1, 15: 87}
    assert all(dp.flags == 0 for dp in seen[0])
    frames = written_frames(h)
    assert len(frames) == 1
    _, response_to, code, ack, flag = frames[0]
    assert (code, response_to, flag) == (DP_RECEIVE_V4.value, 9, 5)
    assert ack == data[:7] + b"\x00"
    assert len(ack) == 8


async def test_v4_report_decodes_every_type(h: ProtocolHarness) -> None:
    """Every datapoint type decodes like the v3 parser does."""
    body = (
        klv(1, TuyaBLEDataPointType.DT_RAW, b"\x00\xab")
        + klv(2, TuyaBLEDataPointType.DT_BOOL, b"\x01")
        + klv(3, TuyaBLEDataPointType.DT_VALUE, pack(">i", -40))
        + klv(4, TuyaBLEDataPointType.DT_STRING, b"wet")
        + klv(5, TuyaBLEDataPointType.DT_ENUM, b"\x02")
        + klv(6, TuyaBLEDataPointType.DT_BITMAP, b"\x05")
    )
    deliver(h, DP_RECEIVE_V4, report(body))
    await drain_responses(h.device)

    values = {
        dp_id: dp.value
        for dp_id in range(1, 7)
        if (dp := h.device.datapoints[dp_id]) is not None
    }
    assert values == {1: b"\x00\xab", 2: True, 3: -40, 4: "wet", 5: 2, 6: b"\x05"}


async def test_v4_report_value_longer_than_one_byte_length(
    h: ProtocolHarness,
) -> None:
    """A value over 255 bytes needs the 2-byte length and spans many fragments."""
    blob = bytes(range(256)) + bytes(44)
    deliver(h, DP_RECEIVE_V4, report(klv(20, TuyaBLEDataPointType.DT_RAW, blob)))
    await drain_responses(h.device)

    dp = h.device.datapoints[20]
    assert dp is not None
    assert dp.value == blob


async def test_v4_report_without_response_flag_gets_no_answer(
    h: ProtocolHarness, caplog: pytest.LogCaptureFixture
) -> None:
    """Bit 7 of the type byte means the device wants no acknowledgement."""
    with caplog.at_level(logging.DEBUG):
        deliver(h, DP_RECEIVE_V4, report(soil_sensor_datapoints(), type_byte=0x80))
        await drain_responses(h.device)

    dp = h.device.datapoints[5]
    assert dp is not None
    assert dp.value == 215
    assert not h.writes()
    assert "FUN_RECEIVE_DP_V4 #1 needs no acknowledgement" in caplog.text


async def test_v4_report_raw_payload_is_logged(
    h: ProtocolHarness, caplog: pytest.LogCaptureFixture
) -> None:
    """The undecoded report reaches the debug log, as do per-DP traces."""
    data = report(klv(5, TuyaBLEDataPointType.DT_VALUE, pack(">i", 215)))
    with caplog.at_level(logging.DEBUG):
        deliver(h, DP_RECEIVE_V4, data)
        await drain_responses(h.device)

    assert f"Received FUN_RECEIVE_DP_V4 payload: {data.hex()}" in caplog.text
    assert "Received DP id=5 type=DT_VALUE flags=0x00 raw=000000d7" in caplog.text


VALID_FIRST = klv(3, TuyaBLEDataPointType.DT_VALUE, pack(">i", 41))


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(b"\x00\x00\x00\x00\x07\x00", id="short-header"),
        pytest.param(report(VALID_FIRST, version=1), id="unknown-version"),
        pytest.param(report(b""), id="no-datapoints"),
        pytest.param(report(VALID_FIRST + b"\x05\x02\x00"), id="partial-entry"),
        pytest.param(
            report(VALID_FIRST + pack(">BBH", 5, 2, 4) + b"\x00\x01"),
            id="value-past-end",
        ),
        pytest.param(
            report(VALID_FIRST + pack(">BBH", 5, 6, 1) + b"\x00"),
            id="unknown-type",
        ),
        pytest.param(
            report(VALID_FIRST + klv(5, TuyaBLEDataPointType.DT_STRING, b"\xff")),
            id="invalid-utf8",
        ),
    ],
)
async def test_malformed_v4_report_changes_nothing(
    h: ProtocolHarness, data: bytes, caplog: pytest.LogCaptureFixture
) -> None:
    """A malformed report stores no datapoint, fires nothing and gets no answer."""
    seen: list[list[TuyaBLEDataPoint]] = []
    h.device.register_callback(seen.append)

    with caplog.at_level(logging.DEBUG):
        deliver(h, DP_RECEIVE_V4, data)
        await drain_responses(h.device)

    assert h.device.datapoints[3] is None
    assert not seen
    assert not h.writes()
    assert f"payload: {data.hex()}" in caplog.text
    assert "Ignoring malformed BLE notification" in caplog.text


@pytest.mark.parametrize(
    ("time_block", "timestamp"),
    [
        pytest.param(pack(">BI", 1, 1_700_000_000), 1_700_000_000.0, id="unix"),
        pytest.param(b"\x00" + b"1700000000123", 1_700_000_000.123, id="ms-string"),
    ],
)
async def test_v4_timed_report_uses_device_timestamp(
    h: ProtocolHarness, time_block: bytes, timestamp: float
) -> None:
    """0x8007 carries its time block after the header and before the datapoints."""
    data = report(time_block + soil_sensor_datapoints(), sn=11)
    deliver(h, DP_RECEIVE_TIME_V4, data, seq=4)
    await drain_responses(h.device)

    dp = h.device.datapoints[15]
    assert dp is not None
    assert dp.value == 87
    assert dp.timestamp == pytest.approx(timestamp)
    frames = written_frames(h)
    assert len(frames) == 1
    _, response_to, code, ack, _flag = frames[0]
    assert (code, response_to) == (DP_RECEIVE_TIME_V4.value, 4)
    assert ack == data[:7] + b"\x00"


@pytest.mark.parametrize(
    "time_block",
    [
        pytest.param(pack(">BI", 2, 1_700_000_000), id="unknown-time-type"),
        pytest.param(b"\x01\x00\x00", id="truncated-unix"),
        pytest.param(b"\x00" + b"17000000", id="truncated-ms-string"),
    ],
)
async def test_malformed_v4_timed_report_changes_nothing(
    h: ProtocolHarness, time_block: bytes
) -> None:
    """A bad time block rejects the whole timed report.

    A valid datapoint follows each time block, so only the time block itself
    can be what rejects the report.
    """
    deliver(h, DP_RECEIVE_TIME_V4, report(time_block + VALID_FIRST))
    await drain_responses(h.device)

    assert not h.writes()
    assert h.device.datapoints[3] is None


async def test_v4_timed_report_without_time_block_changes_nothing(
    h: ProtocolHarness,
) -> None:
    """A timed report that ends after its header carries no time type."""
    deliver(h, DP_RECEIVE_TIME_V4, report(b""))
    await drain_responses(h.device)

    assert not h.writes()
    assert not list(h.device.datapoints.values())


# ---------------------------------------------------------------------------
# End to end over a firmware double
# ---------------------------------------------------------------------------

# Commands the SDK accepts before a pair request has bonded the link
# (tuya_ble_commonData_rx_proc), limited to the ones this integration sends.
HANDSHAKE_CODES = frozenset({
    TuyaBLECode.FUN_SENDER_DEVICE_INFO.value,
    TuyaBLECode.FUN_SENDER_PAIR.value,
})
REPORT_CODES = frozenset({DP_RECEIVE_V4.value, DP_RECEIVE_TIME_V4.value})
TIME_REQUEST = TuyaBLECode.FUN_RECEIVE_TIME1_REQ.value


def device_info_response(srand: bytes) -> bytes:
    """Build a protocol-4.2 device-info response (only the parsed prefix)."""
    data = bytearray(96)
    data[0:2] = b"\x01\x00"  # firmware version
    data[2:4] = b"\x04\x02"  # protocol version 4.2
    data[4] = 0x00  # flags: registered over BLE, no advanced encryption
    data[5] = 1  # bound
    data[6:12] = srand
    data[12:14] = b"\x01\x00"  # hardware version
    return bytes(data)


class FakeV4Firmware(FakeBleakClient):
    """Device side of the protocol-4 handshake, following the SDK handlers.

    It decodes each frame the integration writes and answers like the SDK:

    - a sequence number that does not increase disconnects;
    - commands other than device info and pair are dropped until a pair
      request has bonded the link;
    - device info is answered only for a 2-byte payload, otherwise the
      device disconnects;
    - pair is answered with ``2`` (already bound), followed by the default
      configuration's 0x8011 time request;
    - the status query is answered with ``0``, followed by an unsolicited
      0x8006 report;
    - a report acknowledgement that is not 8 bytes is ignored.

    Anything the SDK would reject is recorded in ``violations``.
    """

    def __init__(
        self,
        credentials: TuyaBLEDeviceCredentials,
        report_data: bytes,
        characteristics: Iterable[str] = V4_CHARACTERISTICS,
    ) -> None:
        super().__init__(characteristics=characteristics)
        local_key = credentials.local_key[:6].encode()
        srand = b"rand42"
        self._uuid = credentials.uuid.encode()
        self._srand = srand
        self._keys = {
            4: hashlib.md5(local_key, usedforsecurity=False).digest(),
            5: hashlib.md5(local_key + srand, usedforsecurity=False).digest(),
        }
        self._report = report_data
        self._seq = 0
        self._last_received_seq = 0
        self._bonded = False
        self._frame: list[bytes] = []
        self._remaining = 0
        self.report_seq = 0
        self.received: list[tuple[int, int, bytes, int]] = []
        self.violations: list[str] = []
        self.acknowledged = asyncio.Event()

    async def write_gatt_char(
        self, gatt_char: str, data: bytes, response: bool
    ) -> None:
        """Record the fragment and answer once a whole frame has arrived."""
        await super().write_gatt_char(gatt_char, data, response)
        packet = bytes(data)
        packet_num, pos = unpack_varint(packet, 0)
        if packet_num == 0:
            self._frame = []
            self._remaining, pos = unpack_varint(packet, pos)
            pos += 1
        self._frame.append(packet)
        self._remaining -= len(packet) - pos
        if self._remaining == 0:
            self._receive(*decode_frame(self._keys, self._frame))

    def _receive(
        self, seq_num: int, response_to: int, code: int, data: bytes, flag: int
    ) -> None:
        self.received.append((code, response_to, data, flag))
        if seq_num <= self._last_received_seq:
            self.violations.append(f"sequence number {seq_num} did not increase")
            self._is_connected = False
            return
        self._last_received_seq = seq_num
        if not self._bonded and code not in HANDSHAKE_CODES:
            self.violations.append(f"0x{code:04x} before bonding")
            return
        if code in REPORT_CODES and len(data) != 8:
            self.violations.append(f"0x{code:04x} acknowledgement of {len(data)}")
            return
        self._answer(seq_num, code, data, flag)

    def _answer(self, seq_num: int, code: int, data: bytes, flag: int) -> None:
        match code:
            case TuyaBLECode.FUN_SENDER_DEVICE_INFO.value if len(data) == 2:
                self._reply(code, seq_num, device_info_response(self._srand), flag)
            case TuyaBLECode.FUN_SENDER_DEVICE_INFO.value:
                self._is_connected = False
            case TuyaBLECode.FUN_SENDER_PAIR.value:
                assert data[:16] == self._uuid
                self._bonded = True
                self._reply(code, seq_num, b"\x02", 5)
                self._reply(TIME_REQUEST, 0, b"", 5)
            case TuyaBLECode.FUN_SENDER_DEVICE_STATUS.value:
                self._reply(code, seq_num, b"\x00", 5)
                self.report_seq = self._reply(DP_RECEIVE_V4.value, 0, self._report, 5)
            case DP_RECEIVE_V4.value:
                self.acknowledged.set()

    def _reply(self, code: int, response_to: int, data: bytes, flag: int) -> int:
        self._seq += 1
        encrypted = encrypt_payload(
            self._keys[flag], flag, self._seq, response_to, TuyaBLECode(code), data
        )
        loop = asyncio.get_running_loop()
        for packet in fragment(encrypted, protocol_version=4):
            loop.call_soon(self._notify, bytearray(packet))
        return self._seq

    def _notify(self, packet: bytearray) -> None:
        assert self.notify_handler is not None
        self.notify_handler(CHARACTERISTIC_NOTIFY_V4, packet)


def make_v4_device(credentials: TuyaBLEDeviceCredentials) -> TuyaBLEDevice:
    """Build a device advertising a bound FD50 identity for ``credentials``."""
    device = make_device()
    device.set_ble_device_and_advertisement_data(
        device._ble_device, v4_advertisement(uuid=credentials.uuid.encode())
    )
    device.initialize_with_credentials(credentials)
    return device


async def run_session(
    device: TuyaBLEDevice,
    firmware: FakeV4Firmware,
    while_connecting: Callable[[], None] | None = None,
) -> None:
    """Connect via ``update()``, wait for the report to be acknowledged, stop.

    ``while_connecting`` runs inside ``establish_connection``, i.e. after the
    connect loop has started but before the handshake.
    """

    async def establish(*_args: object, **_kwargs: object) -> FakeV4Firmware:
        if while_connecting is not None:
            while_connecting()
        return firmware

    with patch(
        "custom_components.tuya_ble.tuya_ble.base.establish_connection",
        side_effect=establish,
    ):
        await device.update()
        await asyncio.wait_for(firmware.acknowledged.wait(), 1)
        await drain_responses(device)
        await device.stop()


def assert_v4_session(firmware: FakeV4Firmware, report_data: bytes) -> None:
    """Check the firmware double saw a clean protocol-4 session."""
    assert not firmware.violations
    codes = [code for code, *_ in firmware.received]
    assert codes[:2] == [0x0000, 0x0001]
    assert sorted(codes[2:]) == sorted([0x0003, TIME_REQUEST, 0x8006])
    assert codes.index(0x0003) < codes.index(0x8006)
    info = firmware.received[0]
    assert info[2:] == (b"\x00\x14", 4)
    assert {frame[3] for frame in firmware.received[1:]} == {5}
    ack = next(frame for frame in firmware.received if frame[0] == 0x8006)
    assert ack[1:3] == (firmware.report_seq, report_data[:7] + b"\x00")


@pytest.mark.parametrize(
    ("offered", "notify_char", "write_char"),
    [
        (V4_CHARACTERISTICS, CHARACTERISTIC_NOTIFY_V4, CHARACTERISTIC_WRITE_V4),
        (CLASSIC_CHARACTERISTICS, CHARACTERISTIC_NOTIFY, CHARACTERISTIC_WRITE),
    ],
)
async def test_v4_connect_and_read_end_to_end(
    offered: Iterable[str], notify_char: str, write_char: str
) -> None:
    """Connect, pair, query status and read a pushed report like on hardware."""
    credentials = make_credentials()
    data = report(soil_sensor_datapoints(), sn=3)
    firmware = FakeV4Firmware(credentials, data, characteristics=offered)
    device = make_v4_device(credentials)
    connected: list[bool] = []
    device.register_connected_callback(lambda: connected.append(True))

    await run_session(device, firmware)

    assert_v4_session(firmware, data)
    assert firmware.notify_char == notify_char
    assert set(firmware.write_targets) == {write_char}
    assert firmware.stopped == [notify_char]
    assert connected == [True]
    assert device.protocol_version == "4.2"
    values = {
        dp_id: dp.value
        for dp_id in (3, 5, 14, 15)
        if (dp := device.datapoints[dp_id]) is not None
    }
    assert values == {3: 41, 5: 215, 14: 1, 15: 87}
