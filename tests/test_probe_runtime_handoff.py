"""A verified mismatch candidate must reconnect with a clean runtime handshake."""

# pylint: disable=protected-access

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable, Coroutine
from dataclasses import dataclass
import hashlib
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, patch

from bleak.backends.bluezdbus.client import BleakClientBlueZDBus
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from bleak_retry_connector import BleakClientWithServiceCache
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityDescription
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tuya_ble import async_setup_entry
from custom_components.tuya_ble.const import (
    CONF_CLOUD_UUID,
    CONF_FUNCTIONS,
    CONF_UUID,
    DOMAIN,
)
from custom_components.tuya_ble.device_registry import async_setup_registry
from custom_components.tuya_ble.devices import TuyaBLEData
from custom_components.tuya_ble.entity import TuyaBLEEntity
from custom_components.tuya_ble.tuya_ble import (
    MANUFACTURER_DATA_ID,
    TuyaBLEDeviceCredentials,
)
from custom_components.tuya_ble.tuya_ble.const import TuyaBLECode
from tests.conftest import FakeBleakClient, make_credentials
from tests.protocol_harness import (
    decode_frame,
    device_info_data,
    encrypt_payload,
    fragment,
    unpack_varint,
)
from tests.test_bluez_cleanup import NativeBus, make_native_client
from tests.test_config_flow_probe import _flow, _setup
from tests.test_probe import _input


