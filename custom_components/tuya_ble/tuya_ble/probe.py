"""Bounded, isolated authenticated BLE status read for cloud identity matching."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from enum import StrEnum
from functools import partial
import logging
from typing import Any, cast

from bleak.backends.client import BaseBleakClient
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from bleak_retry_connector import (
    BleakAbortedError,
    BleakClientWithServiceCache,
    BleakConnectionError,
    BleakOutOfConnectionSlotsError,
    establish_connection,
)

from .advertisement import decode_tuya_ble_advertisement
from .base import TuyaBLEDevice, global_connect_lock
from .const import CHARACTERISTIC_NOTIFY, CHARACTERISTIC_WRITE, TuyaBLECode
from .datapoints import TuyaBLEDataPoint
from .exceptions import TuyaBLEDataFormatError, TuyaBLEDeviceError
from .manager import TuyaBLEDeviceCredentials

_LOGGER = logging.getLogger(__name__)
PROBE_TIMEOUT = 12
PROBE_CLEANUP_TIMEOUT = 5
_ProbeCleanup = tuple[asyncio.Task[None], BleakClientWithServiceCache | None]
_pending_probe_cleanup: dict[str, _ProbeCleanup] = {}


def _consume_cleanup_result(address: str, task: asyncio.Task[None]) -> None:
    """Observe a late cleanup failure without logging transport exceptions."""
    if not task.cancelled():
        task.exception()
    pending = _pending_probe_cleanup.get(address)
    if (
        pending is not None
        and pending[0] is task
        and _cleanup_finished(pending)
        and not (
            isinstance(pending[1], _ProbeClient)
            and pending[1].needs_transport_confirmation
        )
    ):
        _pending_probe_cleanup.pop(address, None)


def _cleanup_finished(pending: _ProbeCleanup) -> bool:
    """Require completed cleanup and a closed link before another probe connects."""
    task, client = pending
    return task.done() and (client is None or not client.is_connected)


async def _async_confirm_cleanup(pending: _ProbeCleanup) -> bool:
    """Use current transport state when retained native backend flags may be stale."""
    task, client = pending
    if not task.done():
        return False
    if isinstance(client, _ProbeClient):
        await client.refresh_transport_state()
    return _cleanup_finished(pending)


async def _async_disconnect_retained_client(
    client: BleakClientWithServiceCache,
) -> None:
    """Give a failed backend disconnect one bounded recovery attempt."""
    async with asyncio.timeout(PROBE_CLEANUP_TIMEOUT):
        if isinstance(client, _ProbeClient) and client.needs_transport_confirmation:
            await client.disconnect_retained_links()
        elif client.is_connected:
            await client.disconnect()


async def _async_wait_for_previous_cleanup(address: str) -> bool:
    """Bound the wait for old cleanup without overlapping a new BLE connection."""
    pending = _pending_probe_cleanup.get(address)
    if pending is None:
        return True
    if await _async_confirm_cleanup(pending):
        if _pending_probe_cleanup.get(address) is pending:
            _pending_probe_cleanup.pop(address, None)
        return True
    task, client = pending
    if task.done() and client is not None:
        _LOGGER.debug("Retrying previous BLE probe disconnect")
        recovery = asyncio.create_task(_async_disconnect_retained_client(client))
        pending = (recovery, client)
        _pending_probe_cleanup[address] = pending
        recovery.add_done_callback(partial(_consume_cleanup_result, address))
    # Waiting for task completion does not confuse a cancelled cleanup task
    # with cancellation of this new probe, and does not cancel old cleanup.
    await asyncio.wait((pending[0],), timeout=PROBE_CLEANUP_TIMEOUT)
    if not await _async_confirm_cleanup(pending):
        return False
    if _pending_probe_cleanup.get(address) is pending:
        _pending_probe_cleanup.pop(address, None)
    return True


class ProbeOutcome(StrEnum):
    """Result of an authenticated status read (not a cloud-device guess)."""

    MATCHED = "matched"
    REJECTED = "rejected"
    INCONCLUSIVE = "inconclusive"
    RETRYABLE = "retryable"


class _ProbeClient(BleakClientWithServiceCache):
    """Capture ownership before connect starts and allow one connect call per probe."""

    def __init__(
        self,
        *args: Any,
        capture_client: Callable[[BleakClientWithServiceCache], None],
        **kwargs: Any,
    ) -> None:
        """Register the client before any backend can partially establish its link."""
        self._probe_backends: list[BaseBleakClient] = []
        self._probe_transport_state: dict[int, bool] = {}
        self._probe_native_released: set[int] = set()
        self._probe_proxy_released: set[int] = set()
        self._probe_proxy_sessions: dict[int, tuple[Any, Any]] = {}
        super().__init__(*args, **kwargs)
        self._probe_connect_started = False
        capture_client(self)

    def __setattr__(self, name: str, value: Any) -> None:
        """Retain concrete backends even when a wrapper drops its backend reference."""
        # Home Assistant selects its backend during connect() and clears this
        # reference on failure. Capture it before that failure can hide a link.
        if name == "_backend" and value is not None:
            self._probe_backends.append(value)
            if self._is_proxy_backend(value):
                api_client = getattr(value, "_client", None)
                session = getattr(api_client, "_connection", None)
                if api_client is not None and session is not None:
                    self._probe_proxy_sessions[id(value)] = (api_client, session)
        super().__setattr__(name, value)

    @property
    def is_connected(self) -> bool:
        """Report retained physical links rather than a wrapper's dropped reference."""
        return any(
            self._probe_transport_state.get(id(backend), backend.is_connected)
            or (
                self._probe_connect_started
                and self._is_bluez_backend(backend)
                and id(backend) not in self._probe_native_released
            )
            or (
                self._probe_connect_started
                and self._is_proxy_backend(backend)
                and id(backend) not in self._probe_proxy_released
            )
            for backend in self._probe_backends
        )

    @property
    def needs_transport_confirmation(self) -> bool:
        """Identify transports whose local flags do not confirm physical closure."""
        return any(
            self._is_bluez_backend(backend) or self._is_proxy_backend(backend)
            for backend in self._probe_backends
        )

    @staticmethod
    def _is_bluez_backend(backend: BaseBleakClient) -> bool:
        """Detect the supported native backend without importing Linux-only modules."""
        return any(
            base.__module__ == "bleak.backends.bluezdbus.client"
            for base in type(backend).__mro__
        )

    @staticmethod
    def _is_proxy_backend(backend: BaseBleakClient) -> bool:
        """Identify ESPHome clients that clear local state on cancelled disconnect."""
        return any(
            base.__module__ == "bleak_esphome.backend.client"
            for base in type(backend).__mro__
        )

    async def refresh_transport_state(self) -> None:
        """Confirm native state and terminated proxy sessions independently."""
        for backend in self._probe_backends:
            if self._is_bluez_backend(backend):
                self._probe_transport_state[
                    id(backend)
                ] = await self._async_bluez_connected(backend)
            elif self._is_proxy_backend(backend) and self._proxy_session_ended(backend):
                # ESPHome releases a subscriber's BLE links when its API session
                # ends. A replaced session cannot acknowledge the old disconnect.
                self._probe_proxy_released.add(id(backend))
                self._probe_transport_state[id(backend)] = False

    def _proxy_session_ended(self, backend: BaseBleakClient) -> bool:
        """Require a captured CLOSED session that is no longer the client's owner."""
        captured = self._probe_proxy_sessions.get(id(backend))
        if captured is None:
            return False
        api_client, session = captured
        state = getattr(session, "connection_state", None)
        return (
            not backend.is_connected
            and getattr(state, "name", None) == "CLOSED"
            and getattr(api_client, "_connection", session) is not session
        )

    async def disconnect_retained_links(self) -> None:
        """Recover native links through a live manager instead of a dead session bus."""
        await self.refresh_transport_state()
        for backend in self._probe_backends:
            if self._is_bluez_backend(backend):
                await self._async_release_bluez_backend(backend)
            elif self._is_proxy_backend(backend):
                if id(
                    backend
                ) not in self._probe_proxy_released or self._probe_transport_state.get(
                    id(backend), backend.is_connected
                ):
                    # ESPHome disconnect always awaits the proxy's address-specific
                    # disconnected response, even after its local flag was cleared.
                    await backend.disconnect()
                    self._probe_proxy_released.add(id(backend))
            elif backend.is_connected:
                await backend.disconnect()
        await self.refresh_transport_state()

    async def _async_release_bluez_backend(self, backend: BaseBleakClient) -> None:
        """Cancel native requests, confirm transport closure, and finish teardown."""
        if (
            id(backend) in self._probe_native_released
            and not self._probe_transport_state[id(backend)]
        ):
            return
        await self._async_disconnect_bluez_backend(backend)
        self._probe_transport_state[id(backend)] = await self._async_bluez_connected(
            backend
        )
        if not self._probe_transport_state[id(backend)]:
            await self._async_teardown_bluez_backend(backend)
            self._probe_native_released.add(id(backend))

    @staticmethod
    async def _async_disconnect_bluez_backend(backend: BaseBleakClient) -> None:
        """Require acknowledged cancellation even when Connect is still pending."""
        path = getattr(backend, "_device_path", None)
        if not isinstance(path, str):
            await backend.disconnect()
            return
        # The connector's disconnect_devices suppresses D-Bus failures, so it
        # cannot prove pending Connect cancellation. Check the exact-path reply.
        # pylint: disable=import-outside-toplevel
        from bleak.backends.bluezdbus import defs
        from bleak.backends.bluezdbus.manager import get_global_bluez_manager
        from bleak.backends.bluezdbus.utils import assert_reply
        from bleak.exc import BleakDBusError
        from dbus_fast import ErrorType, Message
        # pylint: enable=import-outside-toplevel

        manager = await get_global_bluez_manager()
        bus = manager._bus  # pylint: disable=protected-access
        if bus is None:
            raise ConnectionError("BlueZ recovery bus unavailable")
        reply = await bus.call(
            Message(
                destination=defs.BLUEZ_SERVICE,
                interface=defs.DEVICE_INTERFACE,
                path=path,
                member="Disconnect",
            )
        )
        try:
            assert_reply(reply)
        except BleakDBusError as err:
            if err.dbus_error != ErrorType.UNKNOWN_OBJECT.value:
                raise

    @staticmethod
    async def _async_teardown_bluez_backend(backend: BaseBleakClient) -> None:
        """Release session resources after current transport state confirms closure."""
        monitor = getattr(backend, "_disconnect_monitor_event", None)
        if monitor is not None:
            monitor.set()
            cast(Any, backend)._disconnect_monitor_event = None  # pylint: disable=protected-access
        if not backend.is_connected:
            await backend.disconnect()
            return
        # A removed watcher can leave is_connected stale, making disconnect()
        # reuse a dead bus. Complete the native resource teardown in that case.
        cleanup = cast(Any, backend)._cleanup_all  # pylint: disable=protected-access
        cleanup()
        bus = getattr(backend, "_bus", None)
        if bus is not None:
            bus.disconnect()
            await bus.wait_for_disconnect()
            cast(Any, backend)._bus = None  # pylint: disable=protected-access

    @staticmethod
    async def _async_bluez_connected(backend: BaseBleakClient) -> bool:
        """Treat an unavailable current BlueZ state as an unconfirmed active link."""
        path = getattr(backend, "_device_path", None)
        if not isinstance(path, str):
            return backend.is_connected
        try:
            # pylint: disable-next=import-outside-toplevel
            from bleak.backends.bluezdbus.manager import get_global_bluez_manager

            async with asyncio.timeout(PROBE_CLEANUP_TIMEOUT):
                manager = await get_global_bluez_manager()
                return bool(manager.is_connected(path))
        except Exception:  # pylint: disable=broad-exception-caught
            _LOGGER.debug("Current BlueZ connection state is unavailable")
            return True

    async def disconnect(self) -> None:
        """Release the normal client and any backend abandoned during failed setup."""
        active_backend = getattr(self, "_backend", None)
        await super().disconnect()
        if active_backend is not None and self._is_proxy_backend(active_backend):
            self._probe_proxy_released.add(id(active_backend))
        for backend in self._probe_backends:
            if backend.is_connected or (
                self._is_proxy_backend(backend)
                and id(backend) not in self._probe_proxy_released
            ):
                await backend.disconnect()
                if self._is_proxy_backend(backend):
                    self._probe_proxy_released.add(id(backend))

    async def connect(self, **kwargs: Any) -> None:
        """Leave retries to the probe loop so each one follows confirmed cleanup."""
        if self._probe_connect_started:
            raise ConnectionError("Probe connection attempt already consumed")
        self._probe_connect_started = True
        await super().connect(**kwargs)


