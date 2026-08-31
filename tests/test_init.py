"""Unit tests for the Tuya BLE integration setup/teardown entry points."""

from __future__ import annotations

from contextlib import ExitStack
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from bleak.backends.device import BLEDevice
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tuya_ble import (
    OfflineTuyaBLEDeviceManager,
    _async_update_listener,
    _remove_legacy_sensor_entities,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.tuya_ble.const import (
    CONF_CATEGORY,
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_FUNCTIONS,
    CONF_LOCAL_KEY,
    CONF_PRODUCT_ID,
    CONF_PRODUCT_MODEL,
    CONF_PRODUCT_NAME,
    CONF_STATUS_RANGE,
    CONF_UUID,
    DOMAIN,
)
from custom_components.tuya_ble.entity import _resolve_unique_id


class FakeBLEAddress:
    """Stand-in for a bleak BLEDevice exposing address and name."""

    def __init__(self, address: str = "AA:BB:CC:DD:EE:FF") -> None:
        self.address = address
        self.name = "TestDevice"


def _make_entry_data(address: str = "AA:BB:CC:DD:EE:FF") -> dict[str, Any]:
    """Return a full set of config entry data with all credential fields."""
    return {
        "address": address,
        CONF_UUID: "1234567890abcdef",
        CONF_LOCAL_KEY: "abcdef",
        CONF_DEVICE_ID: "device123",
        CONF_CATEGORY: "wk",
        CONF_PRODUCT_ID: "drlajpqc",
        CONF_DEVICE_NAME: "Device",
        CONF_PRODUCT_MODEL: "Model",
        CONF_PRODUCT_NAME: "Product",
        CONF_FUNCTIONS: [],
        CONF_STATUS_RANGE: [],
    }


def _patch_deps(
    hass: HomeAssistant,
    ble_device: BLEDevice | None = None,
    product_info: Any = None,
) -> dict[str, Any]:
    """Return context managers mocking the heavy dependencies of async_setup_entry."""
    device = MagicMock()
    device.initialize_with_credentials = AsyncMock()
    device.update = AsyncMock()
    device.stop = AsyncMock()
    device.address = "AA:BB:CC:DD:EE:FF"

    coordinator = MagicMock()

    ble = ble_device or FakeBLEAddress()

    return {
        "device": device,
        "coordinator": coordinator,
        "ble_device": ble,
        "patches": [
            patch(
                "custom_components.tuya_ble.bluetooth.async_ble_device_from_address",
                return_value=ble,
            ),
            patch("custom_components.tuya_ble.get_device", return_value=ble),
            patch("custom_components.tuya_ble.TuyaBLEDevice", return_value=device),
            patch(
                "custom_components.tuya_ble.TuyaBLECoordinator",
                return_value=coordinator,
            ),
            patch(
                "custom_components.tuya_ble.get_device_product_info",
                return_value=product_info,
            ),
            patch.object(
                hass.config_entries,
                "async_forward_entry_setups",
                return_value=None,
            ),
            patch(
                "custom_components.tuya_ble.bluetooth.async_register_callback",
                return_value=MagicMock(),
            ),
        ],
    }


async def test_async_setup_entry_success(hass: HomeAssistant) -> None:
    """Assert a successful setup entry wires up device and credentials."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Device",
        data=_make_entry_data(),
        entry_id="entry1",
    )
    entry.add_to_hass(hass)

    deps = _patch_deps(hass, product_info=MagicMock())
    with ExitStack() as stack:
        for p in deps["patches"]:
            stack.enter_context(p)
        result = await async_setup_entry(hass, entry)

    assert result is True
    deps["device"].initialize_with_credentials.assert_awaited_once()
    deps["device"].update.assert_called_once()
    assert hass.data[DOMAIN][entry.entry_id].title == "Device"

    # Verify the manager is an OfflineTuyaBLEDeviceManager
    data = hass.data[DOMAIN][entry.entry_id]
    assert isinstance(data.manager, OfflineTuyaBLEDeviceManager)

    # Trigger the HA stop -> device.stop
    await hass.async_stop()


def test_remove_legacy_sensor_entities(hass: HomeAssistant) -> None:
    """Remove only sensor entries duplicated by a correctly typed entity."""
    entry = MockConfigEntry(domain=DOMAIN, data=_make_entry_data())
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    legacy = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "device123-countdown",
        config_entry=entry,
    )
    replacement = registry.async_get_or_create(
        "number",
        DOMAIN,
        "device123-countdown",
        config_entry=entry,
    )
    valid_sensor = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "device123-battery",
        config_entry=entry,
    )

    _remove_legacy_sensor_entities(hass, entry)

    assert registry.async_get(legacy.entity_id) is None
    assert registry.async_get(replacement.entity_id) is not None
    assert registry.async_get(valid_sensor.entity_id) is not None


def test_unique_id_adopt_legacy(hass: HomeAssistant) -> None:
    """Entity adopts an existing legacy unique_id when one is found in the registry."""
    data = _make_entry_data()
    data[CONF_PRODUCT_ID] = "qycalacn"
    entry = MockConfigEntry(domain=DOMAIN, data=data)
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "number", DOMAIN, "device123-countdown_duration_z1", config_entry=entry
    )

    device = MagicMock()
    device.device_id = "device123"
    device.category = "ggq"
    device.product_id = "qycalacn"

    uid = _resolve_unique_id(hass, device, "countdown_zone1")
    assert uid == "device123-countdown_duration_z1"


def test_unique_id_adopt_ignores_other_integration(hass: HomeAssistant) -> None:
    """A legacy unique_id owned by another integration is not adopted."""
    data = _make_entry_data()
    data[CONF_PRODUCT_ID] = "qycalacn"
    entry = MockConfigEntry(domain=DOMAIN, data=data)
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "number", "other_component", "device123-countdown_duration_z1"
    )

    device = MagicMock()
    device.device_id = "device123"
    device.category = "ggq"
    device.product_id = "qycalacn"

    uid = _resolve_unique_id(hass, device, "countdown_zone1")
    assert uid == "device123-countdown_zone1"


def test_unique_id_fallback_new(hass: HomeAssistant) -> None:
    """Entity uses the new unique_id when no legacy entry exists."""
    device = MagicMock()
    device.device_id = "device123"
    device.category = "ggq"
    device.product_id = "qycalacn"

    uid = _resolve_unique_id(hass, device, "countdown_zone1")
    assert uid == "device123-countdown_zone1"


def test_unique_id_no_legacy_for_unknown_product(hass: HomeAssistant) -> None:
    """Unknown product gets the new unique_id without error."""
    device = MagicMock()
    device.device_id = "device123"
    device.category = "wk"
    device.product_id = "unknown_product"

    uid = _resolve_unique_id(hass, device, "countdown_zone1")
    assert uid == "device123-countdown_zone1"


async def test_setup_removes_legacy_duplicate_before_platforms(
    hass: HomeAssistant,
) -> None:
    """Remove a known legacy entity before forwarding platform setup."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Device",
        data=_make_entry_data(),
        entry_id="entry-legacy",
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    legacy = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "device123-countdown",
        config_entry=entry,
    )
    registry.async_get_or_create(
        "number",
        DOMAIN,
        "device123-countdown",
        config_entry=entry,
    )
    deps = _patch_deps(hass, product_info=MagicMock())

    async def assert_legacy_removed(*args: object) -> None:
        assert registry.async_get(legacy.entity_id) is None

    with ExitStack() as stack:
        for dependency_patch in deps["patches"]:
            stack.enter_context(dependency_patch)
        stack.enter_context(
            patch.object(
                hass.config_entries,
                "async_forward_entry_setups",
                side_effect=assert_legacy_removed,
            )
        )
        assert await async_setup_entry(hass, entry) is True