class AuthenticatedFirmwareClient(FakeBleakClient):
    """Require protocol 3 and exchange encrypted info, pair, status, and DP frames."""

    def __init__(
        self,
        failure: str,
        disconnected_callback: Callable[[Any], None],
        session_number: int,
        cleanup_failure: str = "none",
    ) -> None:
        """Configure a single handshake failure while keeping its link open."""
        super().__init__()
        self.failure = failure
        self.disconnected_callback = disconnected_callback
        self.packets: list[bytes] = []
        self.expected_length = 0
        self.version = 0
        self.frames: list[tuple[int, TuyaBLECode]] = []
        self.initial_protocol = 3
        self.reply_sequence = 100
        self.runtime_verified: asyncio.Event | None = None
        self.disconnect_calls = 0
        self.physical_connected = True
        self.cleanup_failure = cleanup_failure
        self.disconnect_started = asyncio.Event()
        self.cleanup_retry_seen = asyncio.Event()
        self.release_disconnect = asyncio.Event()
        self.srand = b"abcde" + bytes((102 + session_number,))
        self.keys = {
            4: hashlib.md5(b"abcdef", usedforsecurity=False).digest(),
            5: hashlib.md5(b"abcdef" + self.srand, usedforsecurity=False).digest(),
        }

    async def start_notify(
        self, gatt_char: str, handler: Callable[[str, bytearray], None]
    ) -> None:
        """Fail notification subscription only on the selected runtime link."""
        if self.failure == "notifications":
            raise OSError("Synthetic notification subscription failure")
        await super().start_notify(gatt_char, handler)

    async def disconnect(self) -> None:
        """Release the physical link and deliver the normal disconnect callback."""
        self.disconnect_calls += 1
        self.disconnect_started.set()
        if self.disconnect_calls > 1:
            self.cleanup_retry_seen.set()
        if (
            self.cleanup_failure == "cleanup_blocked"
            and not self.release_disconnect.is_set()
        ):
            self._is_connected = False
            self.disconnected_callback(self)
            raise OSError("Synthetic unacknowledged disconnect")
        if self.disconnect_calls == 1:
            if self.cleanup_failure in (
                "cleanup_error",
                "cleanup_recovered_during_backoff",
                "cleanup_recovered_while_another_connects",
                "cleanup_stopped_during_backoff",
            ):
                self._is_connected = False
                self.disconnected_callback(self)
                raise OSError("Synthetic unacknowledged disconnect")
            if self.cleanup_failure == "cleanup_ineffective":
                return
            if self.cleanup_failure == "cleanup_cancelled":
                # Deliberately inject worker cancellation, not a supported
                # backend failure: cancellation must propagate, retaining ownership.
                raise asyncio.CancelledError
            if self.cleanup_failure == "cleanup_timeout":
                try:
                    await asyncio.Event().wait()
                finally:
                    self._is_connected = False
                    self.disconnected_callback(self)
            if self.cleanup_failure.startswith("cancel_cleanup"):
                await self.release_disconnect.wait()
        await super().disconnect()
        self.physical_connected = False
        self._notify_started = False
        self.disconnected_callback(self)

    def notify(
        self, code: TuyaBLECode, data: bytes, response_to: int = 0, flag: int = 5
    ) -> None:
        """Deliver encrypted notification fragments through the real parser."""
        self.reply_sequence += 1
        encrypted = encrypt_payload(
            self.keys[flag], flag, self.reply_sequence, response_to, code, data
        )
        assert self.notify_handler is not None
        for packet in fragment(encrypted, 3):
            self.notify_handler("notify", bytearray(packet))
        if code == TuyaBLECode.FUN_RECEIVE_DP and self.runtime_verified is not None:
            self.runtime_verified.set()

    def _complete_frame(self, packet: bytes) -> bool:
        """Collect packet fragments and identify the protocol byte before decoding."""
        number, position = unpack_varint(packet, 0)
        if number == 0:
            self.packets = []
            self.expected_length, position = unpack_varint(packet, position)
            self.version = packet[position] >> 4
        self.packets.append(packet)
        total = 0
        for index, part in enumerate(self.packets):
            _, offset = unpack_varint(part, 0)
            if index == 0:
                _, offset = unpack_varint(part, offset)
                offset += 1
            total += len(part) - offset
        return total == self.expected_length

    async def write_gatt_char(
        self, gatt_char: str, data: bytes, response: bool
    ) -> None:
        """Validate the encrypted wire frame and respond as protocol-3 firmware."""
        await super().write_gatt_char(gatt_char, data, response)
        if not self._complete_frame(bytes(data)):
            return
        sequence, response_to, raw_code, payload, _ = decode_frame(
            self.keys, self.packets
        )
        code = TuyaBLECode(raw_code)
        self.frames.append((self.version, code))
        self.packets = []
        expected_version = (
            self.initial_protocol if code == TuyaBLECode.FUN_SENDER_DEVICE_INFO else 3
        )
        assert self.version == expected_version, (
            "Handshake must retain negotiated protocol"
        )
        if response_to:
            return
        if code == TuyaBLECode.FUN_SENDER_DEVICE_INFO:
            if self.failure == "info_timeout":
                return
            info = bytearray(device_info_data())
            info[6:12] = self.srand
            self.notify(code, bytes(info), sequence, flag=4)
        elif code == TuyaBLECode.FUN_SENDER_PAIR:
            assert payload.startswith(b"1234567890abcdefabcdef")
            self.notify(
                code, b"\x01" if self.failure == "pair_rejected" else b"\x00", sequence
            )
        elif code == TuyaBLECode.FUN_SENDER_DEVICE_STATUS:
            self.notify(code, b"\x00", sequence)
            self.notify(TuyaBLECode.FUN_RECEIVE_DP, b"\x03\x02\x04\x00\x00\x00\x2a")


class ProxyFirmwareBackend:
    """Model an ESPHome source whose API may be permanently unavailable."""

    def __init__(self, owner: ProxyFirmwareClient, fail_disconnect: bool) -> None:
        """Expose the SDK source lifecycle independently of local BLE flags."""
        self.owner = owner
        self.fail_disconnect = fail_disconnect
        self._bluetooth_device = SimpleNamespace(available=True, _unavailable=False)
        self._client = SimpleNamespace(is_connected=True)

    @property
    def is_connected(self) -> bool:
        """Return only the local flag, not authoritative remote teardown state."""
        return self.owner._is_connected

    async def disconnect(self) -> None:
        """A live unacknowledged request and an offline API both clear local state."""
        self.owner.disconnect_calls += 1
        self.owner._is_connected = False
        self.owner.disconnected_callback(self.owner)
        if self.fail_disconnect or not self._client.is_connected:
            raise OSError("Synthetic proxy cannot acknowledge disconnect")
        self.owner.physical_connected = False
        self.owner._notify_started = False


