"""Recovery of a device that is absent at startup and starts advertising later.

Regression scenarios carried over from ha-tuya-ble/ha_tuya_ble PRs 351, 293,
297, 298 and 277. They drive the real ``async_setup_entry``, coordinator and
``TuyaBLEDevice`` against a simulated protocol-3 firmware; only the Bluetooth
stack and Home Assistant's platform forwarding are mocked.
"""

# pylint: disable=protected-access

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
import logging
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, patch

from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, BleakNotFoundError
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.tuya_ble import async_setup_entry, async_unload_entry
from custom_components.tuya_ble.const import (
    CONF_CATEGORY,
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_LOCAL_KEY,
    CONF_PRODUCT_ID,
    CONF_UUID,
    DOMAIN,
)
from custom_components.tuya_ble.devices import TuyaBLEData
from custom_components.tuya_ble.tuya_ble import TuyaBLEDevice
from custom_components.tuya_ble.tuya_ble import base as base_module
from custom_components.tuya_ble.tuya_ble.const import TuyaBLECode
from custom_components.tuya_ble.tuya_ble.manager import AbstractTuyaBLEDeviceManager
from tests.conftest import make_credentials, make_device
from tests.protocol_harness import v4_advertisement
from tests.test_probe import _input
from tests.test_probe_runtime_handoff import AuthenticatedFirmwareClient

ADDRESS = "AA:BB:CC:DD:EE:FF"
BASE = "custom_components.tuya_ble.tuya_ble.base"
TIMEOUT = 3.0
# Real, if shortened, backoff timing plus a cold descriptor registry load when
# this module runs first need more than the suite's default two seconds.
pytestmark = pytest.mark.timeout(8)


async def _until(condition: Callable[[], bool]) -> None:
    """Wait, bounded, for state driven by background tasks."""
    async with asyncio.timeout(TIMEOUT):
        while not condition():
            await asyncio.sleep(0.005)


class DroppingFirmwareClient(AuthenticatedFirmwareClient):
    """Firmware link that can fail a write while staying connected, or drop."""

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self.fail_next_write = False

    async def write_gatt_char(
        self, gatt_char: str, data: bytes, response: bool
    ) -> None:
        """Fail one write without touching the link, like a GATT error."""
        if self.fail_next_write:
            self.fail_next_write = False
            raise OSError("Synthetic GATT write failure")
        await super().write_gatt_char(gatt_char, data, response)

    def drop(self) -> None:
        """Lose the link from the device side and report it like bleak does."""
        self._is_connected = False
        self.physical_connected = False
        self.disconnected_callback(self)


@dataclass
class AbsentFirmware:
    """A single-link device that is only reachable while it advertises."""

    advertising: bool = False
    failures: list[str] = field(default_factory=list)
    cleanup_failures: list[str] = field(default_factory=list)
    clients: list[DroppingFirmwareClient] = field(default_factory=list)
    devices: list[BLEDevice] = field(default_factory=list)
    overlaps: int = 0
    gate: asyncio.Event | None = None
    in_flight: int = 0
    max_in_flight: int = 0

    async def establish(
        self,
        _client_class: type[BleakClientWithServiceCache],
        device: BLEDevice,
        _name: str,
        disconnected_callback: Callable[[Any], None],
        **_kwargs: Any,
    ) -> BleakClientWithServiceCache:
        """Connect only when advertising and the previous link is released."""
        self.devices.append(device)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self.gate is not None:
                await self.gate.wait()
        finally:
            self.in_flight -= 1
        if not self.advertising:
            raise BleakNotFoundError(f"{ADDRESS} not found")
        if any(client.physical_connected for client in self.clients):
            self.overlaps += 1
        failure = self.failures.pop(0) if self.failures else "none"
        cleanup = self.cleanup_failures.pop(0) if self.cleanup_failures else "none"
        client = DroppingFirmwareClient(
            failure, disconnected_callback, len(self.clients), cleanup
        )
        self.clients.append(client)
        return cast(BleakClientWithServiceCache, client)


@dataclass
class Runtime:
    """The public setup of one entry and the callbacks Home Assistant holds."""

    hass: HomeAssistant
    entry: MockConfigEntry
    firmware: AbsentFirmware
    data: TuyaBLEData
    on_advertisement: Callable[[Any, Any], None]
    advertised: BLEDevice
    advertisement: AdvertisementData

    def advertise(self) -> None:
        """Make the device reachable and deliver one advertisement."""
        self.firmware.advertising = True
        self.on_advertisement(
            Mock(device=self.advertised, advertisement=self.advertisement), "change"
        )

    async def unload(self) -> bool:
        """Unload the entry through the integration's public entry point."""
        with patch.object(
            self.hass.config_entries,
            "async_unload_platforms",
            AsyncMock(return_value=True),
        ):
            return await async_unload_entry(self.hass, self.entry)


