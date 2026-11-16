"""Verified automatic profiles, v4 commands and offline reconnect sessions."""

# pylint: disable=protected-access
from __future__ import annotations

import asyncio
from dataclasses import replace
from struct import pack
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from bleak.exc import BleakError
import pytest

from custom_components.tuya_ble.tuya_ble import (
    ConnectionProfile,
    ProbeOutcome,
    TuyaBLEDevice,
    async_detect_v4_profile,
    async_probe_authenticated_status,
    connection_cleanup,
)
from custom_components.tuya_ble.tuya_ble.const import (
    CHARACTERISTIC_WRITE_V4,
    TuyaBLECode,
    TuyaBLEDataPointType,
)
from custom_components.tuya_ble.tuya_ble.exceptions import (
    TuyaBLEDataFormatError,
    TuyaBLEDeviceError,
    TuyaBLEOutgoingDataLengthError,
    TuyaBLEResponseTimeoutError,
)
from tests.conftest import (
    FakeBleakClient,
    FakeBLEManager,
    FakeGATTServices,
    make_credentials,
    make_device,
)
from tests.protocol_harness import (
    ProtocolHarness,
    encrypt_payload,
    fragment,
    unpack_varint,
    v4_advertisement,
)
from tests.test_config_flow import FakeDiscovery
from tests.test_protocol_v4 import (
    FakeV4Firmware,
    drain_responses,
    klv,
    report,
)

PROBE_CONNECT = "custom_components.tuya_ble.tuya_ble.probe.establish_connection"
RUNTIME_CONNECT = "custom_components.tuya_ble.tuya_ble.base.establish_connection"
SHADE_DATA = report(
    klv(1, TuyaBLEDataPointType.DT_ENUM, b"\x01")
    + klv(3, TuyaBLEDataPointType.DT_VALUE, pack(">i", 60))
    + klv(13, TuyaBLEDataPointType.DT_VALUE, pack(">i", 88))
)


class ProfileServices(FakeGATTServices):
    """Expose concrete negotiated characteristic properties."""

    def __init__(self, characteristics: set[str], capacity: Any) -> None:
        super().__init__(characteristics)
        self.capacity = capacity

    def get_characteristic(self, specifier: str) -> Any:
        """Expose the negotiated ATT write capacity."""
        if specifier in self.characteristics:
            return SimpleNamespace(max_write_without_response_size=self.capacity)
        return None


class ProfileFirmware(FakeV4Firmware):
    """Synthetic shade rejecting incompatible markers/fragmentation."""

    def __init__(
        self,
        profile: ConnectionProfile,
        *,
        capacity: Any = 244,
        write_status: int = 0,
        status_ack: bool = True,
        report_data: bytes = SHADE_DATA,
        status_result: int = 0,
    ) -> None:
        super().__init__(make_credentials(), report_data)
        self.profile = profile
        self.capacity = capacity
        self.write_status = write_status
        self.status_ack = status_ack
        self.status_result = status_result
        self.on_disconnect: Any = None
        self.closed = False
        self.headers: list[int] = []
        self.services = ProfileServices(self.services.characteristics, capacity)

    async def disconnect(self) -> None:
        """Confirm each attempt releases physical ownership."""
        self.closed = True
        await super().disconnect()

    async def write_gatt_char(
        self, gatt_char: str, data: bytes, response: bool
    ) -> None:
        """Reject unsupported initial headers or fragmentation immediately."""
        num, pos = unpack_varint(data, 0)
        if num == 0:
            _, pos = unpack_varint(data, pos)
            self.headers.append(data[pos])
            if not self.received and (
                data[pos] != self.profile.info_version << 4
                or (self.profile.negotiated_mtu and len(data) <= 20)
            ):
                self._is_connected = False
                if self.on_disconnect is not None:
                    self.on_disconnect(self)
                raise BleakError("Unsupported handshake")
        assert gatt_char == CHARACTERISTIC_WRITE_V4
        assert not response
        assert len(data) <= self.capacity
        await super().write_gatt_char(gatt_char, data, response)

    def _answer(self, seq_num: int, code: int, data: bytes, flag: int) -> None:
        """Answer v4 writes and optionally omit the status-query ACK."""
        if code == TuyaBLECode.FUN_SENDER_DPS_V4.value:
            self._reply(code, seq_num, data[:5] + bytes([self.write_status]), 5)
        elif code == 3 and self.status_result != 0:
            self._reply(code, seq_num, bytes([self.status_result]), 5)
            self._reply(0x8006, 0, self._report, 5)
        elif code == TuyaBLECode.FUN_SENDER_DEVICE_STATUS.value and not self.status_ack:
            self.report_seq = self._reply(0x8006, 0, self._report, 5)
        else:
            super()._answer(seq_num, code, data, flag)


