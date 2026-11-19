"""Track and bound teardown of connections discarded before a valid handshake."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial
import logging
from typing import TYPE_CHECKING, Any, cast
from weakref import WeakSet

from bleak.backends.client import BaseBleakClient
from bleak_retry_connector import BleakClientWithServiceCache

from .bluez_cleanup import _BlueZSource
from .exceptions import BLE_CONNECTION_EXCEPTIONS

if TYPE_CHECKING:
    from bleak.backends.bluezdbus.client import BleakClientBlueZDBus

_LOGGER = logging.getLogger(__name__)
_probe_cleanup_state: dict[str, Callable[[str], Awaitable[bool]]] = {}
# Cleanup of stopped devices that still holds links, keyed by upper-case address.
# A stop no longer waits for an in-flight connect, so its late client can be
# retained after the replacement device for the same address is created.
_orphaned_cleanups: dict[str, set[_ConnectionCleanup]] = {}


def set_probe_cleanup_waiter(waiter: Callable[[str], Awaitable[bool]]) -> None:
    """Register the probe ownership barrier without a device/probe import cycle."""
    _probe_cleanup_state["waiter"] = waiter


async def async_wait_for_probe_cleanup(address: str) -> bool:
    """Gate runtime connections on retained configuration-time ownership."""
    if not _probe_cleanup_state:
        return True
    return await _probe_cleanup_state["waiter"](address)


@dataclass(frozen=True, repr=False)
class _ProxySource:
    """Retain the actual proxy backend and its authoritative source lifecycle."""

    backend: BaseBleakClient
    device: Any
    api_client: Any

    def lost(self) -> bool:
        """Require explicit SDK unavailability and loss of the owning API session."""
        # available=False alone is also the initial unknown state. The latch is
        # set only by async_set_unavailable(), which HA invokes on source loss.
        return (
            getattr(self.device, "_unavailable", None) is True
            and getattr(self.device, "available", None) is False
            and getattr(self.api_client, "is_connected", None) is False
        )


class _ConnectionCleanup:
    """Retain failed links until teardown or source loss is confirmed."""

    def __init__(self, address: str) -> None:
        """Maintain cleanup state separately from the current live connection."""
        self.address = address
        self.discarded: WeakSet[BleakClientWithServiceCache] = WeakSet()
        self.pending: set[BleakClientWithServiceCache] = set()
        self.tasks: dict[BleakClientWithServiceCache, asyncio.Task[None]] = {}
        self._proxy_sources: dict[BleakClientWithServiceCache, _ProxySource] = {}
        self._native_sources: dict[BleakClientWithServiceCache, _BlueZSource] = {}
        self._orphaned = False

    def orphan(self) -> None:
        """Let later devices for this address finish this stopped owner's cleanup."""
        self._orphaned = True
        self._publish()

    def _publish(self) -> None:
        """Expose retained links of a stopped owner to its successors."""
        if self._orphaned and self.pending:
            _orphaned_cleanups.setdefault(self.address.upper(), set()).add(self)

    def _peers(self) -> tuple[_ConnectionCleanup, ...]:
        """Return stopped owners for this address other than this one."""
        peers = _orphaned_cleanups.get(self.address.upper(), set())
        for peer in tuple(peers):
            if not peer.pending:
                peers.discard(peer)
        if not peers:
            _orphaned_cleanups.pop(self.address.upper(), None)
        return tuple(peer for peer in peers if peer is not self)

    def blocked(self) -> bool:
        """Report, under the global connect lock, links a stopped owner still holds.

        The cleanup gate runs before that lock is taken, so a stopped device's
        attempt can retain its late client while a replacement waits for it.
        """
        if peers := self._peers():
            _LOGGER.debug(
                "%s: Waiting for %s stopped connection(s) to close",
                self.address,
                sum(len(peer.pending) for peer in peers),
            )
        return bool(peers)

    def capture_source(self, client: BleakClientWithServiceCache) -> None:
        """Capture a returned source before failure can discard its session state."""
        if client in self._proxy_sources or client in self._native_sources:
            return
        backend = getattr(client, "_backend", None)
        if any(
            base.__module__ == "bleak.backends.bluezdbus.client"
            for base in type(backend).__mro__
        ):
            self._native_sources.setdefault(
                client, _BlueZSource(cast("BleakClientBlueZDBus", backend))
            )
            return
        if not any(
            base.__module__ == "bleak_esphome.backend.client"
            for base in type(backend).__mro__
        ):
            return
        self._proxy_sources[client] = _ProxySource(
            cast(BaseBleakClient, backend),
            getattr(backend, "_bluetooth_device", None),
            getattr(backend, "_client", None),
        )

    def release_source(self, client: BleakClientWithServiceCache) -> None:
        """Drop source snapshots when a handshake succeeds or teardown completes."""
        self._proxy_sources.pop(client, None)
        self._native_sources.pop(client, None)

    def _disconnect_target(
        self, client: BleakClientWithServiceCache
    ) -> BleakClientWithServiceCache | BaseBleakClient:
        """A dropped wrapper backend must not turn cleanup into an unconfirmed no-op."""
        source = self._proxy_sources.get(client) or self._native_sources.get(client)
        if (
            source is not None
            and getattr(client, "_backend", None) is not source.backend
        ):
            return source.backend
        return client

    def _release_pending(self, client: BleakClientWithServiceCache) -> None:
        """Release both the ownership barrier and its retained source references."""
        self.pending.discard(client)
        self.release_source(client)

    async def _disconnect_discarded(
        self, client: BleakClientWithServiceCache, cleanup_duration: float
    ) -> None:
        """Retain the barrier on failed, timed-out, or ineffective teardown."""
        target = self._disconnect_target(client)
        try:
            async with asyncio.timeout(cleanup_duration):
                native = self._native_sources.get(client)
                if native is not None:
                    if await native.async_disconnect():
                        self._release_pending(client)
                    return
                await target.disconnect()
        except (TimeoutError, *BLE_CONNECTION_EXCEPTIONS):
            source = self._proxy_sources.get(client)
            if source is not None and source.lost():
                self._release_pending(client)
                _LOGGER.debug("%s: Released unavailable proxy ownership", self.address)
            else:
                _LOGGER.debug("%s: Closing the unused connection failed", self.address)
        else:
            if not target.is_connected:
                self._release_pending(client)

    def schedule_discard(
        self, client: BleakClientWithServiceCache, cleanup_duration: float
    ) -> asyncio.Task[None]:
        """Hold the ownership barrier now and start closing an unused link."""
        self.discarded.add(client)
        self.pending.add(client)
        self.capture_source(client)
        self._publish()

        task = self.tasks.get(client)
        if task is None or task.done():
            task = asyncio.create_task(
                self._disconnect_discarded(client, cleanup_duration)
            )
            self.tasks[client] = task
            task.add_done_callback(partial(self._finish_cleanup, client))
        return task

    async def async_discard(
        self, client: BleakClientWithServiceCache, cleanup_duration: float
    ) -> None:
        """Close an unused link without allowing cancellation to lose ownership."""
        task = self.schedule_discard(client, cleanup_duration)
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            # Keep cancellation distinct from recoverable teardown failures.
            # ESPHome backend 4.0.0 / API 46.6.0 fail disconnect waiters with
            # API errors or timeouts, not independently cancelled futures.
            await asyncio.shield(task)
            raise

    def _finish_cleanup(
        self, client: BleakClientWithServiceCache, task: asyncio.Task[None]
    ) -> None:
        """Retain tasks across repeated cancellation and consume their results."""
        if self.tasks.get(client) is task:
            self.tasks.pop(client)
        if not task.cancelled():
            task.exception()

    async def async_cleanup(
        self,
        current_client: BleakClientWithServiceCache | None,
        cleanup_duration: float,
    ) -> bool:
        """Resolve unused connection ownership before a replacement can connect.

        Links retained by a stopped device for the same address hold the gate too.
        """
        peers = self._peers()
        for peer in peers:
            await peer.async_cleanup_own(None, cleanup_duration)
        await self.async_cleanup_own(current_client, cleanup_duration)
        return not self.pending and not any(peer.pending for peer in peers)

    async def async_cleanup_own(
        self,
        current_client: BleakClientWithServiceCache | None,
        cleanup_duration: float,
    ) -> None:
        """Retry teardown of every unused link this owner still retains."""
        for client in tuple(self.pending):
            if client is current_client:
                continue
            # Proxy-local state can be false before disconnect is acknowledged.
            await self.async_discard(client, cleanup_duration)
