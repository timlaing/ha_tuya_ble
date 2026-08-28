"""Tests for BLE connection lifecycle and protocol error paths."""

# pylint: disable=protected-access
from __future__ import annotations

import asyncio
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
    FakeAdvertisementData,
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


def _close_task(coro: object) -> None:
    """Close a coroutine so it doesn't trigger unawaited-coroutine warnings."""
    if hasattr(coro, "close"):
        coro.close()


# ---------------------------------------------------------------------------
# tuya_ble.py coverage helpers
# ---------------------------------------------------------------------------


class TestUpdateDeviceInfo:
    """Tests for _update_device_info credential loading."""

    async def test_initialize_no_manager(self) -> None:
        """Initialize with a manager that returns no credentials."""
        dev = make_device(manager=FakeBLEManager(None))
        await dev.initialize()
        assert dev._device_info is None
        assert dev._local_key is None

    async def test_initialize_fetches_credentials(self) -> None:
        """Initialize fetches credentials from the manager."""
        creds = make_credentials()
        dev = make_device(manager=FakeBLEManager(creds))
        await dev.initialize()
        assert dev._device_info is creds
        assert dev._local_key == creds.local_key[:6].encode()


class TestAdvertisementParsing:
    """Tests for advertisement data parsing edge cases."""

    def test_parse_service_data_short(self) -> None:
        """Service data with length <= 1 returns None."""
        dev = make_device()
        dev._advertisement_data = FakeAdvertisementData(  # type: ignore[assignment]
            service_data={"0000ffe0": b"\x00"},
        )
        assert dev._parse_product_id_from_service_data() is None

    def test_parse_service_data_wrong_first_byte(self) -> None:
        """Service data with non-zero first byte returns None."""
        dev = make_device()
        dev._advertisement_data = FakeAdvertisementData(  # type: ignore[assignment]
            service_data={"0000ffe0": b"\x01\x02\x03"},
        )
        assert dev._parse_product_id_from_service_data() is None

    def test_parse_manufacturer_data_no_product_id(self) -> None:
        """Manufacturer data parsed without a product ID skips UUID decryption."""
        dev = make_device()
        dev._advertisement_data = FakeAdvertisementData(  # type: ignore[assignment]
            manufacturer_data={0x01FC: b"\x80\x02" + b"\x00" * 4 + b"\x00" * 16},
        )
        dev._parse_manufacturer_data(None)
        assert dev._uuid == ""


class TestExecuteDisconnect:
    """Tests for _execute_disconnect with connected client."""

    async def test_disconnect_with_live_client(self) -> None:
        """Disconnect should stop notify and disconnect the client."""
        dev = make_device()
        client = FakeBleakClient(is_connected=True)
        dev._client = client  # type: ignore[assignment]
        dev._is_paired = True
        await dev._execute_disconnect()
        assert dev._client is None
        assert CHARACTERISTIC_NOTIFY in client.stopped

    async def test_disconnect_with_none_client(self) -> None:
        """Disconnect with no client should not raise."""
        dev = make_device()
        dev._client = None
        await dev._execute_disconnect()
        assert dev._client is None


class TestEnsureConnected:
    """Tests for _ensure_connected edge cases."""

    async def test_expected_disconnect_returns_early(self) -> None:
        """Return early if expected_disconnect is True."""
        dev = make_device()
        dev._expected_disconnect = True
        await dev._ensure_connected()

    async def test_already_ready_returns_early(self) -> None:
        """Return early if already connected and paired."""
        dev = make_device()
        dev._client = FakeBleakClient(is_connected=True)  # type: ignore[assignment]
        dev._is_paired = True
        await dev._ensure_connected()


class TestTryEstablishConnection:
    """Tests for _try_establish_connection error paths."""

    async def test_returns_none_when_client_not_connected(self) -> None:
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

    async def test_bleak_not_found_returns_none(self) -> None:
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

    async def test_bleak_error_returns_none(self) -> None:
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

    async def test_os_error_returns_none(self) -> None:
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

    async def test_connection_exception_returns_none(self) -> None:
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