class NegotiatingServices(ProfileServices):
    """Expose a temporary capacity and signal when the write limit is read."""

    def __init__(self, characteristics: set[str]) -> None:
        super().__init__(characteristics, 20)
        self.capacity_read = asyncio.Event()
        self.write_lookups = 0

    def get_characteristic(self, specifier: str) -> Any:
        if specifier == CHARACTERISTIC_WRITE_V4:
            self.write_lookups += 1
            self.capacity_read.set()
        return super().get_characteristic(specifier)


@pytest.mark.parametrize("profile", list(ConnectionProfile))
@pytest.mark.parametrize("status_ack", [True, False])
async def test_detect_profile_over_real_encrypted_handshakes(
    profile: ConnectionProfile, status_ack: bool
) -> None:
    """Detection only accepts encrypted pairing and mapped status datapoints."""
    credentials = make_credentials()
    device = make_device()
    firmware: list[ProfileFirmware] = []

    async def establish(*args: Any, **kwargs: Any) -> ProfileFirmware:
        assert all(item.closed or not item.is_connected for item in firmware)
        client = ProfileFirmware(profile, status_ack=status_ack)
        client.on_disconnect = args[3]
        kwargs["capture_client"](client)
        firmware.append(client)
        return client

    with patch(PROBE_CONNECT, side_effect=establish):
        result = await async_detect_v4_profile(
            device._ble_device, v4_advertisement(), credentials, frozenset({1, 3, 13})
        )
    assert result == profile
    assert len(firmware) == list(ConnectionProfile).index(profile) + 1
    assert all(client.closed or not client.is_connected for client in firmware)
    assert not firmware[-1].violations
    assert [entry[0] for entry in firmware[-1].received] == [0, 1, 3]
    assert credentials.connection_profile is None


@pytest.mark.parametrize("profile", list(ConnectionProfile))
async def test_saved_profile_boots_without_advertisement_and_controls(
    profile: ConnectionProfile,
) -> None:
    """A fresh offline object uses the saved profile before any advert callback."""
    credentials = replace(make_credentials(), connection_profile=profile)
    # Deliberately no advertisement on the new runtime object, like HA startup.
    device = TuyaBLEDevice(None, make_device()._ble_device)  # type: ignore[arg-type]
    device.initialize_with_credentials(credentials)
    firmware = ProfileFirmware(profile, status_ack=False)
    with patch(RUNTIME_CONNECT, return_value=firmware):
        await device.update()
        await asyncio.wait_for(firmware.acknowledged.wait(), 1)
        assert device.connection_profile == profile
        assert device.protocol_version == "4.2"
        dp = device.datapoints[1]
        assert dp is not None
        await dp.set_value(0)
        await dp.set_value(2)
        await dp.set_value(1)
        await device.set_multiple_values({1: 0, 3: 20})
        await drain_responses(device)
        await device.stop()
    writes = [entry[2] for entry in firmware.received if entry[0] == 0x27]
    assert len(writes) == 4
    assert [data[5:] for data in writes[:3]] == [
        klv(1, TuyaBLEDataPointType.DT_ENUM, bytes([value])) for value in (0, 2, 1)
    ]
    assert writes[-1][5:] == (
        klv(1, TuyaBLEDataPointType.DT_ENUM, b"\x00")
        + klv(3, TuyaBLEDataPointType.DT_VALUE, pack(">i", 20))
    )
    assert firmware.headers[0] == profile.info_version << 4
    assert set(firmware.headers[1:]) == {0x40}
    assert firmware.closed


