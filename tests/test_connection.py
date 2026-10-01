"""Tests for BLE connection lifecycle and protocol error paths."""

# pylint: disable=protected-access,too-many-lines
from __future__ import annotations

import asyncio
from contextlib import suppress
import logging
from struct import pack
from unittest.mock import AsyncMock, patch

from bleak.exc import BleakDBusError, BleakError
from bleak_retry_connector import BleakNotFoundError
import pytest

from custom_components.tuya_ble.tuya_ble.const import (
    CHARACTERISTIC_NOTIFY,
    TuyaBLECode,
)
from custom_components.tuya_ble.tuya_ble.exceptions import (
    TuyaBLEDataCRCError,
    TuyaBLEDataFormatError,
    TuyaBLEDataLengthError,
    TuyaBLEDeviceError,
    TuyaBLEError,
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
    frame_packet0,
    make_raw,
    pack_varint,
)

pytestmark = pytest.mark.filterwarnings(
    "ignore::RuntimeWarning",
)


def _close_task(coro: object) -> asyncio.Task[None]:
    """Close a coroutine and return a real task, without running the coroutine.

    ``asyncio.create_task`` is patched out in these tests so nothing is actually
    scheduled, but the production code still attaches a done-callback to the
    return value, so a genuine task object has to come back. Uses the loop's
    own ``create_task``, which this patch does not intercept.
    """
    if hasattr(coro, "close"):
        coro.close()
    return asyncio.get_event_loop().create_task(asyncio.sleep(0))


# ---------------------------------------------------------------------------
# tuya_ble.py coverage helpers
# ---------------------------------------------------------------------------


async def test_initialize_no_manager() -> None:
    """Initialize with a manager that returns no credentials."""
    dev = make_device(manager=FakeBLEManager(None))
    await dev.initialize()
    assert dev._device_info is None
    assert dev._local_key is None


async def test_initialize_fetches_credentials() -> None:
    """Initialize fetches credentials from the manager."""
    creds = make_credentials()
    dev = make_device(manager=FakeBLEManager(creds))
    await dev.initialize()
    assert dev._device_info is creds
    assert dev._local_key == creds.local_key[:6].encode()


async def test_disconnect_with_live_client() -> None:
    """Disconnect should stop notify and disconnect the client."""
    dev = make_device()
    client = FakeBleakClient(is_connected=True)
    dev._client = client  # type: ignore[assignment]
    dev._is_paired = True
    await dev._execute_disconnect()
    assert dev._client is None
    assert CHARACTERISTIC_NOTIFY in client.stopped


async def test_disconnect_with_none_client() -> None:
    """Disconnect with no client should not raise."""
    dev = make_device()
    dev._client = None
    await dev._execute_disconnect()
    assert dev._client is None


async def test_expected_disconnect_returns_early() -> None:
    """Return early if expected_disconnect is True."""
    dev = make_device()
    dev._expected_disconnect = True
    await dev._ensure_connected()


async def test_already_ready_returns_early() -> None:
    """Return early if already connected and paired."""
    dev = make_device()
    dev._client = FakeBleakClient(is_connected=True)  # type: ignore[assignment]
    dev._is_paired = True
    await dev._ensure_connected()


async def test_returns_none_when_client_not_connected() -> None:
    """Return None when establish_connection returns a disconnected client."""
    dev = make_device(manager=FakeBLEManager(make_credentials()))
    client = FakeBleakClient(is_connected=False)

    async def fake_establish(*args: object, **kwargs: object) -> FakeBleakClient:
        return client

    with patch(
        "custom_components.tuya_ble.tuya_ble.base.establish_connection",
        side_effect=fake_establish,
    ):
        result = await dev._try_establish_connection()
        assert result is None


async def test_bleak_not_found_returns_none() -> None:
    """Return None on BleakNotFoundError."""
    dev = make_device(manager=FakeBLEManager(make_credentials()))

    async def fail(*args: object, **kwargs: object) -> None:
        raise BleakNotFoundError()

    with patch(
        "custom_components.tuya_ble.tuya_ble.base.establish_connection",
        side_effect=fail,
    ):
        result = await dev._try_establish_connection()
        assert result is None


async def test_bleak_error_returns_none() -> None:
    """Return None on BleakError (BLEAK_EXCEPTIONS)."""
    dev = make_device(manager=FakeBLEManager(make_credentials()))

    async def fail(*args: object, **kwargs: object) -> None:
        raise BleakError("test")

    with patch(
        "custom_components.tuya_ble.tuya_ble.base.establish_connection",
        side_effect=fail,
    ):
        result = await dev._try_establish_connection()
        assert result is None