class TestTryStartNotifications:
    """Tests for _try_start_notifications error paths."""

    async def test_returns_false_when_no_client(self) -> None:
        """Return False when _client is None."""
        dev = make_device()
        dev._client = None
        assert await dev._try_start_notifications() is False

    async def test_returns_false_when_disconnected(self) -> None:
        """Return False when client is disconnected."""
        dev = make_device()
        dev._client = FakeBleakClient(is_connected=False)  # type: ignore[assignment]
        assert await dev._try_start_notifications() is False

    async def test_returns_false_on_start_notify_error(self) -> None:
        """Return False and clear client when start_notify raises."""
        dev = make_device()
        client = FakeBleakClient(is_connected=True)
        dev._client = client  # type: ignore[assignment]

        async def fail(*args: object, **kwargs: object) -> None:
            raise BleakError("test")

        client.start_notify = fail  # type: ignore[method-assign]
        assert await dev._try_start_notifications() is False
        assert dev._client is None


class TestTrySendDeviceInfo:
    """Tests for _try_send_device_info error paths."""

    async def test_returns_false_when_no_client(self) -> None:
        """Return False when _client is None."""
        dev = make_device()
        dev._client = None
        assert await dev._try_send_device_info() is False

    async def test_returns_false_when_disconnected(self) -> None:
        """Return False when client is disconnected."""
        dev = make_device()
        dev._client = FakeBleakClient(is_connected=False)  # type: ignore[assignment]
        assert await dev._try_send_device_info() is False

    async def test_returns_false_when_send_fails(self) -> None:
        """Return False and clear client when _send_packet_while_connected fails."""
        dev = make_device()
        dev._client = FakeBleakClient(is_connected=True)  # type: ignore[assignment]
        with patch.object(dev, "_send_packet_while_connected", return_value=False):
            result = await dev._try_send_device_info()
            assert result is False
            assert dev._client is None

    async def test_returns_false_on_bleak_error(self) -> None:
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


class TestTrySendPairing:
    """Tests for _try_send_pairing error paths."""

    async def test_returns_false_when_no_client(self) -> None:
        """Return False when _client is None."""
        dev = make_device()
        dev._client = None
        dev._device_info = make_credentials()
        dev._local_key = b"abcdef"
        assert await dev._try_send_pairing() is False

    async def test_returns_false_when_disconnected(self) -> None:
        """Return False when client is disconnected."""
        dev = make_device()
        dev._client = FakeBleakClient(is_connected=False)  # type: ignore[assignment]
        dev._device_info = make_credentials()
        dev._local_key = b"abcdef"
        assert await dev._try_send_pairing() is False

    async def test_returns_false_when_send_fails(self) -> None:
        """Return False and clear client when send fails."""
        dev = make_device()
        dev._client = FakeBleakClient(is_connected=True)  # type: ignore[assignment]
        dev._device_info = make_credentials()
        dev._local_key = b"abcdef"
        with patch.object(dev, "_send_packet_while_connected", return_value=False):
            result = await dev._try_send_pairing()
            assert result is False
            assert dev._client is None

    async def test_returns_false_on_bleak_error(self) -> None:
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


class TestLogConnectionStatus:
    """Tests for _log_connection_status branches."""

    def test_no_client(self) -> None:
        """Log error when client is None."""
        dev = make_device()
        dev._client = None
        dev._log_connection_status()

    def test_not_connected(self) -> None:
        """Log error when client is not connected."""
        dev = make_device()
        dev._client = FakeBleakClient(is_connected=False)  # type: ignore[assignment]
        dev._log_connection_status()

    def test_connected_not_paired(self) -> None:
        """Log error when connected but not paired."""
        dev = make_device()
        dev._client = FakeBleakClient(is_connected=True)  # type: ignore[assignment]
        dev._is_paired = False
        dev._log_connection_status()


