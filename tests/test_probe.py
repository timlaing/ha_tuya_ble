"""Authenticated read probe never operates the motor or persists credentials."""

# pylint: disable=protected-access

from __future__ import annotations

import asyncio
import logging
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from bleak_retry_connector import (
    BleakAbortedError,
    BleakConnectionError,
    BleakNotFoundError,
    BleakOutOfConnectionSlotsError,
)
import pytest

from custom_components.tuya_ble.tuya_ble import (
    MANUFACTURER_DATA_ID,
    SERVICE_UUID,
)
from custom_components.tuya_ble.tuya_ble.const import TuyaBLECode
from custom_components.tuya_ble.tuya_ble.exceptions import (
    TuyaBLEDataFormatError,
    TuyaBLEDeviceError,
)
import custom_components.tuya_ble.tuya_ble.probe as probe_module
from custom_components.tuya_ble.tuya_ble.probe import (
    ProbeOutcome,
    _ProbeDevice,
    async_probe_authenticated_status,
)
from tests.conftest import (
    FakeAdvertisementData,
    FakeBLEAddress,
    FakeBleakClient,
    make_credentials,
)
from tests.test_device import _encrypt_uuid


@pytest.fixture(autouse=True)
def isolated_probe_cleanups(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep failed mock connections from blocking probes in another test."""
    monkeypatch.setattr(probe_module, "_pending_probe_cleanup", {})


def _input(bound: bool = True) -> tuple[BLEDevice, AdvertisementData]:
    """Build a correctly encrypted legacy identity without a radio."""
    address = cast(BLEDevice, FakeBLEAddress())
    adv = cast(
        AdvertisementData,
        FakeAdvertisementData(
            service_data={SERVICE_UUID: b"\x00pid01"},
            manufacturer_data={
                MANUFACTURER_DATA_ID: bytes((0x80 if bound else 0, 3))
                + bytes(4)
                + _encrypt_uuid(b"pid01")
            },
        ),
    )
    return address, adv


@pytest.mark.parametrize("bound", [False, True])
async def test_probe_without_expected_dp_or_binding(bound: bool) -> None:
    """Reject unsafe/incomplete requests before connecting."""
    ble, adv = _input(bound)
    with patch.object(
        _ProbeDevice, "_try_establish_connection", new_callable=AsyncMock
    ) as connect:
        result = await async_probe_authenticated_status(
            ble,
            adv,
            make_credentials(),
            "1234567890abcdef",
            frozenset() if bound else frozenset({1}),
        )
    assert result == ProbeOutcome.INCONCLUSIVE
    connect.assert_not_awaited()


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        ("none", ProbeOutcome.MATCHED),
        ("connect", ProbeOutcome.INCONCLUSIVE),
        ("notifications", ProbeOutcome.INCONCLUSIVE),
        ("info", ProbeOutcome.RETRYABLE),
        ("missing_session", ProbeOutcome.INCONCLUSIVE),
        ("unbound_info", ProbeOutcome.INCONCLUSIVE),
        ("pair", ProbeOutcome.REJECTED),
        ("pair_uncorrelated", ProbeOutcome.INCONCLUSIVE),
        ("pair_timeout", ProbeOutcome.RETRYABLE),
        ("status", ProbeOutcome.INCONCLUSIVE),
        ("status_timeout", ProbeOutcome.RETRYABLE),
        ("unexpected_dp", ProbeOutcome.RETRYABLE),
    ],
)
async def test_probe_stages(
    monkeypatch: pytest.MonkeyPatch, failure: str, expected: ProbeOutcome
) -> None:
    """Only genuine authenticated status DPs prove a candidate matches."""
    ble, adv = _input()
    client = FakeBleakClient()
    codes: list[TuyaBLECode] = []
    pairing: list[bytes] = []

    async def connect(device: _ProbeDevice) -> Any:
        return None if failure == "connect" else client

    async def send(
        device: _ProbeDevice,
        code: TuyaBLECode,
        data: bytes,
        response_to: int,
        wait: bool,
    ) -> bool:
        codes.append(code)
        if code == TuyaBLECode.FUN_SENDER_DEVICE_INFO:
            if failure == "info":
                return False
            if failure == "missing_session":
                return True
            reply = bytearray(46)
            reply[5] = 0 if failure == "unbound_info" else 1
            reply[2] = 3
            device._handle_device_info_response(bytes(reply))
        elif code == TuyaBLECode.FUN_SENDER_PAIR:
            pairing.append(data)
            if failure == "pair_timeout":
                return False
            device._handle_pair_response(
                b"\x01" if failure in ("pair", "pair_uncorrelated") else b"\x02"
            )
            if failure == "pair":
                device.pair_rejected = True
        elif code == TuyaBLECode.FUN_SENDER_DEVICE_STATUS:
            if failure == "status_timeout":
                return False
            device._handle_device_status_response(
                b"\x01" if failure == "status" else b"\x00"
            )
            device._parse_datapoints_v3(
                0.0,
                0,
                b"\x63\x01\x01\x01"
                if failure == "unexpected_dp"
                else b"\x01\x01\x01\x01",
                0,
            )
        return True

    monkeypatch.setattr(_ProbeDevice, "_try_establish_connection", connect)
    monkeypatch.setattr(_ProbeDevice, "_send_packet_while_connected", send)
    if failure == "notifications":
        client.start_notify = AsyncMock(side_effect=OSError("transport"))  # type: ignore[method-assign]
    monkeypatch.setattr("custom_components.tuya_ble.tuya_ble.probe.PROBE_TIMEOUT", 0.01)
    credentials = make_credentials(uuid="cloud-identity-uuid")
    result = await async_probe_authenticated_status(
        ble, adv, credentials, "1234567890abcdef", frozenset({1})
    )
    assert result == expected
    assert client.is_connected is False if failure != "connect" else True
    if failure == "unbound_info":
        assert codes == [TuyaBLECode.FUN_SENDER_DEVICE_INFO]
    assert all(
        code
        in {
            TuyaBLECode.FUN_SENDER_DEVICE_INFO,
            TuyaBLECode.FUN_SENDER_PAIR,
            TuyaBLECode.FUN_SENDER_DEVICE_STATUS,
        }
        for code in codes
    )
    if pairing:
        assert pairing[0].startswith(
            b"1234567890abcdefabcdef" + credentials.device_id.encode()
        )
    assert credentials.uuid == "cloud-identity-uuid"


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        ("timeout", ProbeOutcome.RETRYABLE),
        ("connection_error", ProbeOutcome.RETRYABLE),
        ("disconnect_callback", ProbeOutcome.RETRYABLE),
        ("disconnect_state", ProbeOutcome.RETRYABLE),
        ("disconnect_exception", ProbeOutcome.RETRYABLE),
        ("other_error", ProbeOutcome.INCONCLUSIVE),
    ],
)
async def test_transport_failure_classification(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure: str,
    expected: ProbeOutcome,
) -> None:
    """Only timeouts and lost connections request a retry of the same identity."""
    caplog.set_level(logging.DEBUG)
    ble, adv = _input()
    client = FakeBleakClient()

    async def authenticated_read(
        device: _ProbeDevice, received: asyncio.Future[None], enable_status: Any
    ) -> ProbeOutcome:
        """Simulate transport failures without exposing their secret-bearing text."""
        if failure == "timeout":
            raise TimeoutError("private-transport-failure")
        if failure == "connection_error":
            raise ConnectionError("private-transport-failure")
        if failure == "other_error":
            raise OSError("private-transport-failure")
        device._client = cast(Any, client)
        if failure == "disconnect_callback":
            device._disconnected(client)
        else:
            await client.disconnect()
        if failure == "disconnect_exception":
            raise OSError("private-transport-failure")
        return ProbeOutcome.INCONCLUSIVE

    monkeypatch.setattr(_ProbeDevice, "authenticated_read", authenticated_read)
    outcome = await async_probe_authenticated_status(
        ble, adv, make_credentials(), "1234567890abcdef", frozenset({1})
    )
    assert outcome == expected
    assert "private-transport-failure" not in caplog.text


async def test_normal_cleanup_disconnect_does_not_request_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A completed authenticated match survives its intentional cleanup callback."""
    ble, adv = _input()

    async def authenticated_read(
        device: _ProbeDevice, received: asyncio.Future[None], enable_status: Any
    ) -> ProbeOutcome:
        """Return a completed authenticated result before cleanup disconnects."""
        return ProbeOutcome.MATCHED

    async def close_probe(device: _ProbeDevice) -> None:
        """Notify the device of the disconnect performed by normal cleanup."""
        device._disconnected(object())

    monkeypatch.setattr(_ProbeDevice, "authenticated_read", authenticated_read)
    monkeypatch.setattr(_ProbeDevice, "close_probe", close_probe)
    assert (
        await async_probe_authenticated_status(
            ble, adv, make_credentials(), "1234567890abcdef", frozenset({1})
        )
        == ProbeOutcome.MATCHED
    )


@pytest.mark.parametrize(
    "error_type",
    [
        BleakConnectionError,
        BleakAbortedError,
        BleakOutOfConnectionSlotsError,
        BleakNotFoundError,
    ],
)
async def test_connector_errors_before_client_return_are_retryable(
    error_type: type[Exception], caplog: pytest.LogCaptureFixture
) -> None:
    """Connector-specific failures still retry when no disconnect callback fires."""
    ble, adv = _input()
    error = error_type("private-connector-failure")
    if error_type is BleakNotFoundError:
        error.__cause__ = TimeoutError("private-connector-timeout")
    with (
        patch(
            "custom_components.tuya_ble.tuya_ble.probe.establish_connection",
            new_callable=AsyncMock,
            side_effect=error,
        ) as establish,
        caplog.at_level(logging.DEBUG),
    ):
        outcome = await async_probe_authenticated_status(
            ble, adv, make_credentials(), "1234567890abcdef", frozenset({1})
        )
    assert outcome == ProbeOutcome.RETRYABLE
    establish.assert_awaited_once()
    assert establish.await_args is not None
    assert establish.await_args.kwargs["max_attempts"] == 1
    assert "private-connector-failure" not in caplog.text
    assert "private-connector-timeout" not in caplog.text


async def test_timeout_remains_retryable_when_cleanup_also_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An additional cleanup exception must not hide an earlier transport timeout."""
    ble, adv = _input()

    async def authenticated_read(
        device: _ProbeDevice, received: asyncio.Future[None], enable_status: Any
    ) -> ProbeOutcome:
        """Report a timeout independently of the subsequent cleanup failure."""
        raise TimeoutError()

    async def close_probe(device: _ProbeDevice) -> None:
        """Fail cleanup after the read has already timed out."""
        raise OSError()

    monkeypatch.setattr(_ProbeDevice, "authenticated_read", authenticated_read)
    monkeypatch.setattr(_ProbeDevice, "close_probe", close_probe)
    assert (
        await async_probe_authenticated_status(
            ble, adv, make_credentials(), "1234567890abcdef", frozenset({1})
        )
        == ProbeOutcome.RETRYABLE
    )


@pytest.mark.parametrize("cleanup_fails", [False, True])
@pytest.mark.parametrize("external_close", [False, True])
async def test_failed_cleanup_recovers_before_next_connection(
    monkeypatch: pytest.MonkeyPatch, cleanup_fails: bool, external_close: bool
) -> None:
    """A finished cleanup error is not proof that its connected client is closed."""
    ble, adv = _input()
    clients = [FakeBleakClient(), FakeBleakClient()]
    reads = 0

    async def authenticated_read(
        device: _ProbeDevice, received: asyncio.Future[None], enable_status: Any
    ) -> ProbeOutcome:
        """Record actual connection attempts rather than blocked probe invocations."""
        nonlocal reads
        if reads:
            assert not clients[0].is_connected
        device._client = cast(Any, clients[reads])
        reads += 1
        return ProbeOutcome.MATCHED

    async def close_probe(device: _ProbeDevice) -> None:
        """Fail to release the first link, but successfully close a later link."""
        if device._client is cast(Any, clients[0]):
            if cleanup_fails:
                raise OSError("private-cleanup-failure")
            return
        assert device._client is cast(Any, clients[1])
        await clients[1].disconnect()

    monkeypatch.setattr(_ProbeDevice, "authenticated_read", authenticated_read)
    monkeypatch.setattr(_ProbeDevice, "close_probe", close_probe)
    first = await async_probe_authenticated_status(
        ble, adv, make_credentials(), "1234567890abcdef", frozenset({1})
    )
    assert first == (
        ProbeOutcome.INCONCLUSIVE if cleanup_fails else ProbeOutcome.RETRYABLE
    )
    assert reads == 1
    assert clients[0].is_connected
    if external_close:
        await clients[0].disconnect()
    with patch.object(
        clients[0], "disconnect", wraps=clients[0].disconnect
    ) as disconnect:
        resumed = await async_probe_authenticated_status(
            ble, adv, make_credentials(), "1234567890abcdef", frozenset({1})
        )
        if external_close:
            disconnect.assert_not_awaited()
        else:
            disconnect.assert_awaited_once()
    assert resumed == ProbeOutcome.MATCHED
    assert reads == 2
    assert not clients[0].is_connected
    assert not probe_module._pending_probe_cleanup


@pytest.mark.parametrize("recovery", ["error", "timeout", "still_connected"])
async def test_retained_client_recovery_is_bounded_and_requires_link_closure(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    recovery: str,
) -> None:
    """Failed or ineffective recovery never permits another overlapping connection."""
    ble, adv = _input()
    client = FakeBleakClient()
    reads = 0

    async def authenticated_read(
        device: _ProbeDevice, received: asyncio.Future[None], enable_status: Any
    ) -> ProbeOutcome:
        """Record the first connection, which must remain the only connection."""
        nonlocal reads
        device._client = cast(Any, client)
        reads += 1
        return ProbeOutcome.MATCHED

    async def close_probe(device: _ProbeDevice) -> None:
        """Leave the physical link open after the initial cleanup fails."""
        raise OSError("private-cleanup-failure")

    async def disconnect() -> None:
        """Model a failing, stalled, or ineffectual backend disconnect."""
        if recovery == "error":
            raise OSError("private-cleanup-failure")
        if recovery == "timeout":
            await asyncio.Event().wait()

    monkeypatch.setattr(_ProbeDevice, "authenticated_read", authenticated_read)
    monkeypatch.setattr(_ProbeDevice, "close_probe", close_probe)
    monkeypatch.setattr(probe_module, "PROBE_CLEANUP_TIMEOUT", 0.01)
    with (
        patch.object(client, "disconnect", side_effect=disconnect) as reconnect_cleanup,
        caplog.at_level(logging.DEBUG),
    ):
        first = await async_probe_authenticated_status(
            ble, adv, make_credentials(), "1234567890abcdef", frozenset({1})
        )
        blocked = await async_probe_authenticated_status(
            ble, adv, make_credentials(), "1234567890abcdef", frozenset({1})
        )
        await asyncio.gather(
            *(pending[0] for pending in probe_module._pending_probe_cleanup.values()),
            return_exceptions=True,
        )
        reconnect_cleanup.assert_awaited_once()
    assert first == ProbeOutcome.INCONCLUSIVE
    assert blocked == ProbeOutcome.RETRYABLE
    assert reads == 1
    assert client.is_connected
    assert "private-cleanup-failure" not in caplog.text


async def test_invalid_pairing_frame_stops_before_connect() -> None:
    """Malformed cloud credentials are not a pairing rejection or retry signal."""
    ble, adv = _input()
    with patch.object(
        _ProbeDevice, "_try_establish_connection", new_callable=AsyncMock
    ) as connect:
        outcome = await async_probe_authenticated_status(
            ble,
            adv,
            make_credentials(local_key="short"),
            "1234567890abcdef",
            frozenset({1}),
        )
    assert outcome == ProbeOutcome.INCONCLUSIVE
    connect.assert_not_awaited()


@pytest.mark.parametrize(
    ("response", "expected"),
    [(b"\x01", ProbeOutcome.REJECTED), (b"\x02", ProbeOutcome.MATCHED)],
)
async def test_real_pair_response_future(
    monkeypatch: pytest.MonkeyPatch, response: bytes, expected: ProbeOutcome
) -> None:
    """The real sender distinguishes explicit refusals from authenticated matches."""
    ble, adv = _input()
    client = FakeBleakClient()

    async def connect(device: _ProbeDevice) -> Any:
        return client

    async def write(device: _ProbeDevice, packets: list[bytes]) -> None:
        seq_num = device._current_seq_num - 1
        if seq_num == 1:
            reply = bytearray(46)
            reply[2] = 3
            reply[5] = 1
            device._handle_command_or_response(
                1, seq_num, TuyaBLECode.FUN_SENDER_DEVICE_INFO, bytes(reply)
            )
        elif seq_num == 2:
            device._handle_command_or_response(
                2, seq_num, TuyaBLECode.FUN_SENDER_PAIR, response
            )
        else:
            device._handle_command_or_response(
                3, seq_num, TuyaBLECode.FUN_SENDER_DEVICE_STATUS, b"\x00"
            )
            device._parse_datapoints_v3(0.0, 0, b"\x01\x01\x01\x01", 0)

    monkeypatch.setattr(_ProbeDevice, "_try_establish_connection", connect)
    monkeypatch.setattr(_ProbeDevice, "_int_send_packet_while_connected", write)
    assert (
        await async_probe_authenticated_status(
            ble, adv, make_credentials(), "1234567890abcdef", frozenset({1})
        )
        == expected
    )


async def test_unsolicited_pair_rejection_is_not_conclusive() -> None:
    """An unrelated rejection is not evidence of a conclusive pairing refusal."""
    ble, adv = _input()
    device = _ProbeDevice(ble, adv)
    device._handle_command_or_response(3, 0, TuyaBLECode.FUN_SENDER_PAIR, b"\x01")
    assert not device.pair_rejected
    pending = asyncio.get_running_loop().create_future()
    device._input_expected_responses[5] = pending
    device._handle_command_or_response(4, 5, TuyaBLECode.FUN_SENDER_PAIR, b"\x01")
    assert not device.pair_rejected
    with pytest.raises(TuyaBLEDeviceError):
        await pending


def test_probe_rejects_other_encryption_flags() -> None:
    """DP evidence must be decrypted with the authenticated session key."""
    ble, adv = _input()
    device = _ProbeDevice(ble, adv)
    device.prepare(make_credentials(), "1234567890abcdef")
    device._input_buffer = bytearray(b"\x01")
    with pytest.raises(TuyaBLEDataFormatError):
        device._decrypt_input()
    device._session_key = bytes(16)
    device._input_buffer = bytearray(b"\x04")
    with pytest.raises(TuyaBLEDataFormatError):
        device._decrypt_input()


async def test_probe_cancellation_disconnects(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cancelling the probe cannot leave a BLE client connected."""
    ble, adv = _input()
    client = FakeBleakClient()
    started = asyncio.Event()
    blocker = asyncio.Event()

    async def connect(device: _ProbeDevice) -> Any:
        started.set()
        await blocker.wait()
        return client

    monkeypatch.setattr(_ProbeDevice, "_try_establish_connection", connect)
    task = asyncio.create_task(
        async_probe_authenticated_status(
            ble, adv, make_credentials(), "1234567890abcdef", frozenset({1})
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # No BLE connection was established before cancellation.
    assert client.is_connected


async def test_probe_disconnects_when_unsubscribe_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GATT cleanup failure must not retain an authenticated BLE session."""
    ble, adv = _input()
    client = FakeBleakClient()
    client.stop_notify = AsyncMock(side_effect=OSError("unsubscribe failed"))  # type: ignore[method-assign]

    async def connect(device: _ProbeDevice) -> Any:
        return client

    async def send(
        device: _ProbeDevice,
        code: TuyaBLECode,
        data: bytes,
        response_to: int,
        wait: bool,
    ) -> bool:
        if code == TuyaBLECode.FUN_SENDER_DEVICE_INFO:
            reply = bytearray(46)
            reply[5] = 1
            reply[2] = 3
            device._handle_device_info_response(bytes(reply))
        elif code == TuyaBLECode.FUN_SENDER_PAIR:
            device._handle_pair_response(b"\x02")
        else:
            device._handle_device_status_response(b"\x00")
            device._parse_datapoints_v3(0.0, 0, b"\x01\x01\x01\x01", 0)
        return True

    monkeypatch.setattr(_ProbeDevice, "_try_establish_connection", connect)
    monkeypatch.setattr(_ProbeDevice, "_send_packet_while_connected", send)
    assert (
        await async_probe_authenticated_status(
            ble, adv, make_credentials(), "1234567890abcdef", frozenset({1})
        )
        == ProbeOutcome.INCONCLUSIVE
    )


async def test_probe_wire_layer_refuses_other_commands() -> None:
    """A future caller cannot use the probe connection for motor writes."""
    ble, adv = _input()
    device = _ProbeDevice(ble, adv)
    device.prepare(make_credentials(), "1234567890abcdef")
    client = FakeBleakClient()
    device._client = cast(Any, client)
    with pytest.raises(TuyaBLEDeviceError):
        await device._send_packet_while_connected(
            TuyaBLECode.FUN_SENDER_DPS, b"motor", 0, False
        )
    assert not client.writes
    await device.close_probe()


async def test_probe_transport_write_does_not_log_secrets(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Even a transport exception containing key-like text stays out of logs."""
    ble, adv = _input()
    device = _ProbeDevice(ble, adv)
    device.prepare(make_credentials(local_key="secret-secret"), "1234567890abcdef")
    client = FakeBleakClient()
    client.write_gatt_char = AsyncMock(side_effect=OSError("secret-secret"))  # type: ignore[method-assign]
    device._client = cast(Any, client)
    with pytest.raises(OSError):
        await device._int_send_packet_while_connected([b"encrypted"])
    assert "secret-secret" not in caplog.text
    await device.close_probe()


async def test_probe_establishes_only_one_connection() -> None:
    """The isolated connect path has no retries or exception logging."""
    ble, adv = _input()
    device = _ProbeDevice(ble, adv)
    client = FakeBleakClient()
    with patch(
        "custom_components.tuya_ble.tuya_ble.probe.establish_connection",
        new_callable=AsyncMock,
        return_value=client,
    ) as establish:
        assert await device._try_establish_connection() is cast(Any, client)
        establish.assert_awaited_once()
    with patch(
        "custom_components.tuya_ble.tuya_ble.probe.establish_connection",
        new_callable=AsyncMock,
        return_value=None,
    ):
        assert await device._try_establish_connection() is None


async def test_probe_direct_write_stays_bounded() -> None:
    """The protected wire writer sends only the caller's encrypted packets."""
    ble, adv = _input()
    device = _ProbeDevice(ble, adv)
    with pytest.raises(TuyaBLEDeviceError):
        await device._int_send_packet_while_connected([b"packet"])
    client = FakeBleakClient()
    device._client = cast(Any, client)
    await device._int_send_packet_while_connected([b"one", b"two"])
    assert client.writes == [b"one", b"two"]
    device._disconnected(client)
    assert not device.paired_for_probe
    assert device._client is None
    await client.disconnect()


async def test_failed_disconnect_cannot_confirm_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful read is inconclusive if the BLE connection cannot close."""
    ble, adv = _input()
    client = FakeBleakClient()

    async def connect(device: _ProbeDevice) -> Any:
        return client

    async def send(
        device: _ProbeDevice,
        code: TuyaBLECode,
        data: bytes,
        response_to: int,
        wait: bool,
    ) -> bool:
        if code == TuyaBLECode.FUN_SENDER_DEVICE_INFO:
            reply = bytearray(46)
            reply[2] = 3
            reply[5] = 1
            device._handle_device_info_response(bytes(reply))
        elif code == TuyaBLECode.FUN_SENDER_PAIR:
            device._handle_pair_response(b"\x02")
        else:
            device._handle_device_status_response(b"\x00")
            device._parse_datapoints_v3(0.0, 0, b"\x01\x01\x01\x01", 0)
        return True

    async def cannot_close(device: _ProbeDevice) -> None:
        raise OSError("cannot close")

    monkeypatch.setattr(_ProbeDevice, "_try_establish_connection", connect)
    monkeypatch.setattr(_ProbeDevice, "_send_packet_while_connected", send)
    monkeypatch.setattr(_ProbeDevice, "close_probe", cannot_close)
    assert (
        await async_probe_authenticated_status(
            ble, adv, make_credentials(), "1234567890abcdef", frozenset({1})
        )
        == ProbeOutcome.INCONCLUSIVE
    )


async def test_disconnect_after_cancelled_stop_notify_has_its_own_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancelling notification cleanup cannot leave final disconnect pending forever."""
    ble, adv = _input()
    client = FakeBleakClient()
    disconnect_started = asyncio.Event()
    disconnect_cancelled = asyncio.Event()

    async def stall_notify(*_args: Any) -> None:
        """Wait until the outer cleanup deadline cancels notification shutdown."""
        await asyncio.Event().wait()

    async def stall_disconnect() -> None:
        """Model a D-Bus reply that never arrives but respects cancellation."""
        disconnect_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            disconnect_cancelled.set()

    async def read(
        device: _ProbeDevice, _received: Any, _enable_status: Any
    ) -> ProbeOutcome:
        """Assign a connected client before cleanup begins."""
        device._client = cast(Any, client)
        return ProbeOutcome.MATCHED

    monkeypatch.setattr(_ProbeDevice, "authenticated_read", read)
    monkeypatch.setattr(probe_module, "PROBE_CLEANUP_TIMEOUT", 0.01)
    with (
        patch.object(client, "stop_notify", side_effect=stall_notify),
        patch.object(client, "disconnect", side_effect=stall_disconnect),
    ):
        result = await async_probe_authenticated_status(
            ble, adv, make_credentials(), "1234567890abcdef", frozenset({1})
        )
        await disconnect_started.wait()
        await disconnect_cancelled.wait()
        await asyncio.gather(
            *(pending[0] for pending in probe_module._pending_probe_cleanup.values()),
            return_exceptions=True,
        )
    assert result == ProbeOutcome.RETRYABLE
    assert all(
        pending[0].done() for pending in probe_module._pending_probe_cleanup.values()
    )
    assert client.is_connected
    resumed = await async_probe_authenticated_status(
        ble, adv, make_credentials(), "1234567890abcdef", frozenset({1})
    )
    assert resumed == ProbeOutcome.MATCHED
    assert not client.is_connected
    assert not probe_module._pending_probe_cleanup


async def test_stalled_cleanup_has_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unresponsive disconnect does not hold the shared probe lock forever."""
    ble, adv = _input()
    cancelled = asyncio.Event()

    async def authenticated_read(
        device: _ProbeDevice, received: asyncio.Future[None], enable_status: Any
    ) -> ProbeOutcome:
        return ProbeOutcome.MATCHED

    async def hanging_cleanup(device: _ProbeDevice) -> None:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(_ProbeDevice, "authenticated_read", authenticated_read)
    monkeypatch.setattr(_ProbeDevice, "close_probe", hanging_cleanup)
    monkeypatch.setattr(
        "custom_components.tuya_ble.tuya_ble.probe.PROBE_CLEANUP_TIMEOUT", 0.01
    )
    assert (
        await async_probe_authenticated_status(
            ble, adv, make_credentials(), "1234567890abcdef", frozenset({1})
        )
        == ProbeOutcome.RETRYABLE
    )
    await cancelled.wait()


async def test_cancelling_cleanup_cancels_disconnect_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Closing a flow mid-disconnect cannot retain its cleanup task."""
    ble, adv = _input()
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def authenticated_read(
        device: _ProbeDevice, received: asyncio.Future[None], enable_status: Any
    ) -> ProbeOutcome:
        return ProbeOutcome.INCONCLUSIVE

    async def hanging_cleanup(device: _ProbeDevice) -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(_ProbeDevice, "authenticated_read", authenticated_read)
    monkeypatch.setattr(_ProbeDevice, "close_probe", hanging_cleanup)
    task = asyncio.create_task(
        async_probe_authenticated_status(
            ble, adv, make_credentials(), "1234567890abcdef", frozenset({1})
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await cancelled.wait()


async def test_unsolicited_dp_is_not_proof(monkeypatch: pytest.MonkeyPatch) -> None:
    """A datapoint emitted before a status request cannot authenticate mapping."""
    ble, adv = _input()
    client = FakeBleakClient()

    async def connect(device: _ProbeDevice) -> Any:
        return client

    async def send(
        device: _ProbeDevice,
        code: TuyaBLECode,
        data: bytes,
        response_to: int,
        wait: bool,
    ) -> bool:
        if code == TuyaBLECode.FUN_SENDER_DEVICE_INFO:
            reply = bytearray(46)
            reply[2] = 3
            reply[5] = 1
            device._handle_device_info_response(bytes(reply))
        elif code == TuyaBLECode.FUN_SENDER_PAIR:
            device._handle_pair_response(b"\x02")
            device._parse_datapoints_v3(0.0, 0, b"\x01\x01\x01\x01", 0)
        else:
            device._handle_device_status_response(b"\x00")
        return True

    monkeypatch.setattr(_ProbeDevice, "_try_establish_connection", connect)
    monkeypatch.setattr(_ProbeDevice, "_send_packet_while_connected", send)
    monkeypatch.setattr("custom_components.tuya_ble.tuya_ble.probe.PROBE_TIMEOUT", 0.01)
    assert (
        await async_probe_authenticated_status(
            ble, adv, make_credentials(), "1234567890abcdef", frozenset({1})
        )
        == ProbeOutcome.RETRYABLE
    )