async def test_os_error_returns_none() -> None:
    """Return None on OSError (BLEAK_EXCEPTIONS)."""
    dev = make_device(manager=FakeBLEManager(make_credentials()))

    async def fail(*args: object, **kwargs: object) -> None:
        raise OSError("test")

    with patch(
        "custom_components.tuya_ble.tuya_ble.base.establish_connection",
        side_effect=fail,
    ):
        result = await dev._try_establish_connection()
        assert result is None


async def test_connection_exception_returns_none() -> None:
    """Return None on BLE_CONNECTION_EXCEPTIONS (TuyaBLEError)."""
    dev = make_device(manager=FakeBLEManager(make_credentials()))

    async def fail(*args: object, **kwargs: object) -> None:
        raise TuyaBLEError("test")

    with patch(
        "custom_components.tuya_ble.tuya_ble.base.establish_connection",
        side_effect=fail,
    ):
        result = await dev._try_establish_connection()
        assert result is None


async def test_returns_false_when_no_client() -> None:
    """Return False when _client is None."""
    dev = make_device()
    dev._client = None
    assert await dev._try_start_notifications() is False


async def test_returns_false_when_disconnected() -> None:
    """Return False when client is disconnected."""
    dev = make_device()
    dev._client = FakeBleakClient(is_connected=False)  # type: ignore[assignment]
    assert await dev._try_start_notifications() is False