@pytest.fixture(autouse=True)
def fast_timing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shrink protocol timeouts and reconnect backoff to test scale."""
    monkeypatch.setattr(
        "custom_components.tuya_ble.tuya_ble.protocol_send.RESPONSE_WAIT_TIMEOUT", 0.01
    )
    monkeypatch.setattr(f"{BASE}._DISCARDED_CLIENT_CLEANUP_TIMEOUT", 0.05)
    monkeypatch.setattr(f"{BASE}.BLEAK_BACKOFF_TIME", 0.01)
    monkeypatch.setattr(f"{BASE}._RECONNECT_INITIAL_DELAY", 0.02)
    monkeypatch.setattr(f"{BASE}._RECONNECT_MAX_DELAY", 0.05)
    # Contended asyncio locks bind to one loop; keep this module's contention local.
    monkeypatch.setattr(f"{BASE}.global_connect_lock", asyncio.Lock())


@pytest.fixture(name="firmware")
def fixture_firmware(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> AbsentFirmware:
    """Route every runtime connection to the simulated firmware.

    Parametrised with ``"gated"``, every attempt is held from the very first.
    """
    firmware = AbsentFirmware()
    if getattr(request, "param", None) == "gated":
        firmware.gate = asyncio.Event()
    monkeypatch.setattr(f"{BASE}.establish_connection", firmware.establish)
    return firmware


@pytest.fixture(name="runtime")
async def fixture_runtime(
    hass: HomeAssistant, firmware: AbsentFirmware
) -> AsyncGenerator[Runtime]:
    """Set up an entry whose device is not in Home Assistant's Bluetooth cache."""
    credentials = make_credentials(category="cl", product_id="jetlwvfm")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Shelf Shade",
        data={
            "address": ADDRESS,
            CONF_UUID: credentials.uuid,
            CONF_LOCAL_KEY: credentials.local_key,
            CONF_DEVICE_ID: credentials.device_id,
            CONF_CATEGORY: credentials.category,
            CONF_PRODUCT_ID: credentials.product_id,
            CONF_DEVICE_NAME: credentials.device_name,
        },
    )
    entry.add_to_hass(hass)
    _, advertisement = _input()
    advertised = BLEDevice(ADDRESS, "Shelf Shade", {})
    callbacks: list[Callable[[Any, Any], None]] = []

    def last_service_info(*_args: Any, **_kwargs: Any) -> Mock | None:
        """Home Assistant only stores an advertisement once one was heard."""
        if not firmware.advertising:
            return None
        return Mock(device=advertised, advertisement=advertisement)

    def register(_hass: HomeAssistant, callback: Any, *_args: Any) -> Mock:
        callbacks.append(callback)
        return Mock()

    with (
        patch(
            "custom_components.tuya_ble.bluetooth.async_ble_device_from_address",
            return_value=None,
        ),
        patch(
            "custom_components.tuya_ble.bluetooth.async_last_service_info",
            side_effect=last_service_info,
        ),
        patch(
            "custom_components.tuya_ble.bluetooth.async_register_callback",
            side_effect=register,
        ),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new_callable=AsyncMock
        ),
    ):
        assert await async_setup_entry(hass, entry)
        data = cast(TuyaBLEData, hass.data[DOMAIN][entry.entry_id])
        runtime = Runtime(
            hass, entry, firmware, data, callbacks[0], advertised, advertisement
        )
        try:
            yield runtime
        finally:
            if firmware.gate is not None:
                firmware.gate.set()
            await data.device.stop()
            await data.coordinator.async_shutdown()
            await hass.async_block_till_done()


def _connected(runtime: Runtime) -> bool:
    """Return whether the session is up and its first status has arrived."""
    datapoint = runtime.data.device.datapoints[3]
    return (
        runtime.data.coordinator.connected
        and datapoint is not None
        and datapoint.value == 42
    )