async def test_profile_reconnect_recomputes_capacity_and_session_keys() -> None:
    """Reconnection does not reuse the old ATT size or cryptographic session."""
    credentials = replace(
        make_credentials(), connection_profile=ConnectionProfile.V2_MTU
    )
    device = make_device()
    device.initialize_with_credentials(credentials)
    first = ProfileFirmware(ConnectionProfile.V2_MTU, capacity=244)
    second = ProfileFirmware(ConnectionProfile.V2_MTU, capacity=100)
    with patch(RUNTIME_CONNECT, side_effect=[first, second]):
        await device.update()
        await asyncio.wait_for(first.acknowledged.wait(), 1)
        await drain_responses(device)
        with patch.object(device, "_schedule_reconnect"):
            first._is_connected = False
            device._disconnected(cast(Any, first))
        assert device._session_key is None
        await device.update()
        await asyncio.wait_for(second.acknowledged.wait(), 1)
        await drain_responses(device)
        # A queued disconnect from the old link must not invalidate the new one.
        device._disconnected(cast(Any, first))
        assert device._client is cast(Any, second)
        assert device._is_paired
        dp = device.datapoints[1]
        assert dp is not None
        await dp.set_value(0)
        await device.stop()
    assert first.received[0][2] == pack(">H", 244)
    assert second.received[0][2] == pack(">H", 100)
    assert not second.violations
    assert second.closed


async def test_v4_write_rejection_rolls_back_all_values() -> None:
    """An authenticated rejection is not a successful or optimistic command."""
    credentials = replace(
        make_credentials(), connection_profile=ConnectionProfile.V4_20
    )
    device = make_device()
    device.initialize_with_credentials(credentials)
    firmware = ProfileFirmware(ConnectionProfile.V4_20, write_status=1)
    with patch(RUNTIME_CONNECT, return_value=firmware):
        await device.update()
        await asyncio.wait_for(firmware.acknowledged.wait(), 1)
        with pytest.raises(TuyaBLEDeviceError):
            await device.set_multiple_values({1: 0, 3: 20})
        state, position = device.datapoints[1], device.datapoints[3]
        assert state is not None
        assert position is not None
        assert state.value == 1
        assert position.value == 60
        await device.stop()


@pytest.mark.parametrize("capacity", [20, 19, None, "244"])
async def test_large_profile_never_exceeds_supported_capacity(capacity: Any) -> None:
    """An unavailable larger ATT size rejects a profile before any packet write."""
    credentials = replace(
        make_credentials(), connection_profile=ConnectionProfile.V2_MTU
    )
    device = make_device()
    firmware = ProfileFirmware(ConnectionProfile.V2_MTU, capacity=capacity)
    with (
        patch(PROBE_CONNECT, return_value=firmware),
        patch(
            "custom_components.tuya_ble.tuya_ble.connection_profile."
            "MTU_NEGOTIATION_TIMEOUT",
            0.01,
        ),
    ):
        result = await async_probe_authenticated_status(
            device._ble_device,
            v4_advertisement(),
            credentials,
            credentials.uuid,
            frozenset({13}),
        )
    assert result == ProbeOutcome.INCONCLUSIVE
    assert not firmware.writes
    assert firmware.closed


async def test_capacity_is_capped_at_tuya_sdk_limit() -> None:
    """An oversized backend ATT capacity is bounded by Tuya's own limit."""
    device = make_device()
    device.initialize_with_credentials(
        replace(make_credentials(), connection_profile=ConnectionProfile.V4_MTU)
    )
    firmware = ProfileFirmware(ConnectionProfile.V4_MTU, capacity=512)
    with patch(RUNTIME_CONNECT, return_value=firmware):
        await device.update()
        await asyncio.wait_for(firmware.acknowledged.wait(), 1)
        await device.stop()
    assert firmware.received[0][2] == pack(">H", 244)