async def test_returns_false_on_start_notify_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Return False and clear client when start_notify raises."""
    dev = make_device()
    client = FakeBleakClient(is_connected=True)
    dev._client = client  # type: ignore[assignment]

    async def fail(*args: object, **kwargs: object) -> None:
        raise BleakError("test")

    client.start_notify = fail  # type: ignore[method-assign]
    with caplog.at_level(logging.WARNING):
        assert await dev._try_start_notifications() is False
    assert dev._client is None
    assert "starting notifications failed" in caplog.text
    assert "retrying" in caplog.text


async def test_send_device_info_no_client_returns_false() -> None:
    """Return False when _client is None."""
    dev = make_device()
    dev._client = None
    assert await dev._try_send_device_info() is False


async def test_send_device_info_disconnected_returns_false() -> None:
    """Return False when client is disconnected."""
    dev = make_device()
    dev._client = FakeBleakClient(is_connected=False)  # type: ignore[assignment]
    assert await dev._try_send_device_info() is False


async def test_returns_false_when_send_fails() -> None:
    """Return False and clear client when _send_packet_while_connected fails."""
    dev = make_device()
    dev._client = FakeBleakClient(is_connected=True)  # type: ignore[assignment]
    with patch.object(dev, "_send_packet_while_connected", return_value=False):
        result = await dev._try_send_device_info()
        assert result is False
        assert dev._client is None


async def test_returns_false_on_bleak_error() -> None:
    """Return False and clear client on BLE_CONNECTION_EXCEPTIONS."""
    dev = make_device()
    dev._client = FakeBleakClient(is_connected=True)  # type: ignore[assignment]
    with patch.object(
        dev,
        "_send_packet_while_connected",
        side_effect=BleakError("test"),
    ):
        result = await dev._try_send_device_info()
        assert result is False
        assert dev._client is None


async def test_send_pairing_no_client_returns_false() -> None:
    """Return False when _client is None."""
    dev = make_device()
    dev._client = None
    dev._device_info = make_credentials()
    dev._local_key = b"abcdef"
    assert await dev._try_send_pairing() is False


async def test_send_pairing_disconnected_returns_false() -> None:
    """Return False when client is disconnected."""
    dev = make_device()
    dev._client = FakeBleakClient(is_connected=False)  # type: ignore[assignment]
    dev._device_info = make_credentials()
    dev._local_key = b"abcdef"
    assert await dev._try_send_pairing() is False


async def test_send_pairing_send_fails_returns_false() -> None:
    """Return False and clear client when send fails."""
    dev = make_device()
    dev._client = FakeBleakClient(is_connected=True)  # type: ignore[assignment]
    dev._device_info = make_credentials()
    dev._local_key = b"abcdef"
    with patch.object(dev, "_send_packet_while_connected", return_value=False):
        result = await dev._try_send_pairing()
        assert result is False
        assert dev._client is None


async def test_send_pairing_bleak_error_returns_false() -> None:
    """Return False and clear client on BLE_CONNECTION_EXCEPTIONS."""
    dev = make_device()
    dev._client = FakeBleakClient(is_connected=True)  # type: ignore[assignment]
    dev._device_info = make_credentials()
    dev._local_key = b"abcdef"
    with patch.object(
        dev,
        "_send_packet_while_connected",
        side_effect=BleakError("test"),
    ):
        result = await dev._try_send_pairing()
        assert result is False
        assert dev._client is None


def test_no_client() -> None:
    """Log error when client is None."""
    dev = make_device()
    dev._client = None
    dev._log_connection_status()


def test_not_connected() -> None:
    """Log error when client is not connected."""
    dev = make_device()
    dev._client = FakeBleakClient(is_connected=False)  # type: ignore[assignment]
    dev._log_connection_status()


def test_connected_not_paired() -> None:
    """Log error when connected but not paired."""
    dev = make_device()
    dev._client = FakeBleakClient(is_connected=True)  # type: ignore[assignment]
    dev._is_paired = False
    dev._log_connection_status()


async def test_returns_on_expected_disconnect() -> None:
    """Return early if expected_disconnect is True."""
    dev = make_device()
    dev._expected_disconnect = True
    await dev._reconnect()


async def test_reconnect_after_expected_disconnect_set() -> None:
    """Return if expected_disconnect becomes True during reconnect."""
    dev = make_device(manager=FakeBLEManager(make_credentials()))
    dev._expected_disconnect = True
    await dev._reconnect()


async def test_reconnect_success() -> None:
    """_reconnect completes successfully when _ensure_connected works."""
    dev = make_device(manager=FakeBLEManager(make_credentials()))
    dev._client = FakeBleakClient(is_connected=True)  # type: ignore[assignment]
    dev._is_paired = True
    await dev._reconnect()


async def test_reconnect_with_bleak_error() -> None:
    """Back off and retry on BLEAK_EXCEPTIONS."""
    dev = make_device(manager=FakeBLEManager(make_credentials()))
    with (
        patch.object(
            dev,
            "_ensure_connected",
            side_effect=BleakError("test"),
        ),
        patch("asyncio.create_task", side_effect=_close_task) as mock_task,
        patch("asyncio.sleep"),
    ):
        await dev._reconnect()
        mock_task.assert_called()


async def test_raises_after_all_retries_fail() -> None:
    """Raise BleakNotFoundError after 100 failed attempts."""
    dev = make_device(manager=FakeBLEManager(make_credentials()))
    with (
        patch.object(dev, "_try_connect_and_configure", return_value=False),
        pytest.raises(BleakNotFoundError),
    ):
        await dev._connect_with_retries()


# --- tuya_ble.py additional coverage ---


async def test_returns_false_when_manager_returns_none() -> None:
    """Returns False when get_device_credentials returns None."""
    dev = make_device(manager=FakeBLEManager(None))
    result = await dev._update_device_info()
    assert result is False
    assert dev._device_info is None


def test_disconnected_returns_early_when_expected_disconnect() -> None:
    """Expected disconnect returns without scheduling reconnect."""
    dev = make_device()
    dev._expected_disconnect = True
    dev._is_paired = True
    client = FakeBleakClient(is_connected=True)
    dev._client = client  # type: ignore[assignment]
    dev._disconnected(client)  # type: ignore[arg-type]
    assert dev._is_paired is False


def test_unexpected_disconnect_schedules_reconnect() -> None:
    """Unexpected disconnect with was_paired schedules reconnect."""
    dev = make_device(manager=FakeBLEManager(make_credentials()))
    dev._is_paired = True
    client = FakeBleakClient(is_connected=True)
    dev._client = client  # type: ignore[assignment]
    with patch("asyncio.create_task", side_effect=_close_task) as mock_task:
        dev._disconnected(client)  # type: ignore[arg-type]
        mock_task.assert_called_once()
    assert dev._client is None
    assert dev._is_paired is False


async def test_lock_debug_log() -> None:
    """Debug log fires when connect_lock is already held."""
    dev = make_device()
    dev._expected_disconnect = False
    async with dev._connect_lock:
        with patch.object(dev, "_is_ready", return_value=True):
            await dev._ensure_connected()


async def test_expected_disconnect_after_ensure() -> None:
    """Return early when expected_disconnect becomes True during ensure."""
    dev = make_device(manager=FakeBLEManager(make_credentials()))
    dev._client = FakeBleakClient(is_connected=True)  # type: ignore[assignment]
    dev._is_paired = True

    async def set_disconnect() -> None:
        dev._expected_disconnect = True

    with patch.object(dev, "_ensure_connected", side_effect=set_disconnect):
        await dev._reconnect()
    assert dev._expected_disconnect is True


async def test_bleak_error_retries() -> None:
    """BLEAK_EXCEPTIONS triggers backoff and retry."""
    dev = make_device(manager=FakeBLEManager(make_credentials()))
    call_count = 0

    async def fail_then_succeed() -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            dev._expected_disconnect = True

    with (
        patch.object(dev, "_ensure_connected", side_effect=fail_then_succeed),
        patch("asyncio.sleep"),
        patch("asyncio.create_task", side_effect=_close_task),
    ):
        await dev._reconnect()


async def test_no_retry_when_disconnect_during_backoff() -> None:
    """Do not reschedule reconnect when disconnect is set during backoff."""
    dev = make_device(manager=FakeBLEManager(make_credentials()))

    async def set_disconnect_during_backoff(_: float) -> None:
        dev._expected_disconnect = True

    with (
        patch.object(
            dev,
            "_ensure_connected",
            side_effect=BleakError("test"),
        ),
        patch("asyncio.sleep", side_effect=set_disconnect_during_backoff),
        patch("asyncio.create_task", side_effect=_close_task) as mock_task,
    ):
        await dev._reconnect()
    mock_task.assert_not_called()


# ---------------------------------------------------------------------------
# protocol_mixin.py coverage helpers
# ---------------------------------------------------------------------------


async def test_timeout_returns_false() -> None:
    """Return False when response times out."""
    h = ProtocolHarness()
    with (
        patch("asyncio.wait_for", side_effect=TimeoutError),
        patch("asyncio.create_task", side_effect=_close_task),
    ):
        result = await h.device._send_packet_while_connected(
            TuyaBLECode.FUN_SENDER_DEVICE_STATUS, b"", 0, True
        )
        assert result is False


async def test_no_wait_for_response() -> None:
    """Return True without waiting when wait_for_response is False."""
    h = ProtocolHarness()
    h.device._client = h.client  # type: ignore[assignment]
    result = await h.device._send_packet_while_connected(
        TuyaBLECode.FUN_SENDER_DEVICE_STATUS, b"", 0, False
    )
    assert result is True


async def test_bleak_not_found_raises() -> None:
    """BleakNotFoundError propagates from _send_packets_locked."""
    h = ProtocolHarness()
    with (
        patch.object(
            h.device,
            "_send_packets_locked",
            side_effect=BleakNotFoundError(),
        ),
        pytest.raises(BleakNotFoundError),
    ):
        await h.device._int_send_packet_while_connected([b"\x00"])


async def test_bleak_error_raises() -> None:
    """BleakError propagates from _send_packets_locked."""
    h = ProtocolHarness()
    with (
        patch.object(
            h.device,
            "_send_packets_locked",
            side_effect=BleakError("test"),
        ),
        pytest.raises(BleakError),
    ):
        await h.device._int_send_packet_while_connected([b"\x00"])


async def test_bleak_dbus_error_paired_resends() -> None:
    """BleakDBusError with _is_paired triggers _resend_packets."""
    h = ProtocolHarness()
    h.device._is_paired = True
    ex = BleakDBusError("org.bluez.Error.Failed", ["test"])

    async def fail(*args: object, **kwargs: object) -> None:
        raise ex

    with (
        patch.object(h.device, "_int_send_packets_locked", side_effect=fail),
        patch("asyncio.sleep"),
        patch("asyncio.create_task", side_effect=_close_task),
        pytest.raises(BleakError),
    ):
        await h.device._send_packets_locked([b"\x00"])


async def test_bleak_dbus_error_not_paired_reconnects() -> None:
    """BleakDBusError without _is_paired triggers _reconnect."""
    h = ProtocolHarness()
    h.device._is_paired = False
    ex = BleakDBusError("org.bluez.Error.Failed", ["test"])

    async def fail(*args: object, **kwargs: object) -> None:
        raise ex

    with (
        patch.object(h.device, "_int_send_packets_locked", side_effect=fail),
        patch("asyncio.sleep"),
        patch("asyncio.create_task", side_effect=_close_task),
        pytest.raises(BleakError),
    ):
        await h.device._send_packets_locked([b"\x00"])


async def test_bleak_error_paired_resends() -> None:
    """BleakError with _is_paired triggers _resend_packets."""
    h = ProtocolHarness()
    h.device._is_paired = True

    async def fail(*args: object, **kwargs: object) -> None:
        raise BleakError("test")

    with (
        patch.object(h.device, "_int_send_packets_locked", side_effect=fail),
        patch("asyncio.create_task", side_effect=_close_task),
        pytest.raises(BleakError),
    ):
        await h.device._send_packets_locked([b"\x00"])


async def test_bleak_error_not_paired_reconnects() -> None:
    """BleakError without _is_paired triggers _reconnect."""
    h = ProtocolHarness()
    h.device._is_paired = False

    async def fail(*args: object, **kwargs: object) -> None:
        raise BleakError("test")

    with (
        patch.object(h.device, "_int_send_packets_locked", side_effect=fail),
        patch("asyncio.create_task", side_effect=_close_task),
        pytest.raises(BleakError),
    ):
        await h.device._send_packets_locked([b"\x00"])


async def test_client_none_raises_bleak_error() -> None:
    """Raise BleakError when _client is None during packet send."""
    h = ProtocolHarness()
    h.device._client = None
    with pytest.raises(BleakError):
        await h.device._int_send_packets_locked([b"\x00"])


async def test_write_exception_clears_client() -> None:
    """Clear client and raise BleakError when write raises."""
    h = ProtocolHarness()
    client = FakeBleakClient(is_connected=True)
    h.device._client = client  # type: ignore[assignment]
    h.device._expected_disconnect = True  # prevent _reconnect task

    async def fail_write(char: str, data: bytes, resp: bool) -> None:
        raise OSError("write failed")

    client.write_gatt_char = fail_write  # type: ignore[assignment]
    with (
        patch.object(h.device, "_disconnected") as mock_disc,
        pytest.raises(BleakError),
    ):
        await h.device._int_send_packets_locked([b"\x00"])
    mock_disc.assert_called_once_with(client)


async def test_resend_packets_returns_early_when_expected_disconnect() -> None:
    """Return early when expected_disconnect is True."""
    h = ProtocolHarness()
    h.device._expected_disconnect = True
    await h.device._resend_packets([b"\x00"])


async def test_resend_packets_sends() -> None:
    """Normal _resend_packets call reaches _int_send_packet_while_connected."""
    h = ProtocolHarness()
    h.device._client = h.client  # type: ignore[assignment]
    with patch.object(h.device, "_int_send_packet_while_connected") as mock_send:
        await h.device._resend_packets([b"\x00"])
        mock_send.assert_called_once_with([b"\x00"])


async def test_stale_response_resets() -> None:
    """Unexpected lower packet number resets input buffer."""
    h = ProtocolHarness()
    await h.register_notify()
    # Send packet 0
    resp = frame_packet0(
        encrypt_payload(session_key(h), 5, 1, 0, TuyaBLECode.FUN_RECEIVE_DP, b"\x00")
    )
    h.notify(resp)
    await asyncio.sleep(0)
    # Send packet 0 again (stale) — should reset
    h.notify(resp)
    await asyncio.sleep(0)
    assert h.device._input_buffer is None


def session_key(h: ProtocolHarness) -> bytes:
    """Return the device session key."""
    assert h.device._session_key is not None
    return h.device._session_key


async def test_send_packet_returns_early_when_expected_disconnect() -> None:
    """Return early when expected_disconnect is True."""
    h = ProtocolHarness()
    h.device._expected_disconnect = True
    await h.device._send_packet(TuyaBLECode.FUN_SENDER_DEVICE_STATUS, b"")


async def test_send_packet_expected_disconnect_after_ensure() -> None:
    """_send_packet returns when expected_disconnect becomes True during ensure."""
    h = ProtocolHarness()
    h.device._client = h.client  # type: ignore[assignment]
    h.device._is_paired = True

    async def set_disconnect() -> None:
        h.device._expected_disconnect = True

    with patch.object(h.device, "_ensure_connected", side_effect=set_disconnect):
        await h.device._send_packet(TuyaBLECode.FUN_SENDER_DEVICE_STATUS, b"")


async def test_send_response_when_connected() -> None:
    """Send response when client is connected."""
    h = ProtocolHarness()
    await h.device._send_response(TuyaBLECode.FUN_RECEIVE_DP, b"\x00", 1)
    assert len(h.writes()) >= 1


async def test_send_response_when_not_connected() -> None:
    """Send response when client is not connected — no-op."""
    h = ProtocolHarness()
    h.device._client = None
    await h.device._send_response(TuyaBLECode.FUN_RECEIVE_DP, b"\x00", 1)


def test_auth_key() -> None:
    """Return auth key for security_flag 1."""
    h = ProtocolHarness()
    h.device._auth_key = b"\x01" * 32
    assert h.device._get_key(1) == b"\x01" * 32


def test_login_key() -> None:
    """Return login key for security_flag 4."""
    h = ProtocolHarness()
    assert h.device._get_key(4) == h.device._login_key


def test_session_key() -> None:
    """Return session key for security_flag 5."""
    h = ProtocolHarness()
    assert h.device._get_key(5) == h.device._session_key


def test_invalid_security_flag() -> None:
    """Raise TuyaBLEDataFormatError for unknown security flag."""
    h = ProtocolHarness()
    with pytest.raises(TuyaBLEDataFormatError):
        h.device._get_key(9)


def test_response_to_zero_returns_early() -> None:
    """Return early when response_to is 0."""
    h = ProtocolHarness()
    h.device._resolve_expected_response(0, 0)


def test_no_future_found() -> None:
    """Return early when no matching future exists."""
    h = ProtocolHarness()
    h.device._resolve_expected_response(99, 0)


def test_nonzero_result_sets_exception() -> None:
    """Set TuyaBLEDeviceError on the future for non-zero result."""
    h = ProtocolHarness()
    future: asyncio.Future[int] = asyncio.get_event_loop().create_future()
    h.device._input_expected_responses[1] = future
    h.device._resolve_expected_response(1, 1)
    assert future.done()
    with pytest.raises(TuyaBLEDeviceError):
        future.result()


def test_valid_packet_with_extra_crc_data() -> None:
    """Valid CRC on extra-length data should not raise."""
    h = ProtocolHarness()
    raw = make_raw(
        1,
        0,
        TuyaBLECode.FUN_SENDER_DEVICE_STATUS.value,
        b"\x00",
        extra_after_crc=b"\x00" * 4,
    )
    result = h.device._validate_and_parse_packet(raw)
    assert result is not None
    seq_num, _response_to, code, _data = result
    assert seq_num == 1
    assert code == TuyaBLECode.FUN_SENDER_DEVICE_STATUS


def test_invalid_crc_raises() -> None:
    """Invalid CRC raises TuyaBLEDataCRCError."""
    h = ProtocolHarness()
    raw = make_raw(
        1,
        0,
        TuyaBLECode.FUN_SENDER_DEVICE_STATUS.value,
        b"\x00",
        crc_override=0,
    )
    with pytest.raises(TuyaBLEDataCRCError):
        h.device._validate_and_parse_packet(raw)


def test_short_data_raises() -> None:
    """Data shorter than claimed length raises TuyaBLEDataLengthError."""
    h = ProtocolHarness()
    with pytest.raises(TuyaBLEDataLengthError):
        h.device._parse_timestamp(b"", 0)


def test_time_type_one_short_data_raises() -> None:
    """Time type 1 with insufficient data raises TuyaBLEDataLengthError."""
    h = ProtocolHarness()
    with pytest.raises(TuyaBLEDataLengthError):
        h.device._parse_timestamp(b"\x01\x00\x00", 0)


def test_unknown_code_returns_none() -> None:
    """Unknown code returns None."""
    h = ProtocolHarness()
    raw = make_raw(1, 0, 0x9999, b"\x00")
    assert h.device._validate_and_parse_packet(raw) is None


async def test_client_becomes_none_during_send() -> None:
    """Raise BleakError when _client is None at start of packet loop."""
    h = ProtocolHarness()
    h.device._client = None
    with pytest.raises(BleakError):
        await h.device._int_send_packets_locked([b"\x00", b"\x01"])


# --- protocol_mixin.py additional coverage ---


async def test_resend_packets_expected_disconnect_during_ensure() -> None:
    """Return early when expected_disconnect becomes True during ensure."""
    h = ProtocolHarness()

    async def set_disconnect() -> None:
        h.device._expected_disconnect = True

    with patch.object(h.device, "_ensure_connected", side_effect=set_disconnect):
        await h.device._resend_packets([b"\x00"])


def test_string_datapoint() -> None:
    """DT_STRING datapoint is parsed correctly."""
    h = ProtocolHarness()
    dp_id = 1
    dp_type = 3
    raw_value = b"hello"
    dp_data = (
        pack_varint(dp_id)
        + pack(">B", dp_type)
        + pack_varint(len(raw_value))
        + raw_value
    )
    raw = make_raw(1, 0, TuyaBLECode.FUN_RECEIVE_DP.value, dp_data)
    result = h.device._validate_and_parse_packet(raw)
    assert result is not None
    _seq, _resp, code, _data = result
    assert code == TuyaBLECode.FUN_RECEIVE_DP


async def test_stale_packet_number_resets() -> None:
    """Stale packet number (< expected) resets buffer."""
    h = ProtocolHarness()
    await h.register_notify()
    h.device._input_expected_packet_num = 2
    h.device._input_buffer = bytearray()
    h.device._input_expected_length = 100
    p1 = pack_varint(1) + b"\x00" * 20
    h.notify(p1)
    assert h.device._input_buffer is None


async def test_missing_packet_resets() -> None:
    """Missing intermediate packet resets buffer."""
    h = ProtocolHarness()
    await h.register_notify()
    resp = frame_packet0(
        encrypt_payload(session_key(h), 5, 1, 0, TuyaBLECode.FUN_RECEIVE_DP, b"\x00")
    )
    h.notify(resp)
    await asyncio.sleep(0)
    p2 = pack_varint(2) + b"\x00" * 20
    h.notify(p2)
    await asyncio.sleep(0)
    assert h.device._input_buffer is None


async def test_locked_log_message() -> None:
    """Debug log fires when operation_lock is already held."""
    h = ProtocolHarness()
    h.device._client = h.client  # type: ignore[assignment]

    mock_lock = AsyncMock()
    mock_lock.locked.return_value = True
    mock_lock.__aenter__ = AsyncMock(return_value=None)
    mock_lock.__aexit__ = AsyncMock(return_value=False)
    h.device._operation_lock = mock_lock

    with patch.object(h.device, "_send_packets_locked"):
        await h.device._int_send_packet_while_connected([b"\x00"])


# ---------------------------------------------------------------------------
# Regression tests for the P0/P1 reconnect-lifecycle fixes (issue #66)
# ---------------------------------------------------------------------------


async def test_one_failed_write_spawns_one_reconnect() -> None:
    """A failed GATT write must not schedule two reconnect tasks."""
    h = ProtocolHarness()
    client = FakeBleakClient(is_connected=True)
    h.device._client = client  # type: ignore[assignment]
    h.device._is_paired = True
    scheduled: list[asyncio.Task[None]] = []
    created: list[object] = []

    real_create_task = asyncio.create_task

    def record(coro: object) -> asyncio.Task[None]:
        created.append(coro)
        # Close any coroutines that won't run so we don't leave unawaited ones
        if hasattr(coro, "close"):
            coro.close()
        task = real_create_task(asyncio.sleep(0))
        scheduled.append(task)
        return task

    async def fail_write(char: str, data: bytes, resp: bool) -> None:
        raise OSError("write failed")

    client.write_gatt_char = fail_write  # type: ignore[assignment]

    with (
        patch("asyncio.create_task", side_effect=record),
        pytest.raises(BleakError),
    ):
        await h.device._send_packets_locked([b"\x00"])

    await asyncio.gather(*scheduled, return_exceptions=True)
    assert len(created) == 1
    assert len(scheduled) == 1


async def test_repeated_failures_do_not_multiply_reconnect_tasks() -> None:
    """Successive failures must never leave more than one reconnect in flight."""
    dev = make_device(manager=FakeBLEManager(make_credentials()))
    dev._is_paired = False

    for _ in range(5):
        assert dev._reconnect_task is None or not dev._reconnect_task.done()
        dev._schedule_reconnect()

    assert dev._reconnect_task is not None
    assert not dev._reconnect_task.done()

    dev._expected_disconnect = True
    await dev.stop()
    assert dev._reconnect_task.cancelled()


async def test_schedule_reconnect_skipped_when_stopping() -> None:
    """No reconnect may be scheduled once an intentional stop is under way."""
    dev = make_device(manager=FakeBLEManager(make_credentials()))
    dev._expected_disconnect = True
    dev._schedule_reconnect()
    assert dev._reconnect_task is None


async def test_schedule_resend_does_not_multiply() -> None:
    """Only one resend task may be outstanding at a time."""
    dev = make_device(manager=FakeBLEManager(make_credentials()))
    dev._schedule_resend([b"\x00"])
    first = dev._resend_task
    assert first is not None
    dev._schedule_resend([b"\x00"])
    assert dev._resend_task is first

    dev._expected_disconnect = True
    await dev.stop()
    assert first.cancelled()


async def test_stop_does_not_wait_for_connect_loop() -> None:
    """stop() must not block behind an in-flight connect attempt."""
    dev = make_device(manager=FakeBLEManager(make_credentials()))
    attempts = 0

    async def slow_attempt() -> bool:
        nonlocal attempts
        attempts += 1
        await asyncio.sleep(0.05)
        return False

    async def run() -> None:
        await dev.stop()

    with patch.object(dev, "_try_connect_and_configure", side_effect=slow_attempt):
        task = asyncio.ensure_future(dev._connect_with_retries())
        await asyncio.sleep(0.01)
        stopper = asyncio.ensure_future(run())
        await asyncio.sleep(0.15)

    assert dev._expected_disconnect is True
    assert stopper.done()
    assert attempts <= 2
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    stopper.cancel()
    with suppress(asyncio.CancelledError):
        await stopper


async def test_connect_with_retries_aborts_on_expected_disconnect() -> None:
    """The 100-attempt loop must bail out as soon as a stop is requested."""
    dev = make_device(manager=FakeBLEManager(make_credentials()))
    attempts = 0

    async def attempt() -> bool:
        nonlocal attempts
        attempts += 1
        dev._expected_disconnect = True
        return False

    with patch.object(dev, "_try_connect_and_configure", side_effect=attempt):
        await dev._connect_with_retries()

    assert attempts == 1


async def test_reconnect_retries_after_tuya_error() -> None:
    """A TuyaBLEError during reconnect must be retried, not terminate the loop."""
    dev = make_device(manager=FakeBLEManager(make_credentials()))
    with (
        patch.object(dev, "_ensure_connected", side_effect=TuyaBLEDeviceError(0)),
        patch("asyncio.sleep"),
        patch("asyncio.create_task", side_effect=_close_task) as mock_task,
    ):
        await dev._reconnect()
    mock_task.assert_called()


async def test_reconnect_retries_after_attribute_error_is_not_swallowed() -> None:
    """AttributeError must escape _reconnect instead of being silently retried."""
    dev = make_device(manager=FakeBLEManager(make_credentials()))

    async def boom(*_args: object, **_kwargs: object) -> None:
        raise AttributeError("typo")

    with (
        patch.object(dev, "_ensure_connected", side_effect=boom),
        pytest.raises(AttributeError),
    ):
        await dev._reconnect()


async def test_background_task_exception_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A tracked task that fails must log, not vanish into "never retrieved"."""

    async def boom() -> None:
        raise TuyaBLEDeviceError(0)

    dev = make_device(manager=FakeBLEManager(make_credentials()))
    with caplog.at_level(logging.WARNING):
        task = dev._spawn_background_task(boom(), "test task")
        with pytest.raises(TuyaBLEDeviceError):
            await task
        await asyncio.sleep(0)

    assert "Unretrieved exception from test task" in caplog.text