async def test_startup_out_of_range_connects_once_the_device_advertises(
    runtime: Runtime, caplog: pytest.LogCaptureFixture
) -> None:
    """An absent device at startup is connected as soon as it is heard from.

    The initial update must not end in an unwatched task (PR 297), and no
    restart or entry reload may be needed once it advertises (PR 351).
    """
    device = runtime.data.device
    firmware = runtime.firmware

    await _until(lambda: len(firmware.devices) >= 6)
    assert not runtime.data.coordinator.connected
    assert not firmware.clients
    assert device._reconnect_task is not None
    assert not device._reconnect_task.done()
    assert firmware.devices[0].address == ADDRESS

    runtime.advertise()
    await _until(lambda: _connected(runtime))

    assert firmware.devices[-1] is runtime.advertised
    assert len(firmware.clients) == 1
    assert firmware.overlaps == 0
    assert device._reconnect_delay == 0.02
    assert not device._connection_failure_reported
    records = caplog.get_records("setup") + caplog.records
    warnings = [
        r
        for r in records
        if r.levelno >= logging.WARNING and r.name.startswith("custom_components")
    ]
    assert [r.getMessage() for r in warnings] == [
        f"{ADDRESS}: Unable to connect after 3 connection cycles; RSSI: None. "
        "Check device power, Bluetooth range and proxy availability; "
        "enable debug logging for retry details"
    ]
    assert not any("never retrieved" in r.getMessage() for r in records)


async def test_repeated_advertisements_do_not_start_duplicate_connections(
    runtime: Runtime,
) -> None:
    """A burst of advertisements during a connection attempt adds no attempts."""
    firmware = runtime.firmware
    firmware.gate = asyncio.Event()
    runtime.advertise()
    await _until(lambda: firmware.in_flight == 1)
    attempts = len(firmware.devices)

    for _ in range(25):
        runtime.advertise()
    await asyncio.sleep(0.05)

    assert len(firmware.devices) == attempts
    firmware.gate.set()
    await _until(lambda: _connected(runtime))

    for _ in range(25):
        runtime.advertise()
    await asyncio.sleep(0.05)
    assert firmware.max_in_flight == 1
    assert len(firmware.devices) == attempts
    assert len(firmware.clients) == 1


async def test_repeated_fd50_identity_advertisements_start_one_connection(
    firmware: AbsentFirmware,
) -> None:
    """Each identity advertisement must not spawn another connect task."""
    credentials = make_credentials()
    device = make_device()
    device.initialize_with_credentials(credentials)
    device._awaiting_fd50_identity = True
    firmware.gate = asyncio.Event()
    advertisement = v4_advertisement(uuid=credentials.uuid.encode())
    try:
        device.set_ble_device_and_advertisement_data(device._ble_device, advertisement)
        task = device._identity_connect_task
        assert task is not None
        await _until(lambda: firmware.in_flight == 1)
        for _ in range(25):
            device.set_ble_device_and_advertisement_data(
                device._ble_device, advertisement
            )
        await asyncio.sleep(0.02)
        assert device._identity_connect_task is task
        assert firmware.max_in_flight == 1
        assert len(firmware.devices) == 1
    finally:
        await device.stop()
    assert task.cancelled()


@pytest.mark.parametrize("failure", ["info_timeout", "pair_rejected", "notifications"])
async def test_failed_handshakes_recover_without_overlapping_links(
    runtime: Runtime, failure: str
) -> None:
    """Every failed handshake is released before the next one, then recovers."""
    firmware = runtime.firmware
    firmware.failures = [failure] * 4
    runtime.advertise()

    await _until(lambda: _connected(runtime))

    assert len(firmware.clients) == 5
    assert firmware.overlaps == 0
    assert all(not client.physical_connected for client in firmware.clients[:-1])
    assert firmware.clients[-1].physical_connected
    assert runtime.data.device._reconnect_delay == 0.02

    # Notifications still queued on a failed link must not reach the parser.
    stale = firmware.clients[0]
    if stale.notify_handler is not None:
        stale.notify(TuyaBLECode.FUN_RECEIVE_DP, b"\x03\x02\x04\x00\x00\x00\x07")
    datapoint = runtime.data.device.datapoints[3]
    assert datapoint is not None
    assert datapoint.value == 42


@pytest.mark.parametrize(
    ("firmware", "owner"),
    [("gated", "initial update"), (None, "reconnect")],
    indirect=["firmware"],
)
async def test_unload_during_an_in_flight_connect_does_not_block(
    runtime: Runtime, owner: str
) -> None:
    """Unloading must not wait behind a connection attempt (PR 298).

    The attempt may come from the entry's initial update or a reconnect. Once
    unloaded, a link that completes late is closed and never adopted.
    """
    firmware = runtime.firmware
    device = runtime.data.device
    if owner == "reconnect":
        await _until(lambda: device._reconnect_task is not None)
        firmware.gate = asyncio.Event()
    gate = firmware.gate
    assert gate is not None
    await _until(lambda: firmware.in_flight == 1)
    assert (device._reconnect_task is None) == (owner == "initial update")
    attempts = len(firmware.devices)

    async with asyncio.timeout(TIMEOUT):
        assert await runtime.unload()

    assert runtime.entry.entry_id not in runtime.hass.data[DOMAIN]
    firmware.advertising = True
    gate.set()
    await asyncio.sleep(0.05)
    await runtime.hass.async_block_till_done()
    assert all(not client.physical_connected for client in firmware.clients)
    assert not runtime.data.coordinator.connected
    assert device._client is None
    assert len(firmware.devices) == attempts