class _ProbeDevice(TuyaBLEDevice):
    """Never reconnect or send unsolicited protocol replies during a probe."""

    def __init__(self, ble_device: BLEDevice, advertisement: AdvertisementData) -> None:
        """Create an isolated device with no accepted authentication or status."""
        super().__init__(None, ble_device, advertisement)  # type: ignore[arg-type]
        self.status_accepted = False
        self.pair_rejected = False
        self._awaiting_pair = False
        self._connection_lost = False
        self._owned_client: BleakClientWithServiceCache | None = None

    def _capture_client(self, client: BleakClientWithServiceCache) -> None:
        """Keep a constructor-created client even if connect never returns it."""
        if self._owned_client is not None:
            raise ConnectionError("Probe already owns a connection client")
        self._owned_client = client

    @property
    def cleanup_client(self) -> BleakClientWithServiceCache | None:
        """Return the owned connection, including links with incomplete discovery."""
        return self._owned_client or self._client

    def _handle_command_or_response(
        self, seq_num: int, response_to: int, code: TuyaBLECode, data: bytes
    ) -> None:
        """Track only a rejection correlated to the outstanding PAIR request."""
        if (
            self._awaiting_pair
            and code == TuyaBLECode.FUN_SENDER_PAIR
            and response_to in self._input_expected_responses
        ):
            self.pair_rejected = len(data) == 1 and data[0] not in (0, 2)
        super()._handle_command_or_response(seq_num, response_to, code, data)

    def _decrypt_input(self) -> bytes:
        """Require login-key device info, then session-key auth and status."""
        required_flag = 5 if self._session_key is not None else 4
        if not self._input_buffer or self._input_buffer[0] != required_flag:
            raise TuyaBLEDataFormatError()
        return super()._decrypt_input()

    def _handle_device_status_response(self, data: bytes) -> int:
        """Record whether the authenticated status request was accepted."""
        result = super()._handle_device_status_response(data)
        self.status_accepted = result == 0
        return result

    async def _send_response(
        self, code: TuyaBLECode, data: bytes, response_to: int
    ) -> None:
        """The probe does not emit time/DP acknowledgements or other packets."""

    def _schedule_reconnect(self) -> None:
        """Do not reconnect outside the bounded probe."""

    def _schedule_resend(self, packets: list[bytes]) -> None:
        """Do not retry writes after a disconnect."""

    def _disconnected(self, client: object) -> None:
        """Do not spawn background tasks after an unexpected disconnect."""
        self._connection_lost = True
        self._is_paired = False
        self._client = None

    @property
    def connection_lost(self) -> bool:
        """Detect a dropped connection even before its callback is delivered."""
        return self._connection_lost or (
            self._client is not None and not self._client.is_connected
        )

    async def close_probe(self) -> None:
        """Disconnect even when stopping notifications fails."""
        client = self.cleanup_client
        try:
            await self.stop()
        finally:
            # Cancellation during stop_notify must not start an unbounded
            # disconnect. Native recovery also checks links hidden by stale flags,
            # including on the final attempt when there will be no later probe.
            if client is not None:
                await _async_disconnect_retained_client(client)

    def prepare(self, credentials: TuyaBLEDeviceCredentials, uuid: str) -> None:
        """Load an ephemeral pairing identity and validate the frame before I/O."""
        self.initialize_with_credentials(replace(credentials, uuid=uuid))
        self._build_pairing_request()

    @property
    def paired_for_probe(self) -> bool:
        """Only accept status datapoints after pair authentication succeeds."""
        return self._is_paired

    def _failed_pair_outcome(self) -> ProbeOutcome:
        """Classify only an explicit PAIR refusal as conclusive."""
        if self.pair_rejected:
            return ProbeOutcome.REJECTED
        return ProbeOutcome.INCONCLUSIVE

    async def _try_establish_connection(self) -> BleakClientWithServiceCache | None:
        """Connect without the regular retry logger exposing exception details."""
        async with global_connect_lock:
            client = await establish_connection(
                _ProbeClient,
                self._ble_device,
                self.address,
                self._disconnected,
                use_services_cache=True,
                ble_device_callback=lambda: self._ble_device,
                max_attempts=1,
                capture_client=self._capture_client,
            )
        return client if client and client.is_connected else None

    async def _send_packet_while_connected(
        self, code: TuyaBLECode, data: bytes, response_to: int, wait_for_response: bool
    ) -> bool:
        """Refuse every command except device-info, pair and status."""
        if code not in {
            TuyaBLECode.FUN_SENDER_DEVICE_INFO,
            TuyaBLECode.FUN_SENDER_PAIR,
            TuyaBLECode.FUN_SENDER_DEVICE_STATUS,
        }:
            raise TuyaBLEDeviceError(0)
        return await super()._send_packet_while_connected(
            code, data, response_to, wait_for_response
        )

    async def _int_send_packet_while_connected(self, packets: list[bytes]) -> None:
        """Write without normal resend/error logging of transport exceptions."""
        client = self._client
        if client is None or not client.is_connected:
            raise TuyaBLEDeviceError(0)
        for packet in packets:
            await client.write_gatt_char(CHARACTERISTIC_WRITE, packet, False)

    # Explicit early returns keep every unsafe stage from advancing to PAIR.
    # pylint: disable=too-many-return-statements
    async def authenticated_read(
        self, received: asyncio.Future[None], enable_status: Callable[[], None]
    ) -> ProbeOutcome:
        """Execute only the three read/authentication protocol commands."""
        client = await self._try_establish_connection()
        if client is None:
            return ProbeOutcome.INCONCLUSIVE
        self._client = client
        await client.start_notify(
            CHARACTERISTIC_NOTIFY, self._safe_notification_handler
        )
        if not await self._send_packet_while_connected(
            TuyaBLECode.FUN_SENDER_DEVICE_INFO, b"", 0, True
        ):
            return ProbeOutcome.RETRYABLE
        if self._session_key is None:
            return ProbeOutcome.INCONCLUSIVE
        if not self._is_bound or self._protocol_version not in (2, 3):
            return ProbeOutcome.INCONCLUSIVE
        self._awaiting_pair = True
        try:
            if not await self._send_packet_while_connected(
                TuyaBLECode.FUN_SENDER_PAIR, self._build_pairing_request(), 0, True
            ):
                return ProbeOutcome.RETRYABLE
        except TuyaBLEDeviceError:
            return self._failed_pair_outcome()
        finally:
            self._awaiting_pair = False
        if not self._is_paired:
            return self._failed_pair_outcome()
        enable_status()
        if not await self._send_packet_while_connected(
            TuyaBLECode.FUN_SENDER_DEVICE_STATUS, b"", 0, True
        ):
            return ProbeOutcome.RETRYABLE
        if not self.status_accepted:
            return ProbeOutcome.INCONCLUSIVE
        await received
        return ProbeOutcome.MATCHED

    # pylint: enable=too-many-return-statements