class NativeFirmwareClient(AuthenticatedFirmwareClient):
    """Exchange firmware packets while retaining a real native teardown backend."""

    def __init__(
        self, callback: Callable[[Any], None], native: BleakClientWithServiceCache
    ) -> None:
        super().__init__("info_timeout", callback, 1)
        self.native = native
        self._backend = cast(BleakClientBlueZDBus, native._backend)

    @property
    def is_connected(self) -> bool:
        return self.native.is_connected

    async def disconnect(self) -> None:
        """Use Bleak's actual BlueZ disconnect implementation on the failed link."""
        self.disconnect_calls += 1
        await self.native.disconnect()


class ProxyFirmwareClient(AuthenticatedFirmwareClient):
    """Provide normal firmware frames through a wrapper that can drop its backend."""

    def __init__(
        self,
        failure: str,
        disconnected_callback: Callable[[Any], None],
        session_number: int,
        fail_disconnect: bool,
    ) -> None:
        """Retain the source even after the wrapper clears its backend pointer."""
        super().__init__(failure, disconnected_callback, session_number)
        self.source = ProxyFirmwareBackend(self, fail_disconnect)
        self._backend: ProxyFirmwareBackend | None = self.source

    @property
    def is_connected(self) -> bool:
        """Match HA's false connection flag after discarding its backend."""
        return self._backend is not None and self._backend.is_connected

    async def disconnect(self) -> None:
        """A discarded wrapper reference cannot acknowledge remote disconnection."""
        if self._backend is not None:
            await self._backend.disconnect()

    def lose_source(self) -> None:
        """Simulate explicit SDK source loss and subscriber-link teardown."""
        self.source._bluetooth_device.available = False
        self.source._bluetooth_device._unavailable = True
        self.source._client.is_connected = False
        self.physical_connected = False
        self._backend = None


class SingleLinkFirmware:
    """A battery roller accepts a replacement only after the old link closes."""

    def __init__(self, failure: str) -> None:
        """Fail only the first runtime handshake, never its successful setup probe."""
        self.failure = failure
        self.exact_match = failure == "exact_match"
        self.clients: list[AuthenticatedFirmwareClient] = []
        self.runtime_connected = asyncio.Event()
        self.runtime_verified = asyncio.Event()
        self.replacement_waiting = asyncio.Event()
        self.release_replacement = asyncio.Event()

    async def establish(
        self,
        _client_class: type[BleakClientWithServiceCache],
        _device: BLEDevice,
        _name: str,
        disconnected_callback: Callable[[Any], None],
        **_kwargs: Any,
    ) -> BleakClientWithServiceCache:
        """Assert that cleanup has released every previous physical connection."""
        assert all(not client.physical_connected for client in self.clients)
        runtime_start = 0 if self.exact_match else 1
        failure = self.failure if len(self.clients) == runtime_start else "none"
        cleanup_failure = "none"
        if failure.startswith(("cleanup_", "cancel_cleanup")):
            cleanup_failure, failure = failure, "info_timeout"
        client: AuthenticatedFirmwareClient
        if (
            self.failure.endswith("proxy_source_lost")
            and len(self.clients) >= runtime_start
        ):
            first_runtime = len(self.clients) == runtime_start
            client = ProxyFirmwareClient(
                "info_timeout" if first_runtime else "none",
                disconnected_callback,
                len(self.clients),
                first_runtime,
            )
        else:
            client = AuthenticatedFirmwareClient(
                failure, disconnected_callback, len(self.clients), cleanup_failure
            )
        self.clients.append(client)
        if self.failure == "protocol_negotiation":
            client.initial_protocol = 2
        if len(self.clients) > runtime_start:
            client.runtime_verified = self.runtime_verified
        if len(self.clients) == runtime_start + 1:
            self.runtime_connected.set()
        if (
            self.failure == "cleanup_recovered_while_another_connects"
            and len(self.clients) == runtime_start + 2
        ):
            self.replacement_waiting.set()
            await self.release_replacement.wait()
        return cast(BleakClientWithServiceCache, client)