@pytest.mark.parametrize("phase", ["probe", "runtime"])
async def test_mtu_profile_waits_for_delayed_capacity_before_writing(
    phase: str,
) -> None:
    """A transient 20-byte limit must not reject an otherwise usable profile."""
    profile = ConnectionProfile.V2_MTU
    credentials = replace(make_credentials(), connection_profile=profile)
    device = make_device()
    device.initialize_with_credentials(credentials)
    firmware = ProfileFirmware(profile, capacity=20)
    services = NegotiatingServices(firmware.services.characteristics)
    firmware.services = services

    async def run() -> ProbeOutcome | None:
        if phase == "probe":
            return await async_probe_authenticated_status(
                device._ble_device,
                v4_advertisement(),
                credentials,
                credentials.uuid,
                frozenset({13}),
            )
        await device.update()
        await asyncio.wait_for(firmware.acknowledged.wait(), 1)
        return None

    with (
        patch(PROBE_CONNECT, return_value=firmware),
        patch(RUNTIME_CONNECT, return_value=firmware),
        patch(
            "custom_components.tuya_ble.tuya_ble.connection_profile."
            "MTU_NEGOTIATION_POLL_INTERVAL",
            0.005,
        ),
    ):
        task = asyncio.create_task(run())
        await services.capacity_read.wait()
        assert not firmware.writes
        assert not task.done()
        firmware.capacity = 244
        services.capacity = 244
        result = await task
        if phase == "runtime":
            await drain_responses(device)
            await device.stop()

    assert services.write_lookups > 2
    assert firmware.received[0][2] == pack(">H", 244)
    assert result == (ProbeOutcome.MATCHED if phase == "probe" else None)
    assert firmware.closed


@pytest.mark.parametrize("termination", ["disconnect", "cancel"])
async def test_mtu_probe_wait_stops_without_writes(termination: str) -> None:
    """Disconnect and cancellation do not wait for the full negotiation timeout."""
    credentials = replace(
        make_credentials(), connection_profile=ConnectionProfile.V2_MTU
    )
    firmware = ProfileFirmware(ConnectionProfile.V2_MTU, capacity=20)
    services = NegotiatingServices(firmware.services.characteristics)
    firmware.services = services
    with (
        patch(PROBE_CONNECT, return_value=firmware),
        patch(
            "custom_components.tuya_ble.tuya_ble.connection_profile."
            "MTU_NEGOTIATION_POLL_INTERVAL",
            0.005,
        ),
    ):
        task = asyncio.create_task(
            async_probe_authenticated_status(
                make_device()._ble_device,
                v4_advertisement(),
                credentials,
                credentials.uuid,
                frozenset({13}),
            )
        )
        await services.capacity_read.wait()
        if termination == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert firmware.closed
        else:
            firmware._is_connected = False
            assert await asyncio.wait_for(task, 0.5) == ProbeOutcome.RETRYABLE
    assert not firmware.writes
    assert not firmware.is_connected


async def test_mtu_runtime_wait_stops_on_shutdown() -> None:
    """An intentional stop releases the link without waiting out negotiation."""
    device = make_device()
    device.initialize_with_credentials(
        replace(
            make_credentials(),
            connection_profile=ConnectionProfile.V2_MTU,
        )
    )
    firmware = ProfileFirmware(ConnectionProfile.V2_MTU, capacity=20)
    services = NegotiatingServices(firmware.services.characteristics)
    firmware.services = services
    with (
        patch(RUNTIME_CONNECT, return_value=firmware),
        patch(
            "custom_components.tuya_ble.tuya_ble.connection_profile."
            "MTU_NEGOTIATION_POLL_INTERVAL",
            0.005,
        ),
    ):
        update = asyncio.create_task(device.update())
        await services.capacity_read.wait()
        await asyncio.wait_for(device.stop(), 0.5)
        await update
    assert not firmware.writes
    assert firmware.closed


@pytest.mark.parametrize("outcome", [ProbeOutcome.INCONCLUSIVE, ProbeOutcome.REJECTED])
async def test_no_verified_profile_is_returned_on_failure(
    outcome: ProbeOutcome,
) -> None:
    """Failures never select a default profile or change cached credentials."""
    device = make_device()
    with patch(
        "custom_components.tuya_ble.tuya_ble.probe.async_probe_authenticated_status",
        new=AsyncMock(return_value=outcome),
    ) as probe:
        assert (
            await async_detect_v4_profile(
                device._ble_device,
                v4_advertisement(),
                make_credentials(),
                frozenset({13}),
            )
            is None
        )
    assert probe.call_count == (1 if outcome == ProbeOutcome.REJECTED else 4)