async def test_dropped_link_reconnects_and_ignores_the_old_subscription(
    runtime: Runtime,
) -> None:
    """A link lost on the device side is replaced by exactly one new session."""
    firmware = runtime.firmware
    runtime.advertise()
    await _until(lambda: _connected(runtime))
    first = firmware.clients[0]

    first.drop()
    await _until(lambda: len(firmware.clients) == 2 and _connected(runtime))

    assert firmware.overlaps == 0
    assert runtime.data.device._client is firmware.clients[1]
    first.notify(TuyaBLECode.FUN_RECEIVE_DP, b"\x03\x02\x04\x00\x00\x00\x07")
    datapoint = runtime.data.device.datapoints[3]
    assert datapoint is not None
    assert datapoint.value == 42


async def test_abandoned_live_link_is_released_before_reconnecting(
    runtime: Runtime,
) -> None:
    """A write failure on a live link must close it, not just forget it (PR 277).

    Otherwise the old connection keeps its notification subscription and, on a
    single-link device or proxy, the slot the replacement needs.
    """
    firmware = runtime.firmware
    runtime.advertise()
    await _until(lambda: _connected(runtime))
    first = firmware.clients[0]
    first.fail_next_write = True

    with pytest.raises(BleakError):
        await runtime.data.device.update()

    await _until(lambda: len(firmware.clients) == 2 and _connected(runtime))
    assert first.disconnect_calls == 1
    assert not first.physical_connected
    assert firmware.overlaps == 0
    assert runtime.data.device._client is firmware.clients[1]


def _paired_device() -> TuyaBLEDevice:
    """Build a device with stored credentials and a protocol-3 advertisement."""
    ble, advertisement = _input()
    device = TuyaBLEDevice(cast(AbstractTuyaBLEDeviceManager, None), ble, advertisement)
    device.initialize_with_credentials(make_credentials())
    return device


@pytest.mark.parametrize("firmware", ["gated"], indirect=True)
@pytest.mark.parametrize("cleanup", ["cleanup_error", "cleanup_blocked"])
async def test_replacement_waits_for_a_stopped_devices_late_link(
    firmware: AbsentFirmware, cleanup: str
) -> None:
    """A reload must not connect over a link the stopped device still holds.

    The stopped device's in-flight attempt closes its own late client. When
    that teardown fails, the device replacing it retries it first, and does not
    connect for as long as the link stays up.
    """
    firmware.advertising = True
    firmware.cleanup_failures = [cleanup]
    stopped = _paired_device()
    attempt = asyncio.create_task(stopped.update())
    await _until(lambda: firmware.in_flight == 1)
    gate = firmware.gate
    assert gate is not None

    async with asyncio.timeout(TIMEOUT):
        await stopped.stop()
    firmware.gate = None
    gate.set()
    await attempt  # a stopped device's request ends quietly
    late = firmware.clients[0]
    assert late.disconnect_calls == 1
    assert late.physical_connected
    assert stopped._pending_client_cleanup == {cast(BleakClientWithServiceCache, late)}

    replacement = _paired_device()
    try:
        if cleanup == "cleanup_blocked":
            with pytest.raises(BleakNotFoundError):
                await replacement.update()
            assert len(firmware.clients) == 1
            assert late.physical_connected
            late.release_disconnect.set()
            await _until(replacement._is_ready)
        else:
            await replacement.update()
        assert not late.physical_connected
        assert not stopped._pending_client_cleanup
        assert firmware.overlaps == 0
        assert replacement._client is firmware.clients[1]
        await replacement.update()
        datapoint = replacement.datapoints[3]
        assert datapoint is not None
        assert datapoint.value == 42
    finally:
        await replacement.stop()