async def _async_close_probe(
    device: _ProbeDevice, outcome: ProbeOutcome
) -> ProbeOutcome:
    """Bound cleanup while retaining an unclosed client as a connection barrier."""
    # stop() clears _client before awaiting the backend's disconnect, so keep
    # the actual client until both cleanup and link closure are confirmed.
    previous_client = device.cleanup_client
    cleanup_task = asyncio.create_task(device.close_probe())
    pending = (cleanup_task, previous_client)
    _pending_probe_cleanup[device.address] = pending
    cleanup_task.add_done_callback(partial(_consume_cleanup_result, device.address))
    try:
        await asyncio.wait_for(asyncio.shield(cleanup_task), PROBE_CLEANUP_TIMEOUT)
    except asyncio.CancelledError:
        cleanup_task.cancel()
        raise
    except TimeoutError:
        cleanup_task.cancel()
        _LOGGER.debug("BLE probe disconnect timed out")
        return ProbeOutcome.RETRYABLE
    except Exception:  # pylint: disable=broad-exception-caught
        cleanup_task.cancel()
        _LOGGER.debug("BLE probe disconnect failed")
        if outcome == ProbeOutcome.RETRYABLE:
            return outcome
        return ProbeOutcome.INCONCLUSIVE
    if await _async_confirm_cleanup(pending):
        if _pending_probe_cleanup.get(device.address) is pending:
            _pending_probe_cleanup.pop(device.address, None)
        return outcome
    return ProbeOutcome.RETRYABLE