async def test_profile_candidate_preserves_credentials_and_schema() -> None:
    """The detached profile copy retains every credential and metadata field."""
    credentials = make_credentials()
    credentials.functions = [{"dp_id": 13, "code": "battery"}]
    credentials.status_range = [{"code": "battery", "type": "Integer"}]
    credentials.local_schema = {"captured_at": "synthetic"}
    credentials.cloud_info = {"product_name": "Battery BLE Roller"}
    device = make_device()
    with patch(
        "custom_components.tuya_ble.tuya_ble.probe.async_probe_authenticated_status",
        return_value=ProbeOutcome.MATCHED,
    ) as probe:
        result = await async_detect_v4_profile(
            device._ble_device, v4_advertisement(), credentials, frozenset({13})
        )
    candidate = probe.call_args.args[2]
    assert result == ConnectionProfile.V4_20
    assert vars(candidate) == {**vars(credentials), "connection_profile": result}
    assert candidate is not credentials
    assert credentials.connection_profile is None


async def test_missing_expected_report_never_verifies_profile() -> None:
    """A clean handshake with unrelated datapoints is not enough."""
    device = make_device()
    firmware = ProfileFirmware(ConnectionProfile.V4_20)
    with (
        patch(PROBE_CONNECT, return_value=firmware),
        patch("custom_components.tuya_ble.tuya_ble.probe.PROBE_TIMEOUT", 0.01),
    ):
        result = await async_probe_authenticated_status(
            device._ble_device,
            v4_advertisement(),
            make_credentials(),
            make_credentials().uuid,
            frozenset({99}),
        )
    assert result == ProbeOutcome.RETRYABLE
    assert firmware.closed


async def test_stop_cancels_active_profile_detection_and_releases_link() -> None:
    """Flow cancellation cannot leave the final probe connected."""
    device = make_device()
    firmware = ProfileFirmware(ConnectionProfile.V4_20)
    with patch(PROBE_CONNECT, return_value=firmware):
        task = asyncio.create_task(
            async_detect_v4_profile(
                device._ble_device,
                v4_advertisement(),
                make_credentials(),
                frozenset({99}),
            )
        )
        while not any(code == 3 for code, *_ in firmware.received):
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert firmware.closed


async def test_v4_write_timeout_rolls_back() -> None:
    """A silent device cannot leave an unconfirmed datapoint value committed."""
    device = make_device()
    device.initialize_with_credentials(make_credentials())
    device._protocol_version = 4
    device._is_paired = True
    device._session_key = bytes(16)
    device._client = FakeBleakClient()  # type: ignore[assignment]
    dp = device.datapoints.update_from_device(1, 0, 0, TuyaBLEDataPointType.DT_ENUM, 1)
    with (
        patch(
            "custom_components.tuya_ble.tuya_ble.protocol_send.RESPONSE_WAIT_TIMEOUT",
            0.01,
        ),
        pytest.raises(TuyaBLEResponseTimeoutError),
    ):
        await dp.set_value(0)
    assert dp.value == 1
    assert not device._input_expected_responses
    assert not device._v4_write_headers
    assert not device._input_expected_codes
    await device.stop()