async def test_async_setup_entry_device_not_found(hass: HomeAssistant) -> None:
    """Assert setup raises ConfigEntryNotReady when no BLE device is found."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Device",
        data=_make_entry_data(),
        entry_id="entry2",
    )
    entry.add_to_hass(hass)

    # Force no BLE device found.
    ble_patch = patch(
        "custom_components.tuya_ble.bluetooth.async_ble_device_from_address",
        return_value=None,
    )
    get_device_patch = patch(
        "custom_components.tuya_ble.get_device",
        return_value=None,
    )
    deps = _patch_deps(hass, ble_device=None, product_info=None)
    with ExitStack() as stack:
        for p in deps["patches"]:
            stack.enter_context(p)
        stack.enter_context(ble_patch)
        stack.enter_context(get_device_patch)
        with pytest.raises(ConfigEntryNotReady):
            await async_setup_entry(hass, entry)


async def test_async_setup_entry_unknown_product(hass: HomeAssistant) -> None:
    """Assert setup raises ConfigEntryNotReady when the product info is unknown."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Device",
        data=_make_entry_data(),
        entry_id="entry2b",
    )
    entry.add_to_hass(hass)

    deps = _patch_deps(hass, product_info=None)
    with ExitStack() as stack:
        for p in deps["patches"]:
            stack.enter_context(p)
        with pytest.raises(ConfigEntryNotReady):
            await async_setup_entry(hass, entry)