async def async_probe_authenticated_status(
    ble_device: BLEDevice,
    advertisement: AdvertisementData,
    credentials: TuyaBLEDeviceCredentials,
    pairing_uuid: str,
    expected_dp_ids: frozenset[int],
) -> ProbeOutcome:
    """Match only after encrypted device-info, accepted pair and expected DPs.

    Timeouts and silent disconnects return RETRYABLE so the caller can retry
    this identity once before advancing. Other failures remain INCONCLUSIVE;
    neither failure outcome proves that the credentials match. Each invocation
    uses a fresh connection and ephemeral credentials.
    """
    info = decode_tuya_ble_advertisement(advertisement)
    if (
        info is None
        or not info.is_bound
        or info.protocol_version not in (2, 3)
        or not expected_dp_ids
    ):
        return ProbeOutcome.INCONCLUSIVE

    device = _ProbeDevice(ble_device, advertisement)
    try:
        # This is a detached copy. Never mutate the SDK's cached cloud record.
        device.prepare(credentials, pairing_uuid)
    except TuyaBLEDeviceError, ValueError:
        return ProbeOutcome.INCONCLUSIVE

    if not await _async_wait_for_previous_cleanup(ble_device.address):
        _LOGGER.debug("Previous BLE probe cleanup has not confirmed disconnection")
        return ProbeOutcome.RETRYABLE

    loop = asyncio.get_running_loop()
    received = loop.create_future()
    status_requested = False

    def on_datapoints(updates: list[TuyaBLEDataPoint]) -> None:
        """Accept expected datapoints only after authenticated status is requested."""
        if (
            status_requested
            and device.paired_for_probe
            and not received.done()
            and any(dp.dp_id in expected_dp_ids for dp in updates)
        ):
            received.set_result(None)

    def enable_status() -> None:
        """Allow subsequent authenticated datapoints to confirm the candidate."""
        nonlocal status_requested
        status_requested = True

    unregister = device.register_callback(on_datapoints)
    outcome = ProbeOutcome.INCONCLUSIVE
    try:
        async with asyncio.timeout(PROBE_TIMEOUT):
            outcome = await device.authenticated_read(received, enable_status)
    except (
        TimeoutError,
        ConnectionError,
        BleakAbortedError,
        BleakConnectionError,
        BleakOutOfConnectionSlotsError,
    ):
        outcome = ProbeOutcome.RETRYABLE
        _LOGGER.debug("Authenticated BLE probe timed out or disconnected")
    except Exception as err:  # pylint: disable=broad-exception-caught
        # BLE and SDK exception text may contain secrets. Never log it.
        if isinstance(err.__cause__, TimeoutError):
            outcome = ProbeOutcome.RETRYABLE
        _LOGGER.debug("Authenticated BLE probe failed")
    finally:
        if outcome == ProbeOutcome.INCONCLUSIVE and device.connection_lost:
            outcome = ProbeOutcome.RETRYABLE
        unregister()
        received.cancel()
        outcome = await _async_close_probe(device, outcome)
    return outcome