@pytest.mark.parametrize("invalid", ["code", "header"])
async def test_write_ack_must_match_command_and_echo(invalid: str) -> None:
    """An unrelated response cannot turn an unconfirmed command into success."""
    credentials = replace(
        make_credentials(), connection_profile=ConnectionProfile.V4_20
    )
    device = make_device()
    device.initialize_with_credentials(credentials)
    firmware = ProfileFirmware(ConnectionProfile.V4_20)
    original_reply = firmware._reply

    def reply(code: int, response_to: int, data: bytes, flag: int) -> int:
        if code == 0x27:
            if invalid == "code":
                code, data = 3, b"\x00"
            else:
                data = data[:4] + bytes([data[4] ^ 1]) + data[5:]
        return original_reply(code, response_to, data, flag)

    with (
        patch(RUNTIME_CONNECT, return_value=firmware),
        patch.object(firmware, "_reply", side_effect=reply),
        patch(
            "custom_components.tuya_ble.tuya_ble.protocol_send.RESPONSE_WAIT_TIMEOUT",
            0.01,
        ),
    ):
        await device.update()
        await asyncio.wait_for(firmware.acknowledged.wait(), 1)
        dp = device.datapoints[1]
        assert dp is not None
        with pytest.raises(TuyaBLEResponseTimeoutError):
            await dp.set_value(0)
        assert dp.value == 1
        await device.stop()


@pytest.mark.parametrize("data", [b"", b"\x00" * 5, b"\x01" + b"\x00" * 5])
async def test_malformed_v4_ack_is_dropped(
    data: bytes, caplog: pytest.LogCaptureFixture
) -> None:
    """Public notifications reject malformed ACK lengths and payload versions."""
    harness = ProtocolHarness(protocol_version=4)
    await harness.client.start_notify(
        "notify", harness.device._safe_notification_handler
    )
    assert harness.device._session_key is not None
    encrypted = encrypt_payload(
        harness.device._session_key, 5, 1, 0, TuyaBLECode.FUN_SENDER_DPS_V4, data
    )
    for packet in fragment(encrypted, protocol_version=4):
        harness.notify(packet)
    assert "Ignoring malformed BLE notification" in caplog.text


@pytest.mark.parametrize("invalid", ["missing", "id", "length", "enum", "bitmap"])
async def test_v4_write_validation_precedes_io(invalid: str) -> None:
    """Malformed IDs, lengths and scalar widths never reach the device."""
    device = make_device()
    device._protocol_version = 4
    dp_id = 256 if invalid == "id" else 1
    if invalid != "missing":
        dp_type, value = {
            "id": (TuyaBLEDataPointType.DT_RAW, b"x"),
            "length": (TuyaBLEDataPointType.DT_RAW, bytes(65536)),
            "enum": (TuyaBLEDataPointType.DT_ENUM, 256),
            "bitmap": (TuyaBLEDataPointType.DT_BITMAP, bytes(3)),
        }[invalid]
        device.datapoints.get_or_create(dp_id, dp_type, cast(Any, value))
    with (
        patch.object(device, "_send_packet", new=AsyncMock()) as send,
        pytest.raises((
            TuyaBLEDeviceError,
            TuyaBLEDataFormatError,
            TuyaBLEOutgoingDataLengthError,
        )),
    ):
        await device.send_datapoints([dp_id])
    send.assert_not_called()


async def test_v4_large_raw_write_uses_two_byte_length() -> None:
    """v4 does not inherit the v3 single-byte value-length limit."""
    device = make_device()
    device._protocol_version = 4
    value = bytes(300)
    device.datapoints.get_or_create(9, TuyaBLEDataPointType.DT_RAW, value)
    with patch.object(device, "_send_packet", new=AsyncMock()) as send:
        await device.send_datapoints([9])
    assert send.call_args.args[1][5:] == klv(9, TuyaBLEDataPointType.DT_RAW, value)


@pytest.mark.parametrize(
    ("lengths", "accepted"),
    [
        ((65526,), True),
        ((65527,), False),
        ((65535,), False),
        ((32761, 32761), True),
        ((32761, 32762), False),
        ((32762, 32762), False),
    ],
)
async def test_v4_complete_payload_bounds_and_batch_rollback(
    lengths: tuple[int, ...], accepted: bool
) -> None:
    """Prefixes and all record headers count toward the frame's 16-bit length."""
    device = make_device()
    device._protocol_version = 4
    updates: dict[int, bytes | bool | int | str] = {}
    original: dict[int, bytes] = {}
    for dp_id, size in enumerate(lengths, start=1):
        original[dp_id] = bytes([dp_id])
        updates[dp_id] = bytes(size)
        device.datapoints.get_or_create(
            dp_id, TuyaBLEDataPointType.DT_RAW, original[dp_id]
        )

    with patch.object(device, "_send_packet", new=AsyncMock()) as send:
        if accepted:
            await device.set_multiple_values(updates)
            send.assert_awaited_once()
            assert len(send.call_args.args[1]) == 65535
        else:
            with pytest.raises(TuyaBLEOutgoingDataLengthError) as error:
                await device.set_multiple_values(updates)
            send.assert_not_called()
            assert "aggregate payload length" in str(error.value)
            assert "65535 bytes" in str(error.value)
            assert "id or value length" not in str(error.value)

    for dp_id, requested in updates.items():
        datapoint = device.datapoints[dp_id]
        assert datapoint is not None
        assert datapoint.value == (requested if accepted else original[dp_id])