def _capture_initial_update(
    initial: list[Coroutine[Any, Any, None]],
) -> Callable[..., None]:
    """Record setup's background coroutine so the test controls runtime startup."""

    def create(
        _entry: ConfigEntry,
        _hass: HomeAssistant,
        coroutine: Coroutine[Any, Any, None],
        **_kwargs: Any,
    ) -> None:
        """Preserve the real initial update while deferring its scheduling."""
        initial.append(coroutine)

    return create


@dataclass
class VerifiedSetup:
    """Store only the entry and discovery data from a successful public flow."""

    entry: MockConfigEntry
    ble: BLEDevice
    advertisement: AdvertisementData
    credentials: TuyaBLEDeviceCredentials


@dataclass
class RuntimeHandoff:
    """Own the public runtime setup and its test-controlled initial update."""

    firmware: SingleLinkFirmware
    setup: VerifiedSetup
    runtime: TuyaBLEData
    entity: TuyaBLEEntity
    initial_update: Coroutine[Any, Any, None]
    initial_task: asyncio.Task[None] | None = None


async def _verified_setup(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, firmware: SingleLinkFirmware
) -> VerifiedSetup:
    """Authenticate a real mismatch probe before constructing runtime state."""
    await async_setup_registry(hass)
    ble, advertisement = _input()
    if firmware.failure == "protocol_negotiation":
        manufacturer = advertisement.manufacturer_data[MANUFACTURER_DATA_ID]
        advertisement.manufacturer_data[MANUFACTURER_DATA_ID] = (
            manufacturer[:1] + b"\x02" + manufacturer[2:]
        )
    credentials: TuyaBLEDeviceCredentials = make_credentials(
        uuid="1234567890abcdef" if firmware.exact_match else "different-cloud-uuid",
        category="kg" if firmware.failure == "touch_proxy_source_lost" else "cl",
        product_id="bs3ubslo"
        if firmware.failure == "touch_proxy_source_lost"
        else "jetlwvfm",
    )
    if firmware.exact_match:
        credentials.functions = [
            {
                "dp_id": 3,
                "code": "percent_state",
                "type": "Integer",
                "values": '{"min":0,"max":100,"step":1,"scale":0}',
            }
        ]
    monkeypatch.setattr(
        "custom_components.tuya_ble.tuya_ble.probe.establish_connection",
        firmware.establish,
    )
    monkeypatch.setattr(
        "custom_components.tuya_ble.tuya_ble.base.establish_connection",
        firmware.establish,
    )
    monkeypatch.setattr(
        "custom_components.tuya_ble.tuya_ble.protocol_send.RESPONSE_WAIT_TIMEOUT", 0.01
    )
    monkeypatch.setattr(
        "custom_components.tuya_ble.tuya_ble.base._DISCARDED_CLIENT_CLEANUP_TIMEOUT",
        0.05,
    )
    monkeypatch.setattr(
        "custom_components.tuya_ble.tuya_ble.base.BLEAK_BACKOFF_TIME", 0.01
    )
    flow, discovery = _flow(hass, [credentials])
    discovery.device = ble  # type: ignore[attr-defined]
    discovery.advertisement = advertisement  # type: ignore[assignment]
    if firmware.exact_match:
        with patch.object(
            flow._manager,
            "get_device_credentials_by_uuid",
            new_callable=AsyncMock,
            return_value=credentials,
        ):
            result, error = await _setup(flow, discovery, frozenset({3}))
    else:
        result, error = await _setup(flow, discovery, frozenset({3}))
    assert error is None
    assert result is not None
    assert result["data"][CONF_UUID] == "1234567890abcdef"
    assert result["data"][CONF_CLOUD_UUID] == credentials.uuid
    if firmware.exact_match:
        assert not firmware.clients
    else:
        assert firmware.clients[0].frames == [
            (
                2 if firmware.failure == "protocol_negotiation" else 3,
                TuyaBLECode.FUN_SENDER_DEVICE_INFO,
            ),
            (3, TuyaBLECode.FUN_SENDER_PAIR),
            (3, TuyaBLECode.FUN_SENDER_DEVICE_STATUS),
        ]
        assert not firmware.clients[0].is_connected

    entry = MockConfigEntry(domain=DOMAIN, title="Shelf Shade", data=result["data"])
    entry.add_to_hass(hass)
    return VerifiedSetup(entry, ble, advertisement, credentials)