@pytest.mark.parametrize("firmware", ["gated"], indirect=True)
async def test_replacement_queued_behind_a_stopped_attempt_does_not_overlap(
    firmware: AbsentFirmware,
) -> None:
    """A replacement already waiting to connect rechecks for the late link.

    It passes the cleanup gate while the stopped device is still inside
    ``establish_connection``, so it must check again once it holds the global
    connect lock, and keep backing off while that late link cannot be closed.
    """
    firmware.advertising = True
    firmware.cleanup_failures = ["cleanup_blocked"]
    stopped = _paired_device()
    attempt = asyncio.create_task(stopped.update())
    await _until(lambda: firmware.in_flight == 1)
    gate = firmware.gate
    assert gate is not None
    await stopped.stop()

    replacement = _paired_device()
    try:
        queued = asyncio.create_task(replacement.update())
        await asyncio.sleep(0.02)
        assert firmware.in_flight == 1
        firmware.gate = None
        gate.set()
        await attempt
        with pytest.raises(BleakNotFoundError):
            await queued
        late = firmware.clients[0]
        assert len(firmware.clients) == 1
        assert late.physical_connected
        assert stopped._pending_client_cleanup == {
            cast(BleakClientWithServiceCache, late)
        }

        late.release_disconnect.set()
        await _until(replacement._is_ready)
        assert not late.physical_connected
        assert firmware.overlaps == 0
        assert replacement._client is firmware.clients[1]
    finally:
        await replacement.stop()


async def test_stopped_device_never_starts_a_queued_connection(
    firmware: AbsentFirmware,
) -> None:
    """An attempt still waiting for the global connect lock gives up on stop."""
    firmware.advertising = True
    stopped = _paired_device()
    async with base_module.global_connect_lock:
        attempt = asyncio.create_task(stopped.update())
        await asyncio.sleep(0.02)
        await stopped.stop()
    await attempt
    assert not firmware.devices
    assert not firmware.clients


@pytest.mark.parametrize("cleanup", ["none", "cleanup_blocked"])
async def test_replacement_waits_for_a_stopped_handshake(
    firmware: AbsentFirmware, cleanup: str
) -> None:
    """Stopping during the handshake closes that link before a replacement.

    The stopped attempt already owns its client but has not discarded it yet,
    so the stop itself must put it behind the cleanup gate.
    """
    firmware.advertising = True
    firmware.cleanup_failures = [cleanup]
    stopped = _paired_device()
    configuring, release = asyncio.Event(), asyncio.Event()
    configure = stopped._configure_client

    async def pause() -> bool:
        configuring.set()
        await release.wait()
        return await configure()

    replacement = _paired_device()
    try:
        with patch.object(stopped, "_configure_client", side_effect=pause):
            attempt = asyncio.create_task(stopped.update())
            await configuring.wait()
            late = firmware.clients[0]
            async with asyncio.timeout(TIMEOUT):
                await stopped.stop()
            queued = asyncio.create_task(replacement.update())
            await asyncio.sleep(0.02)
            release.set()
            await attempt
        if cleanup == "cleanup_blocked":
            with pytest.raises(BleakNotFoundError):
                await queued
            assert len(firmware.clients) == 1
            assert late.physical_connected
            late.release_disconnect.set()
            await _until(replacement._is_ready)
        else:
            await queued
        assert not late.physical_connected
        assert firmware.overlaps == 0
        assert replacement._client is cast(
            BleakClientWithServiceCache, firmware.clients[1]
        )
        assert stopped._client is None
    finally:
        await replacement.stop()


async def test_released_late_link_is_not_closed_again(
    firmware: AbsentFirmware,
) -> None:
    """A resumed stopped handshake must not close its released link twice.

    Proxy backends disconnect by address, so a second close of the stopped
    device's link would drop the replacement that now holds that address.
    """
    firmware.advertising = True
    stopped = _paired_device()
    configuring, release = asyncio.Event(), asyncio.Event()
    configure = stopped._configure_client

    async def pause() -> bool:
        configuring.set()
        await release.wait()
        return await configure()

    replacement = _paired_device()
    try:
        with patch.object(stopped, "_configure_client", side_effect=pause):
            attempt = asyncio.create_task(stopped.update())
            await configuring.wait()
            late = firmware.clients[0]
            await stopped.stop()
            await _until(lambda: not late.physical_connected)
            await replacement.update()
            current = firmware.clients[1]

            async def by_address() -> None:
                """Model a proxy backend that disconnects whatever holds the address."""
                late.disconnect_calls += 1
                current.drop()

            late.disconnect = by_address  # type: ignore[method-assign]
            release.set()
            await attempt
        await asyncio.sleep(0.02)
        assert late.disconnect_calls == 1
        assert current.physical_connected
        assert replacement._is_ready()
    finally:
        await replacement.stop()