@pytest.mark.parametrize("dp_id", [-1, 256])
async def test_v4_invalid_id_reports_its_own_limit_and_rolls_back(dp_id: int) -> None:
    """The transport reports the ID range, not the aggregate payload bound."""
    device = make_device()
    device._protocol_version = 4
    datapoint = device.datapoints.get_or_create(
        dp_id, TuyaBLEDataPointType.DT_RAW, b"old"
    )
    with (
        patch.object(device, "_send_packet", new=AsyncMock()) as send,
        pytest.raises(TuyaBLEOutgoingDataLengthError) as error,
    ):
        await device.set_multiple_values({dp_id: b"new"})
    send.assert_not_called()
    assert "datapoint ID" in str(error.value)
    assert "0-255" in str(error.value)
    assert "bytes" not in str(error.value)
    assert "65535" not in str(error.value)
    assert datapoint.value == b"old"


@pytest.mark.parametrize("advertisement", [None, "unbound", "classic", "empty_mapping"])
async def test_profile_detection_rejects_ineligible_input(
    advertisement: str | None,
) -> None:
    """Only bound v4 devices with mapped datapoints enter profile detection."""
    adv = (
        v4_advertisement(service=b"\x41\x00\x00\x08abcdefgh")
        if advertisement == "unbound"
        else v4_advertisement()
    )
    if advertisement in (None, "classic"):
        adv = cast(
            Any,
            make_device()._advertisement_data
            if advertisement is None
            else FakeDiscovery().advertisement,
        )
    device = make_device()
    with patch(PROBE_CONNECT, new=AsyncMock()) as connect:
        result = await async_detect_v4_profile(
            device._ble_device,
            adv,
            make_credentials(),
            frozenset() if advertisement == "empty_mapping" else frozenset({13}),
        )
    assert result is None
    connect.assert_not_called()


