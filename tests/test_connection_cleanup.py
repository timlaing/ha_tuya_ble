"""Discarded-connection teardown keeps the correct task during callback races."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache
import pytest

from custom_components.tuya_ble.tuya_ble import TuyaBLEDevice
from custom_components.tuya_ble.tuya_ble.base import _DISCARDED_CLIENT_CLEANUP_TIMEOUT
from custom_components.tuya_ble.tuya_ble.connection_cleanup import _ConnectionCleanup
from tests.conftest import (
    FakeBleakClient,
    FakeBLEManager,
    make_credentials,
    make_device,
)


async def test_completed_cleanup_callback_does_not_drop_replacement_task() -> None:
    """A backend can queue another close before the previous done callback runs."""
    cleanup = _ConnectionCleanup("AA:BB:CC:DD:EE:FF")
    client = FakeBleakClient()
    close = client.disconnect
    repeats: list[asyncio.Task[None]] = []
    calls = 0

    async def disconnect() -> None:
        """Queue a public cleanup call before the first worker becomes terminal."""
        nonlocal calls
        calls += 1
        if calls == 1:
            # An ineffective close keeps the link pending, so the queued call
            # retries it instead of treating the link as released.
            repeats.append(
                asyncio.create_task(
                    cleanup.async_discard(
                        cast(BleakClientWithServiceCache, client), 0.1
                    )
                )
            )
        else:
            await asyncio.sleep(0)
            await close()

    with patch.object(client, "disconnect", side_effect=disconnect):
        await cleanup.async_discard(cast(BleakClientWithServiceCache, client), 0.1)
        await asyncio.gather(*repeats)

    assert calls == 2
    assert not client.is_connected
    assert not cleanup.pending
    assert not cleanup.tasks


class ProxyBackend:
    """Mirror the ESPHome disconnect and authoritative source-state contract."""

    def __init__(
        self, unavailable: bool | None, available: bool | None, api_alive: bool | None
    ) -> None:
        self._bluetooth_device = SimpleNamespace(
            _unavailable=unavailable, available=available
        )
        self._client = SimpleNamespace(is_connected=api_alive)
        self.is_connected = True
        self.calls = 0
        self.acknowledged = False

    async def disconnect(self) -> None:
        """Clear local state even when the API cannot acknowledge disconnection."""
        self.calls += 1
        self.is_connected = False
        if not self.acknowledged:
            raise OSError("Synthetic unacknowledged proxy disconnect")


class ProxyWrapper(FakeBleakClient):
    """A dropped backend makes the wrapper look closed before remote teardown."""

    def __init__(self, backend: ProxyBackend) -> None:
        super().__init__()
        self._backend: ProxyBackend | None = backend

    @property
    def is_connected(self) -> bool:
        return self._backend is not None and self._backend.is_connected

    async def disconnect(self) -> None:
        if self._backend is not None:
            await self._backend.disconnect()


@pytest.mark.parametrize(
    ("unavailable", "available", "api_alive", "released"),
    [
        (True, False, False, True),
        (True, False, True, False),
        (False, False, False, False),
        (True, True, False, False),
        (None, False, False, False),
        (True, None, False, False),
        (True, False, None, False),
    ],
)
async def test_only_confirmed_proxy_source_loss_releases_pending(
    monkeypatch: pytest.MonkeyPatch,
    unavailable: bool | None,
    available: bool | None,
    api_alive: bool | None,
    released: bool,
) -> None:
    """Local down, unknown state, or a live API session must retain ownership."""
    monkeypatch.setattr(ProxyBackend, "__module__", "bleak_esphome.backend.client")
    backend = ProxyBackend(unavailable, available, api_alive)
    client = ProxyWrapper(backend)
    cleanup = _ConnectionCleanup("AA:BB:CC:DD:EE:FF")
    await cleanup.async_discard(cast(BleakClientWithServiceCache, client), 0.1)
    assert not client.is_connected
    assert backend.calls == 1
    assert bool(cleanup.pending) is not released
    assert not cleanup.tasks


async def test_dropped_proxy_backend_requires_acknowledgment_or_confirmed_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retaining a source prevents a wrapper's dropped reference releasing a link."""
    monkeypatch.setattr(ProxyBackend, "__module__", "bleak_esphome.backend.client")
    backend = ProxyBackend(False, True, True)
    client = ProxyWrapper(backend)
    cleanup = _ConnectionCleanup("AA:BB:CC:DD:EE:FF")
    owned = cast(BleakClientWithServiceCache, client)
    cleanup.capture_source(owned)
    client._backend = None  # pylint: disable=protected-access
    await cleanup.async_discard(owned, 0.1)
    assert not client.is_connected
    assert backend.calls == 1
    assert cleanup.pending
    backend.acknowledged = True
    assert await cleanup.async_cleanup(None, 0.1)
    assert backend.calls == 2
    assert not cleanup.tasks