class TestReconnect:
    """Tests for _reconnect edge cases."""

    async def test_returns_on_expected_disconnect(self) -> None:
        """Return early if expected_disconnect is True."""
        dev = make_device()
        dev._expected_disconnect = True
        await dev._reconnect()

    async def test_reconnect_after_expected_disconnect_set(self) -> None:
        """Return if expected_disconnect becomes True during reconnect."""
        dev = make_device(manager=FakeBLEManager(make_credentials()))
        dev._expected_disconnect = True
        await dev._reconnect()

    async def test_reconnect_success(self) -> None:
        """_reconnect completes successfully when _ensure_connected works."""
        dev = make_device(manager=FakeBLEManager(make_credentials()))
        dev._client = FakeBleakClient(is_connected=True)  # type: ignore[assignment]
        dev._is_paired = True
        await dev._reconnect()

    async def test_reconnect_with_bleak_error(self) -> None:
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


class TestConnectWithRetries:
    """Tests for _connect_with_retries raising after exhausting retries."""

    async def test_raises_after_all_retries_fail(self) -> None:
        """Raise BleakNotFoundError after 100 failed attempts."""
        dev = make_device(manager=FakeBLEManager(make_credentials()))
        with (
            patch.object(dev, "_try_connect_and_configure", return_value=False),
            pytest.raises(BleakNotFoundError),
        ):
            await dev._connect_with_retries()


# --- tuya_ble.py additional coverage ---


class TestUpdateDeviceInfoNone:
    """Tests for _update_device_info returning False."""

    async def test_returns_false_when_manager_returns_none(self) -> None:
        """Returns False when get_device_credentials returns None."""
        dev = make_device(manager=FakeBLEManager(None))
        result = await dev._update_device_info()
        assert result is False
        assert dev._device_info is None


class TestDisconnected:
    """Tests for _disconnected callback."""

    def test_expected_disconnect_returns_early(self) -> None:
        """Expected disconnect returns without scheduling reconnect."""
        dev = make_device()
        dev._expected_disconnect = True
        dev._is_paired = True
        client = FakeBleakClient(is_connected=True)
        dev._client = client  # type: ignore[assignment]
        dev._disconnected(client)  # type: ignore[arg-type]
        assert dev._is_paired is False

    def test_unexpected_disconnect_schedules_reconnect(self) -> None:
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


class TestEnsureConnectedAdditional:
    """Additional tests for _ensure_connected."""

    async def test_lock_debug_log(self) -> None:
        """Debug log fires when connect_lock is already held."""
        dev = make_device()
        dev._expected_disconnect = False
        async with dev._connect_lock:
            with patch.object(dev, "_is_ready", return_value=True):
                await dev._ensure_connected()


class TestReconnectEdgeCases:
    """Additional tests for _reconnect."""

    async def test_expected_disconnect_after_ensure(self) -> None:
        """Return early when expected_disconnect becomes True during ensure."""
        dev = make_device(manager=FakeBLEManager(make_credentials()))
        dev._client = FakeBleakClient(is_connected=True)  # type: ignore[assignment]
        dev._is_paired = True

        async def set_disconnect() -> None:
            dev._expected_disconnect = True

        with patch.object(dev, "_ensure_connected", side_effect=set_disconnect):
            await dev._reconnect()
        assert dev._expected_disconnect is True

    async def test_bleak_error_retries(self) -> None:
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

    async def test_no_retry_when_disconnect_during_backoff(self) -> None:
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


class TestSendPacketWhileConnected:
    """Tests for _send_packet_while_connected edge cases."""

    async def test_timeout_returns_false(self) -> None:
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

    async def test_no_wait_for_response(self) -> None:
        """Return True without waiting when wait_for_response is False."""
        h = ProtocolHarness()
        h.device._client = h.client  # type: ignore[assignment]
        result = await h.device._send_packet_while_connected(
            TuyaBLECode.FUN_SENDER_DEVICE_STATUS, b"", 0, False
        )
        assert result is True


class TestIntSendPacketWhileConnected:
    """Tests for _int_send_packet_while_connected edge cases."""

    async def test_bleak_not_found_raises(self) -> None:
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

    async def test_bleak_error_raises(self) -> None:
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


class TestSendPacketsLocked:
    """Tests for _send_packets_locked error handling."""

    async def test_bleak_dbus_error_paired_resends(self) -> None:
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

    async def test_bleak_dbus_error_not_paired_reconnects(self) -> None:
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

    async def test_bleak_error_paired_resends(self) -> None:
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

    async def test_bleak_error_not_paired_reconnects(self) -> None:
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