async def test_cleanup_gate_before_probe_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No probe ownership exists before the BLE package registers its waiter."""
    monkeypatch.setattr(connection_cleanup, "_probe_cleanup_state", {})
    assert await connection_cleanup.async_wait_for_probe_cleanup("test-address")


async def test_explicit_status_refusal_cannot_verify_profile() -> None:
    """Even valid datapoints cannot hide an explicitly rejected status query."""
    device = make_device()
    firmware = ProfileFirmware(ConnectionProfile.V4_20, status_result=1)
    with patch(PROBE_CONNECT, return_value=firmware):
        result = await async_probe_authenticated_status(
            device._ble_device,
            v4_advertisement(),
            make_credentials(),
            make_credentials().uuid,
            frozenset({13}),
        )
    assert result == ProbeOutcome.INCONCLUSIVE
    assert firmware.closed


@pytest.mark.parametrize("status_result", [0, 1])
async def test_status_reply_after_datapoints_is_not_discarded(
    status_result: int,
) -> None:
    """A completed report future must not hide a queued status refusal."""
    credentials = make_credentials()
    firmware = ProfileFirmware(ConnectionProfile.V4_20)
    original_answer = firmware._answer

    def answer(seq_num: int, code: int, data: bytes, flag: int) -> None:
        if code == TuyaBLECode.FUN_SENDER_DEVICE_STATUS.value:
            firmware._reply(0x8006, 0, firmware._report, 5)
            firmware._reply(code, seq_num, bytes([status_result]), 5)
        else:
            original_answer(seq_num, code, data, flag)

    with (
        patch(PROBE_CONNECT, return_value=firmware),
        patch.object(firmware, "_answer", side_effect=answer),
    ):
        result = await async_probe_authenticated_status(
            make_device()._ble_device,
            v4_advertisement(),
            credentials,
            credentials.uuid,
            frozenset({13}),
        )
    assert result == (
        ProbeOutcome.MATCHED if status_result == 0 else ProbeOutcome.INCONCLUSIVE
    )
    assert firmware.closed


async def test_status_refusal_during_probe_cleanup_prevents_match() -> None:
    """A late queued refusal during teardown still invalidates verification."""
    credentials = make_credentials()
    firmware = ProfileFirmware(ConnectionProfile.V4_20, status_ack=False)
    original_stop = firmware.stop_notify

    async def refuse_during_stop(gatt_char: str) -> None:
        firmware._reply(TuyaBLECode.FUN_SENDER_DEVICE_STATUS.value, 0, b"\x01", 5)
        await asyncio.sleep(0)
        await original_stop(gatt_char)

    with (
        patch(PROBE_CONNECT, return_value=firmware),
        patch.object(firmware, "stop_notify", side_effect=refuse_during_stop),
    ):
        result = await async_probe_authenticated_status(
            make_device()._ble_device,
            v4_advertisement(),
            credentials,
            credentials.uuid,
            frozenset({13}),
        )
    assert result == ProbeOutcome.INCONCLUSIVE
    assert firmware.closed


async def test_profile_loaded_through_offline_manager() -> None:
    """Both public credential initialisation paths restore the persisted profile."""
    credentials = replace(
        make_credentials(), connection_profile=ConnectionProfile.V2_20
    )
    device = make_device(manager=FakeBLEManager(credentials))
    await device.initialize()
    assert device.connection_profile == ConnectionProfile.V2_20
    assert device._protocol_version == 4


async def test_profile_connection_batch_is_bounded_and_schedules_recovery() -> None:
    """A device unavailable at HA startup retries without a hundred-connect loop."""
    device = make_device()
    device.initialize_with_credentials(
        replace(make_credentials(), connection_profile=ConnectionProfile.V4_20)
    )
    with (
        patch.object(
            device, "_try_connect_and_configure", new=AsyncMock(return_value=False)
        ) as connect,
        patch.object(device, "_schedule_reconnect") as reconnect,
        pytest.raises(BleakError),
    ):
        await device.update()
    assert connect.call_count == 3
    reconnect.assert_called_once()


@pytest.mark.parametrize("delay", [1, 40])
async def test_profile_reconnect_backoff_is_bounded(delay: float) -> None:
    """Failed sessions back off, stopping cleanly during the delay."""
    device = make_device()
    device.initialize_with_credentials(
        replace(make_credentials(), connection_profile=ConnectionProfile.V4_20)
    )
    device._reconnect_delay = delay

    async def stop_during_delay(_delay: float) -> None:
        device._expected_disconnect = True

    with (
        patch.object(
            device, "_ensure_connected", new=AsyncMock(side_effect=BleakError)
        ),
        patch(
            "custom_components.tuya_ble.tuya_ble.base.asyncio.sleep",
            side_effect=stop_during_delay,
        ),
        patch.object(device, "_schedule_reconnect") as reconnect,
    ):
        await device._reconnect()
    assert device._reconnect_delay == min(delay * 2, 60)
    reconnect.assert_not_called()


async def test_runtime_waits_for_unconfirmed_probe_cleanup() -> None:
    """Reconfiguration failure cannot overlap retained probe and runtime links."""
    device = make_device()
    device.initialize_with_credentials(
        replace(make_credentials(), connection_profile=ConnectionProfile.V4_20)
    )
    with (
        patch(
            "custom_components.tuya_ble.tuya_ble.probe."
            "_async_wait_for_previous_cleanup",
            return_value=False,
        ),
        patch(RUNTIME_CONNECT, new=AsyncMock()) as connect,
        patch.object(device, "_schedule_reconnect"),
        pytest.raises(BleakError),
    ):
        await device.update()
    connect.assert_not_called()