async def test_connect_with_retries_aborts_after_last_attempt() -> None:
    """A stop requested during the final attempt must not raise."""
    dev = make_device(manager=FakeBLEManager(make_credentials()))
    attempts = 0

    async def attempt() -> bool:
        nonlocal attempts
        attempts += 1
        if attempts == 100:
            dev._expected_disconnect = True
        return False

    with patch.object(dev, "_try_connect_and_configure", side_effect=attempt):
        await dev._connect_with_retries()

    assert attempts == 100


async def test_cancel_pending_responses_skips_settled_future() -> None:
    """An already-settled future must not be failed twice."""
    dev = make_device(manager=FakeBLEManager(make_credentials()))
    settled: asyncio.Future[int] = asyncio.Future()
    settled.set_result(1)
    pending: asyncio.Future[int] = asyncio.Future()
    dev._input_expected_responses[1] = settled
    dev._input_expected_responses[2] = pending

    dev._cancel_pending_responses()

    assert not dev._input_expected_responses
    assert settled.result() == 1
    assert pending.done()


async def test_ensure_connected_logs_status_after_connecting() -> None:
    """A successful connect must report the resulting connection status."""
    dev = make_device(manager=FakeBLEManager(make_credentials()))
    client = FakeBleakClient(is_connected=True)

    async def connect_once() -> bool:
        dev._client = client  # type: ignore[assignment]
        dev._is_paired = True
        return True

    with patch.object(dev, "_try_connect_and_configure", side_effect=connect_once):
        await dev._ensure_connected()

    assert dev._is_ready()


async def test_ensure_connected_returns_when_connected_while_waiting() -> None:
    """Becoming ready inside the connect lock must short-circuit the retries."""
    dev = make_device(manager=FakeBLEManager(make_credentials()))
    client = FakeBleakClient(is_connected=True)

    real_sleep = asyncio.sleep

    async def become_ready(delay: float) -> None:
        await real_sleep(0)
        dev._client = client  # type: ignore[assignment]
        dev._is_paired = True

    with (
        patch("asyncio.sleep", side_effect=become_ready),
        patch.object(dev, "_try_connect_and_configure") as attempt,
    ):
        await dev._ensure_connected()

    assert dev._is_ready()
    attempt.assert_not_called()