@pytest.fixture(name="handoff")
async def fixture_runtime_handoff(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, failure: str
) -> AsyncGenerator[RuntimeHandoff]:
    """Keep the real runtime setup alive while the scenario exercises connection."""
    firmware = SingleLinkFirmware(failure)
    if failure.endswith("proxy_source_lost"):
        monkeypatch.setattr(
            ProxyFirmwareBackend, "__module__", "bleak_esphome.backend.client"
        )
    setup = await _verified_setup(hass, monkeypatch, firmware)
    initial: list[Coroutine[Any, Any, None]] = []
    with (
        patch(
            "custom_components.tuya_ble.bluetooth.async_ble_device_from_address",
            return_value=setup.ble,
        ),
        patch(
            "custom_components.tuya_ble.bluetooth.async_last_service_info",
            return_value=Mock(device=setup.ble, advertisement=setup.advertisement),
        ),
        patch(
            "custom_components.tuya_ble.bluetooth.async_register_callback",
            return_value=Mock(),
        ),
        patch.object(
            ConfigEntry,
            "async_create_background_task",
            _capture_initial_update(initial),
        ),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new_callable=AsyncMock
        ),
    ):
        assert await async_setup_entry(hass, setup.entry)
        runtime = cast(TuyaBLEData, hass.data[DOMAIN][setup.entry.entry_id])
        assert runtime.device.datapoints[3] is None
        assert runtime.device._session_key is None
        assert not runtime.coordinator.connected
        entity = TuyaBLEEntity(
            hass,
            runtime.coordinator,
            runtime.device,
            runtime.product,
            EntityDescription(key="ble_cover"),
        )
        entity.hass = hass
        handoff = RuntimeHandoff(firmware, setup, runtime, entity, initial[0])
        with patch.object(entity, "async_write_ha_state"):
            await entity.async_added_to_hass()
            assert not entity.available
            try:
                yield handoff
            finally:
                if handoff.initial_task is not None and not handoff.initial_task.done():
                    handoff.initial_task.cancel()
                    await asyncio.gather(handoff.initial_task, return_exceptions=True)
                initial[0].close()
                await entity.async_will_remove_from_hass()
                await runtime.device.stop()
                await runtime.coordinator.async_shutdown()


async def _cancel_initial_cleanup(handoff: RuntimeHandoff, twice: bool) -> None:
    """Cancel runtime startup without allowing another connection to overlap cleanup."""
    starting = handoff.initial_task = asyncio.create_task(handoff.initial_update)
    await handoff.firmware.runtime_connected.wait()
    failed = handoff.firmware.clients[1]
    await failed.disconnect_started.wait()
    starting.cancel()
    await asyncio.sleep(0)
    assert failed.physical_connected
    assert not handoff.entity.available
    if twice:
        starting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await starting
        retry = asyncio.create_task(handoff.runtime.device.update())
        await asyncio.sleep(0)
        assert len(handoff.firmware.clients) == 2
        assert handoff.runtime.device._client_cleanup_tasks
        failed.release_disconnect.set()
        await retry
    else:
        failed.release_disconnect.set()
        with pytest.raises(asyncio.CancelledError):
            await starting
        assert not failed.physical_connected
        await handoff.runtime.device.update()