@pytest.mark.timeout(8)
async def test_proxy_acknowledgment_and_five_second_settling_can_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The total deadline must not cancel the SDK's post-acknowledgment settling."""
    monkeypatch.setattr(ProxyBackend, "__module__", "bleak_esphome.backend.client")

    class SettlingProxyBackend(ProxyBackend):
        """Represent a closed link whose proxy has no free slots to report."""

        async def disconnect(self) -> None:
            """Delay acknowledgement, then finish the full SDK settling wait."""
            await asyncio.sleep(0.05)
            await super().disconnect()
            await asyncio.sleep(5.0)

    backend = SettlingProxyBackend(False, True, True)
    backend.acknowledged = True
    client = ProxyWrapper(backend)
    cleanup = _ConnectionCleanup("AA:BB:CC:DD:EE:FF")
    # The declared API allows up to 20s for acknowledgement, plus 5s settling.
    assert _DISCARDED_CLIENT_CLEANUP_TIMEOUT > 25.0
    await cleanup.async_discard(
        cast(BleakClientWithServiceCache, client), _DISCARDED_CLIENT_CLEANUP_TIMEOUT
    )
    assert backend.calls == 1
    assert not cleanup.pending
    assert not cleanup.tasks


def _touch_device_with_client() -> tuple[TuyaBLEDevice, FakeBleakClient]:
    """Build a ready Touch device to exercise its public stop operation."""
    credentials = make_credentials(category="kg", product_id="bs3ubslo")
    device = make_device(manager=FakeBLEManager(credentials))
    device.initialize_with_credentials(credentials)
    client = FakeBleakClient()
    device._client = cast(BleakClientWithServiceCache, client)  # pylint: disable=protected-access
    return device, client


@pytest.mark.parametrize("failure", ["error", "timeout"])
async def test_touch_stop_handles_bounded_teardown_failures(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, failure: str
) -> None:
    """Existing Touch stop semantics remain bounded when SDK teardown fails."""
    device, client = _touch_device_with_client()
    monkeypatch.setattr(
        "custom_components.tuya_ble.tuya_ble.base._FINGERBOT_TOUCH_DISCONNECT_TIMEOUT",
        0.01,
    )

    async def fail() -> None:
        """Model a backend error or an acknowledgement that never arrives."""
        if failure == "error":
            raise BleakError("Synthetic Touch stop failure")
        await asyncio.Event().wait()

    with (
        patch.object(client, "disconnect", side_effect=fail),
        caplog.at_level(logging.DEBUG),
    ):
        await device.stop()
    assert "BLE client cleanup failed" in caplog.text
    assert not device._is_ready()  # pylint: disable=protected-access


async def test_touch_stop_cancellation_finishes_its_owned_disconnect() -> None:
    """Cancelling public stop does not cancel its dedicated bounded worker."""
    device, client = _touch_device_with_client()
    entered, release = asyncio.Event(), asyncio.Event()
    close = client.disconnect

    async def delayed() -> None:
        """Defer acknowledgement until the cancellation has been delivered."""
        entered.set()
        await release.wait()
        await close()

    with patch.object(client, "disconnect", side_effect=delayed):
        stopping = asyncio.create_task(device.stop())
        await entered.wait()
        stopping.cancel()
        await asyncio.sleep(0)
        assert client.is_connected
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await stopping
    assert not client.is_connected