async def test_setup_registers_and_calls_ble_callback(hass: HomeAssistant) -> None:
    """Assert the bluetooth callback pushes advertisement data into the device."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Device",
        data=_make_entry_data(),
        entry_id="entry2c",
    )
    entry.add_to_hass(hass)

    deps = _patch_deps(hass, product_info=MagicMock())
    captured: dict[str, Any] = {}

    def register_callback(
        hass: HomeAssistant,
        callback: Any,
        matcher: Any,
        mode: Any,
    ) -> Any:
        captured["callback"] = callback
        return MagicMock()

    service_info = MagicMock()
    service_info.device = MagicMock()
    service_info.advertisement = MagicMock()

    with ExitStack() as stack:
        for p in deps["patches"]:
            stack.enter_context(p)
        stack.enter_context(
            patch(
                "custom_components.tuya_ble.bluetooth.async_register_callback",
                side_effect=register_callback,
            )
        )
        result = await async_setup_entry(hass, entry)

    assert result is True
    assert captured["callback"] is not None
    captured["callback"](service_info, "change")
    deps["device"].set_ble_device_and_advertisement_data.assert_called_once_with(
        service_info.device, service_info.advertisement
    )

    await hass.async_stop()


async def test_async_unload_entry(hass: HomeAssistant) -> None:
    """Assert unloading an entry stops the device and removes it from hass.data."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Device",
        data=_make_entry_data(),
        entry_id="entry3",
    )
    entry.add_to_hass(hass)

    device = MagicMock()
    device.stop = AsyncMock()

    hass.data[DOMAIN] = {entry.entry_id: MagicMock(device=device)}

    with patch.object(
        hass.config_entries,
        "async_unload_platforms",
        return_value=True,
    ):
        result = await async_unload_entry(hass, entry)

    assert result is True
    device.stop.assert_awaited_once()
    assert entry.entry_id not in hass.data[DOMAIN]


async def test_async_unload_entry_unload_fails(hass: HomeAssistant) -> None:
    """Assert a failed platform unload leaves the device registered."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Device",
        data=_make_entry_data(),
        entry_id="entry3b",
    )
    entry.add_to_hass(hass)

    device = MagicMock()
    device.stop = AsyncMock()

    hass.data[DOMAIN] = {entry.entry_id: MagicMock(device=device)}

    with patch.object(
        hass.config_entries,
        "async_unload_platforms",
        return_value=False,
    ):
        result = await async_unload_entry(hass, entry)

    assert result is False
    device.stop.assert_not_awaited()
    assert entry.entry_id in hass.data[DOMAIN]


async def test_offline_manager_returns_credentials() -> None:
    """Assert the offline manager returns the stored credentials."""
    creds = MagicMock()
    mgr = OfflineTuyaBLEDeviceManager(creds)
    result = await mgr.get_device_credentials("AA:BB:CC:DD:EE:FF")
    assert result is creds


async def test_async_update_listener_title_changed(hass: HomeAssistant) -> None:
    """Assert a changed entry title triggers a reload."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="NewTitle",
        data=_make_entry_data(),
        entry_id="entry4",
    )
    entry.add_to_hass(hass)

    hass.data[DOMAIN] = {entry.entry_id: MagicMock(title="OldTitle")}
    with patch.object(
        hass.config_entries, "async_reload", new=AsyncMock()
    ) as reload_mock:
        await _async_update_listener(hass, entry)
    reload_mock.assert_awaited_once_with(entry.entry_id)


async def test_async_update_listener_title_unchanged(hass: HomeAssistant) -> None:
    """Assert an unchanged entry title does not trigger a reload."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Same",
        data=_make_entry_data(),
        entry_id="entry5",
    )
    entry.add_to_hass(hass)

    hass.data[DOMAIN] = {entry.entry_id: MagicMock(title="Same")}
    with patch.object(
        hass.config_entries, "async_reload", new=AsyncMock()
    ) as reload_mock:
        await _async_update_listener(hass, entry)
    reload_mock.assert_not_awaited()