async def _recover_while_another_update_connects(handoff: RuntimeHandoff) -> None:
    """A deferred worker must not reset the sequence of an in-progress handshake."""
    device = handoff.runtime.device
    recovery = device._reconnect_task
    assert recovery is not None
    observed = asyncio.Event()
    ensure_connected = device._ensure_connected

    async def observe_connection() -> None:
        """Observe the actual worker joining the manual update's connection lock."""
        if asyncio.current_task() is recovery and device._connect_lock.locked():
            observed.set()
        await ensure_connected()

    with patch.object(device, "_ensure_connected", side_effect=observe_connection):
        manual = asyncio.create_task(device.update())
        await handoff.firmware.replacement_waiting.wait()
        sequence = device._current_seq_num
        await observed.wait()
        assert device._current_seq_num == sequence
        handoff.firmware.release_replacement.set()
        await manual
        await recovery


async def _release_lost_proxy_source(handoff: RuntimeHandoff) -> None:
    """A permanently lost source must not block another healthy proxy."""
    assert not handoff.entity.available
    assert len(handoff.firmware.clients) == 2
    failed_proxy = cast(ProxyFirmwareClient, handoff.firmware.clients[1])
    assert not failed_proxy.is_connected
    assert failed_proxy.physical_connected
    assert handoff.runtime.device._pending_client_cleanup
    failed_proxy.lose_source()
    await handoff.firmware.runtime_verified.wait()
    assert not failed_proxy.source._client.is_connected


async def _drive_initial_update(handoff: RuntimeHandoff, failure: str) -> int:
    """Exercise cancellation and failed-closure recovery through public updates."""
    if failure.startswith("cancel_cleanup"):
        await _cancel_initial_cleanup(handoff, failure == "cancel_cleanup_twice")
    elif failure == "cleanup_cancelled":
        # This injected lifecycle cancellation intentionally aborts startup.
        # An explicit new update may resume it; it is not automatic-recovery evidence.
        with pytest.raises(asyncio.CancelledError):
            await handoff.initial_update
        assert not handoff.entity.available
        assert handoff.runtime.device._pending_client_cleanup
        await handoff.runtime.device.update()
    else:
        await handoff.initial_update
    if failure.endswith("proxy_source_lost"):
        await _release_lost_proxy_source(handoff)
        return 0
    if failure == "cleanup_recovered_while_another_connects":
        await _recover_while_another_update_connects(handoff)
        return 0
    if failure == "cleanup_recovered_during_backoff":
        recovery = handoff.runtime.device._reconnect_task
        assert recovery is not None
        await asyncio.sleep(0)
        await handoff.runtime.device.update()
        await handoff.runtime.coordinator.hass.async_block_till_done()
        sequence = handoff.runtime.device._current_seq_num
        await recovery
        assert handoff.runtime.device._current_seq_num == sequence
        return 0
    if failure != "cleanup_blocked":
        return 0
    assert not handoff.entity.available
    assert len(handoff.firmware.clients) == 2
    failed = handoff.firmware.clients[1]
    assert not failed.is_connected
    assert failed.physical_connected
    assert handoff.runtime.device._pending_client_cleanup
    await failed.cleanup_retry_seen.wait()
    assert not handoff.entity.available
    assert len(handoff.firmware.clients) == 2
    blocked_attempts = failed.disconnect_calls
    failed.release_disconnect.set()
    await handoff.firmware.runtime_verified.wait()
    return blocked_attempts


