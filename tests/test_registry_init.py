"""Unit tests for off-event-loop device descriptor registry initialisation."""
# pylint: disable=protected-access

from __future__ import annotations

import asyncio
from pathlib import Path
import threading
import time
from typing import Any
from unittest.mock import patch

from homeassistant.core import HomeAssistant
import pytest

import custom_components.tuya_ble.device_registry as dr


@pytest.fixture(autouse=True)
def _cold_registry_cache() -> Any:
    """Start every test with a cold cache and leave the real one behind."""
    dr.get_registry.cache_clear()
    yield
    dr.get_registry.cache_clear()


async def test_async_setup_registry_loads_off_event_loop(
    hass: HomeAssistant,
) -> None:
    """Descriptor discovery and reads run in an executor, not the loop thread."""
    loop_thread = threading.current_thread()
    threads: list[threading.Thread] = []
    original = dr._parse_yaml

    def _spy(path: Path) -> dict[str, Any]:
        threads.append(threading.current_thread())
        return original(path)

    with patch.object(dr, "_parse_yaml", side_effect=_spy):
        registry = await dr.async_setup_registry(hass)

    assert threads, "the loader never read a descriptor"
    assert all(thread is not loop_thread for thread in threads)
    assert registry is dr.get_registry()


async def test_async_setup_registry_warm_cache_does_no_io(
    hass: HomeAssistant,
) -> None:
    """A second call reuses the cached registry without touching the disk."""
    await dr.async_setup_registry(hass)

    with patch.object(dr, "_parse_yaml", side_effect=AssertionError("filesystem I/O")):
        registry = await dr.async_setup_registry(hass)
        assert dr.get_registry() is registry


async def test_async_setup_registry_concurrent_calls_share_one_load(
    hass: HomeAssistant,
) -> None:
    """Two concurrent setups serialise onto a single registry load."""
    original = dr.DeviceRegistry.load
    calls: list[int] = []

    def _slow_load() -> dr.DeviceRegistry:
        calls.append(1)
        time.sleep(0.05)
        return original()

    with patch.object(dr.DeviceRegistry, "load", side_effect=_slow_load):
        first, second = await asyncio.gather(
            dr.async_setup_registry(hass),
            dr.async_setup_registry(hass),
        )

    assert len(calls) == 1
    assert first is second
    assert first is dr.get_registry()


async def test_async_setup_registry_failure_allows_retry(hass: HomeAssistant) -> None:
    """A failed load raises, caches nothing, and a later attempt succeeds."""
    original = dr.DeviceRegistry.load
    attempts: list[int] = []

    def _flaky_load() -> dr.DeviceRegistry:
        attempts.append(1)
        if len(attempts) == 1:
            raise dr.DeviceRegistryError("bad descriptor")
        return original()

    with patch.object(dr.DeviceRegistry, "load", side_effect=_flaky_load):
        with pytest.raises(dr.DeviceRegistryError):
            await dr.async_setup_registry(hass)
        # The failure is not cached: the retry runs the loader a second time.
        registry = await dr.async_setup_registry(hass)

    assert len(attempts) == 2
    assert registry is dr.get_registry()


async def test_async_setup_registry_reuses_instance_lock(hass: HomeAssistant) -> None:
    """The per-instance initialisation lock is created once and reused."""
    await dr.async_setup_registry(hass)
    lock = hass.data[dr._INIT_LOCK_KEY]

    await dr.async_setup_registry(hass)

    assert hass.data[dr._INIT_LOCK_KEY] is lock
