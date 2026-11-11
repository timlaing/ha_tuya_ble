"""Bounded, isolated authenticated BLE status read for cloud identity matching."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from enum import StrEnum
import logging

from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from .advertisement import decode_tuya_ble_advertisement
from .base import TuyaBLEDevice, global_connect_lock
from .const import CHARACTERISTIC_NOTIFY, CHARACTERISTIC_WRITE, TuyaBLECode
from .datapoints import TuyaBLEDataPoint
from .exceptions import TuyaBLEDataFormatError, TuyaBLEDeviceError
from .manager import TuyaBLEDeviceCredentials

_LOGGER = logging.getLogger(__name__)
PROBE_TIMEOUT = 12
PROBE_CLEANUP_TIMEOUT = 5


def _consume_cleanup_result(task: asyncio.Task[None]) -> None:
    """Observe a late cleanup failure without logging transport exceptions."""
    if not task.cancelled():
        task.exception()


class ProbeOutcome(StrEnum):
    """Result of an authenticated status read (not a cloud-device guess)."""

    MATCHED = "matched"
    REJECTED = "rejected"
    INCONCLUSIVE = "inconclusive"


class _ProbeDevice(TuyaBLEDevice):
    """Never reconnect or send unsolicited protocol replies during a probe."""

    def __init__(self, ble_device: BLEDevice, advertisement: AdvertisementData) -> None:
        """Create an isolated device with no accepted authentication or status."""
        super().__init__(None, ble_device, advertisement)  # type: ignore[arg-type]
        self.status_accepted = False
        self.pair_rejected = False
        self._awaiting_pair = False

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
        self._is_paired = False
        self._client = None

    async def close_probe(self) -> None:
        """Disconnect even when stopping notifications fails."""
        client = self._client
        try:
            await self.stop()
        finally:
            if client is not None and client.is_connected:
                await client.disconnect()

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
                BleakClientWithServiceCache,
                self._ble_device,
                self.address,
                self._disconnected,
                use_services_cache=True,
                ble_device_callback=lambda: self._ble_device,
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
        if (
            not await self._send_packet_while_connected(
                TuyaBLECode.FUN_SENDER_DEVICE_INFO, b"", 0, True
            )
            or self._session_key is None
        ):
            return ProbeOutcome.INCONCLUSIVE
        if not self._is_bound or self._protocol_version not in (2, 3):
            return ProbeOutcome.INCONCLUSIVE
        self._awaiting_pair = True
        try:
            if not await self._send_packet_while_connected(
                TuyaBLECode.FUN_SENDER_PAIR, self._build_pairing_request(), 0, True
            ):
                return ProbeOutcome.INCONCLUSIVE
        except TuyaBLEDeviceError:
            return self._failed_pair_outcome()
        finally:
            self._awaiting_pair = False
        if not self._is_paired:
            return self._failed_pair_outcome()
        enable_status()
        if (
            not await self._send_packet_while_connected(
                TuyaBLECode.FUN_SENDER_DEVICE_STATUS, b"", 0, True
            )
            or not self.status_accepted
        ):
            return ProbeOutcome.INCONCLUSIVE
        await received
        return ProbeOutcome.MATCHED

    # pylint: enable=too-many-return-statements


async def async_probe_authenticated_status(
    ble_device: BLEDevice,
    advertisement: AdvertisementData,
    credentials: TuyaBLEDeviceCredentials,
    pairing_uuid: str,
    expected_dp_ids: frozenset[int],
) -> ProbeOutcome:
    """Match only after encrypted device-info, accepted pair and expected DPs.

    A timeout or BLE error cannot distinguish a wrong key from radio failure;
    return INCONCLUSIVE in that case rather than trying a different identity.
    Each invocation uses a fresh connection and ephemeral credentials.
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
    except TimeoutError:
        pass
    except Exception:  # pylint: disable=broad-exception-caught
        # BLE and SDK exception text may contain secrets. Never log it.
        _LOGGER.debug("Authenticated BLE probe failed")
    finally:
        unregister()
        received.cancel()
        cleanup_task = asyncio.create_task(device.close_probe())
        cleanup_task.add_done_callback(_consume_cleanup_result)
        try:
            # Shield prevents a cancellation-resistant disconnect from holding
            # the config-flow's shared probe lock past the cleanup deadline.
            await asyncio.wait_for(asyncio.shield(cleanup_task), PROBE_CLEANUP_TIMEOUT)
        except asyncio.CancelledError:
            cleanup_task.cancel()
            raise
        except Exception:  # pylint: disable=broad-exception-caught
            cleanup_task.cancel()
            _LOGGER.debug("BLE probe disconnect failed")
            outcome = ProbeOutcome.INCONCLUSIVE
    return outcome