@pytest.mark.parametrize(
    "failure",
    [
        "none",
        "notifications",
        "info_timeout",
        "pair_rejected",
        "cleanup_error",
        "cleanup_ineffective",
        "cleanup_timeout",
        "cleanup_blocked",
        "cleanup_cancelled",
        "proxy_source_lost",
        "touch_proxy_source_lost",
        "cleanup_recovered_during_backoff",
        "cleanup_recovered_while_another_connects",
        "cancel_cleanup",
        "cancel_cleanup_twice",
    ],
)
async def test_authenticated_probe_followed_by_runtime_available(
    hass: HomeAssistant, handoff: RuntimeHandoff, failure: str
) -> None:
    """Verified credentials reconnect correctly without retaining a failed link."""
    blocked_attempts = await _drive_initial_update(handoff, failure)
    await handoff.firmware.runtime_verified.wait()
    await hass.async_block_till_done()
    assert handoff.entity.available
    assert handoff.runtime.device.protocol_version == "3.0"
    assert handoff.runtime.device._session_key == handoff.firmware.clients[-1].keys[5]
    assert handoff.runtime.device._session_key != handoff.firmware.clients[0].keys[5]
    assert handoff.runtime.device.datapoints[3].value == 42
    assert handoff.runtime.device.device_id == handoff.setup.credentials.device_id
    assert not handoff.runtime.device._pending_client_cleanup
    assert not handoff.runtime.device._client_cleanup_tasks
    assert not handoff.runtime.device._connection_cleanup._proxy_sources
    assert len(handoff.firmware.clients) == (2 if failure == "none" else 3)
    assert all(
        not client.physical_connected for client in handoff.firmware.clients[:-1]
    )
    assert all(
        version == 3
        for client in handoff.firmware.clients[1:]
        for version, _ in client.frames
    )
    if failure != "none":
        expected_disconnects = (
            2
            if failure.startswith("cleanup_") or failure.endswith("proxy_source_lost")
            else 1
        )
        if failure == "cleanup_blocked":
            expected_disconnects = blocked_attempts + 1
        assert handoff.firmware.clients[1].disconnect_calls == expected_disconnects


@pytest.mark.parametrize("failure", ["cleanup_stopped_during_backoff"])
async def test_stop_cancels_deferred_cleanup_recovery(handoff: RuntimeHandoff) -> None:
    """Stopping during backoff cannot start another connection or abandon cleanup."""
    await handoff.initial_update
    recovery = handoff.runtime.device._reconnect_task
    assert recovery is not None
    await asyncio.sleep(0)
    assert not handoff.entity.available
    await handoff.runtime.device.stop()
    assert recovery.cancelled()
    assert len(handoff.firmware.clients) == 2
    assert not handoff.firmware.runtime_verified.is_set()
    assert not handoff.runtime.device._pending_client_cleanup
    assert not handoff.runtime.device._client_cleanup_tasks
    assert all(not client.physical_connected for client in handoff.firmware.clients)


@pytest.mark.parametrize("failure", ["exact_match"])
async def test_matching_uuid_cloud_metadata_does_not_prove_live_connection(
    hass: HomeAssistant, handoff: RuntimeHandoff
) -> None:
    """Matching identities and DP definitions can precede any successful BLE read."""
    assert (
        handoff.setup.entry.data[CONF_UUID] == handoff.setup.entry.data[CONF_CLOUD_UUID]
    )
    assert handoff.setup.entry.data[CONF_FUNCTIONS][0]["dp_id"] == 3
    assert not handoff.firmware.clients
    assert handoff.runtime.device.protocol_version == ""
    assert not handoff.runtime.device.datapoints.values()
    assert not handoff.entity.available
    await handoff.initial_update
    await handoff.firmware.runtime_verified.wait()
    await hass.async_block_till_done()
    assert handoff.entity.available
    assert handoff.runtime.device.protocol_version == "3.0"
    assert handoff.runtime.device.datapoints[3].value == 42
    assert len(handoff.firmware.clients) == 1
    assert all(version == 3 for version, _ in handoff.firmware.clients[0].frames)