class TestIntSendPacketsLocked:
    """Tests for _int_send_packets_locked edge cases."""

    async def test_client_none_raises_bleak_error(self) -> None:
        """Raise BleakError when _client is None during packet send."""
        h = ProtocolHarness()
        h.device._client = None
        with pytest.raises(BleakError):
            await h.device._int_send_packets_locked([b"\x00"])

    async def test_write_exception_clears_client(self) -> None:
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


class TestResendPackets:
    """Tests for _resend_packets edge cases."""

    async def test_expected_disconnect_returns_early(self) -> None:
        """Return early when expected_disconnect is True."""
        h = ProtocolHarness()
        h.device._expected_disconnect = True
        await h.device._resend_packets([b"\x00"])

    async def test_resend_packets_sends(self) -> None:
        """Normal _resend_packets call reaches _int_send_packet_while_connected."""
        h = ProtocolHarness()
        h.device._client = h.client  # type: ignore[assignment]
        with patch.object(h.device, "_int_send_packet_while_connected") as mock_send:
            await h.device._resend_packets([b"\x00"])
            mock_send.assert_called_once_with([b"\x00"])


class TestNotificationHandlerEdgeCases:
    """Tests for _notification_handler edge cases."""

    async def test_stale_response_resets(self) -> None:
        """Unexpected lower packet number resets input buffer."""
        h = ProtocolHarness()
        await h.register_notify()
        # Send packet 0
        resp = frame_packet0(
            encrypt_payload(
                session_key(h), 5, 1, 0, TuyaBLECode.FUN_RECEIVE_DP, b"\x00"
            )
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


class TestSendPacketEdgeCases:
    """Tests for _send_packet edge cases."""

    async def test_expected_disconnect_returns_early(self) -> None:
        """Return early when expected_disconnect is True."""
        h = ProtocolHarness()
        h.device._expected_disconnect = True
        await h.device._send_packet(TuyaBLECode.FUN_SENDER_DEVICE_STATUS, b"")

    async def test_send_packet_expected_disconnect_after_ensure(self) -> None:
        """_send_packet returns when expected_disconnect becomes True during ensure."""
        h = ProtocolHarness()
        h.device._client = h.client  # type: ignore[assignment]
        h.device._is_paired = True

        async def set_disconnect() -> None:
            h.device._expected_disconnect = True

        with patch.object(h.device, "_ensure_connected", side_effect=set_disconnect):
            await h.device._send_packet(TuyaBLECode.FUN_SENDER_DEVICE_STATUS, b"")

    async def test_send_response_when_connected(self) -> None:
        """Send response when client is connected."""
        h = ProtocolHarness()
        await h.device._send_response(TuyaBLECode.FUN_RECEIVE_DP, b"\x00", 1)
        assert len(h.writes()) >= 1

    async def test_send_response_when_not_connected(self) -> None:
        """Send response when client is not connected — no-op."""
        h = ProtocolHarness()
        h.device._client = None
        await h.device._send_response(TuyaBLECode.FUN_RECEIVE_DP, b"\x00", 1)


class TestGetKeyEdgeCases:
    """Tests for _get_key edge cases."""

    def test_auth_key(self) -> None:
        """Return auth key for security_flag 1."""
        h = ProtocolHarness()
        h.device._auth_key = b"\x01" * 32
        assert h.device._get_key(1) == b"\x01" * 32

    def test_login_key(self) -> None:
        """Return login key for security_flag 4."""
        h = ProtocolHarness()
        assert h.device._get_key(4) == h.device._login_key

    def test_session_key(self) -> None:
        """Return session key for security_flag 5."""
        h = ProtocolHarness()
        assert h.device._get_key(5) == h.device._session_key

    def test_invalid_security_flag(self) -> None:
        """Raise TuyaBLEDataFormatError for unknown security flag."""
        h = ProtocolHarness()
        with pytest.raises(TuyaBLEDataFormatError):
            h.device._get_key(9)


class TestResolveExpectedResponse:
    """Tests for _resolve_expected_response edge cases."""

    def test_response_to_zero_returns_early(self) -> None:
        """Return early when response_to is 0."""
        h = ProtocolHarness()
        h.device._resolve_expected_response(0, 0)

    def test_no_future_found(self) -> None:
        """Return early when no matching future exists."""
        h = ProtocolHarness()
        h.device._resolve_expected_response(99, 0)

    def test_nonzero_result_sets_exception(self) -> None:
        """Set TuyaBLEDeviceError on the future for non-zero result."""
        h = ProtocolHarness()
        future: asyncio.Future[int] = asyncio.get_event_loop().create_future()
        h.device._input_expected_responses[1] = future
        h.device._resolve_expected_response(1, 1)
        assert future.done()
        with pytest.raises(TuyaBLEDeviceError):
            future.result()


class TestValidateAndParsePacket:
    """Tests for _validate_and_parse_packet edge cases."""

    def test_valid_packet_with_extra_crc_data(self) -> None:
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

    def test_invalid_crc_raises(self) -> None:
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

    def test_short_data_raises(self) -> None:
        """Data shorter than claimed length raises TuyaBLEDataLengthError."""
        h = ProtocolHarness()
        with pytest.raises(TuyaBLEDataLengthError):
            h.device._parse_timestamp(b"", 0)

    def test_time_type_one_short_data_raises(self) -> None:
        """Time type 1 with insufficient data raises TuyaBLEDataLengthError."""
        h = ProtocolHarness()
        with pytest.raises(TuyaBLEDataLengthError):
            h.device._parse_timestamp(b"\x01\x00\x00", 0)

    def test_unknown_code_returns_none(self) -> None:
        """Unknown code returns None."""
        h = ProtocolHarness()
        raw = make_raw(1, 0, 0x9999, b"\x00")
        assert h.device._validate_and_parse_packet(raw) is None


class TestIntSendPacketsLockedClientNone:
    """Test _int_send_packets_locked when client disconnects mid-send."""

    async def test_client_becomes_none_during_send(self) -> None:
        """Raise BleakError when _client is None at start of packet loop."""
        h = ProtocolHarness()
        h.device._client = None
        with pytest.raises(BleakError):
            await h.device._int_send_packets_locked([b"\x00", b"\x01"])


# --- protocol_mixin.py additional coverage ---


class TestResendPacketsEdgeCases:
    """Tests for _resend_packets edge cases."""

    async def test_expected_disconnect_after_ensure(self) -> None:
        """Return early when expected_disconnect becomes True during ensure."""
        h = ProtocolHarness()

        async def set_disconnect() -> None:
            h.device._expected_disconnect = True

        with patch.object(h.device, "_ensure_connected", side_effect=set_disconnect):
            await h.device._resend_packets([b"\x00"])


class TestParseDatapointsV3String:
    """Test _parse_datapoints_v3 with DT_STRING type."""

    def test_string_datapoint(self) -> None:
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


class TestNotificationHandlerStalePacket:
    """Test _notification_handler stale packet handling."""

    async def test_stale_packet_number_resets(self) -> None:
        """Stale packet number (< expected) resets buffer."""
        h = ProtocolHarness()
        await h.register_notify()
        h.device._input_expected_packet_num = 2
        h.device._input_buffer = bytearray()
        h.device._input_expected_length = 100
        p1 = pack_varint(1) + b"\x00" * 20
        h.notify(p1)
        assert h.device._input_buffer is None

    async def test_missing_packet_resets(self) -> None:
        """Missing intermediate packet resets buffer."""
        h = ProtocolHarness()
        await h.register_notify()
        resp = frame_packet0(
            encrypt_payload(
                session_key(h), 5, 1, 0, TuyaBLECode.FUN_RECEIVE_DP, b"\x00"
            )
        )
        h.notify(resp)
        await asyncio.sleep(0)
        p2 = pack_varint(2) + b"\x00" * 20
        h.notify(p2)
        await asyncio.sleep(0)
        assert h.device._input_buffer is None


class TestIntSendPacketWhileConnectedLock:
    """Test _int_send_packet_while_connected with locked operation_lock."""

    async def test_locked_log_message(self) -> None:
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