@pytest.mark.parametrize("failure", ["protocol_negotiation"])
async def test_concurrent_update_preserves_negotiated_protocol_during_pairing(
    hass: HomeAssistant,
    handoff: RuntimeHandoff,
) -> None:
    """A waiter must not downgrade pairing headers from device-info v3 to advert v2."""
    device = handoff.runtime.device
    pairing, release = asyncio.Event(), asyncio.Event()
    send_pairing = device._try_send_pairing
    first = True

    async def pause_pairing() -> bool:
        """Pause the first runtime handshake after authenticated device info."""
        nonlocal first
        if first:
            first = False
            assert device.protocol_version == "3.0"
            pairing.set()
            await release.wait()
        return bool(await send_pairing())

    with patch.object(device, "_try_send_pairing", side_effect=pause_pairing):
        handoff.initial_task = asyncio.create_task(handoff.initial_update)
        await pairing.wait()
        concurrent = asyncio.create_task(device.update())
        await asyncio.sleep(0)
        assert device._connect_lock.locked()
        release.set()
        await asyncio.gather(handoff.initial_task, concurrent)
        await hass.async_block_till_done()
    assert handoff.entity.available
    assert len(handoff.firmware.clients) == 2
    assert handoff.firmware.clients[1].frames[:2] == [
        (2, TuyaBLECode.FUN_SENDER_DEVICE_INFO),
        (3, TuyaBLECode.FUN_SENDER_PAIR),
    ]
    assert device.protocol_version == "3.0"


@pytest.mark.parametrize("failure", ["none"])
async def test_native_dead_bus_recovers_automatically_and_allows_subsequent_update(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, handoff: RuntimeHandoff
) -> None:
    """A dead private bus cannot permanently block the runtime's replacement link."""
    native, backend, dead, _manager = await make_native_client()
    original_establish = handoff.firmware.establish
    failed: NativeFirmwareClient | None = None

    async def establish(
        client_class: type[BleakClientWithServiceCache],
        ble: BLEDevice,
        name: str,
        disconnected_callback: Callable[[Any], None],
        **kwargs: Any,
    ) -> BleakClientWithServiceCache:
        """Use real native cleanup on the first runtime link, then normal firmware."""
        nonlocal failed
        client = await original_establish(
            client_class, ble, name, disconnected_callback, **kwargs
        )
        if failed is None:
            previous = handoff.firmware.clients[-1]
            failed = NativeFirmwareClient(disconnected_callback, native)
            failed.runtime_verified = previous.runtime_verified
            handoff.firmware.clients[-1] = failed
            return cast(BleakClientWithServiceCache, failed)
        return client

    monkeypatch.setattr(
        "custom_components.tuya_ble.tuya_ble.base.establish_connection", establish
    )
    recovery = NativeBus("connect")
    call = recovery.call

    async def acknowledge(message: Any) -> Any:
        """Release simulated physical ownership only after native teardown reply."""
        reply = await call(message)
        assert failed is not None
        failed.physical_connected = False
        return reply

    with (
        patch("dbus_fast.aio.MessageBus", return_value=recovery),
        patch.object(recovery, "call", side_effect=acknowledge),
    ):
        await handoff.initial_update
        assert failed is not None
        assert failed.physical_connected
        assert not handoff.entity.available
        assert handoff.runtime.device._pending_client_cleanup
        assert dead.connected is False
        assert (
            backend.is_connected
        )  # The manager's watcher missed the link-down signal.
        recovery.error = None
        recovery.connected = True
        # No manual update is needed to recover after the D-Bus service returns.
        await handoff.firmware.runtime_verified.wait()
        await hass.async_block_till_done()
        assert handoff.entity.available
        assert len(handoff.firmware.clients) == 3
        assert not handoff.runtime.device._pending_client_cleanup
        assert not handoff.runtime.device._connection_cleanup._native_sources
        assert not backend.is_connected
        assert backend._bus is None
        await handoff.runtime.device.update()
        assert handoff.entity.available
        assert len(handoff.firmware.clients) == 3
