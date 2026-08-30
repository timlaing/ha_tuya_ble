"""Unit tests for the product registry, coordinator, and device info helpers."""
# pylint: disable=protected-access
# The tests deliberately set up device/coordinator internals as test setup
# state, which is sanctioned by the project's test conventions.

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, patch

from home_assistant_bluetooth import BluetoothServiceInfoBleak
from homeassistant.const import CONF_ADDRESS, CONF_DEVICE_ID
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers.entity import EntityDescription

from custom_components.tuya_ble.const import (
    DOMAIN as DEVICES_DOMAIN,
)
from custom_components.tuya_ble.const import (
    FINGERBOT_BUTTON_EVENT,
    DPCode,
    DPType,
)
from custom_components.tuya_ble.devices import (
    TuyaBLECoordinator,
    TuyaBLEEntity,
    TuyaBLEFingerbotInfo,
    TuyaBLEProductInfo,
    get_device_info,
    get_device_product_info,
    get_device_readable_name,
    get_product_info_by_ids,
    get_short_address,
)
from custom_components.tuya_ble.tuya_ble import (
    AbstractTuyaBLEDeviceManager,
    TuyaBLEDataPoint,
    TuyaBLEDataPointType,
    TuyaBLEDevice,
    TuyaBLEDeviceCredentials,
)
from tests.conftest import make_credentials, make_device


def test_found() -> None:
    """Look up a known wk category product."""
    info = get_product_info_by_ids("wk", "drlajpqc")
    assert info is not None
    assert info.name == "Thermostatic Radiator Valve"


def test_category_unknown() -> None:
    """Return None for an unknown category."""
    assert get_product_info_by_ids("nonexistent", "x") is None


def test_product_unknown_falls_back_to_category_info() -> None:
    """Return None when both the category and product are unknown."""
    assert get_product_info_by_ids("ms", "unknown") is None


def test_co2() -> None:
    """Look up a known co2bj category product."""
    info = get_product_info_by_ids("co2bj", "59s19z5m")
    assert info is not None
    assert info.name == "CO2 Detector"


def test_fingerbot_product() -> None:
    """Look up a fingerbot product with its datapoint mapping."""
    info = get_product_info_by_ids("szjqr", "ltak7e1p")
    assert info is not None
    assert info.fingerbot is not None
    assert info.fingerbot.switch == 2


def test_diivoo_dual_water_timer_uses_cloud_category() -> None:
    """Register the Diivoo dual timer under its Tuya cloud category."""
    info = get_product_info_by_ids("ggq", "fdrbxxbg")
    assert info is not None
    assert info.name == "Diivoo WT-05 dual water timer"
    assert get_product_info_by_ids("sfkzq", "fdrbxxbg") is None


def test_device_product_info() -> None:
    """Resolve product info from the device credentials."""
    dev = make_device()
    dev._device_info = make_credentials()
    info = get_device_product_info(dev)
    assert info is not None
    assert info.name == "Thermostatic Radiator Valve"


def test_colon_separated() -> None:
    """Take the last six hex digits of a colon-separated MAC."""
    assert get_short_address("AA:BB:CC:DD:EE:FF") == "DDEEFF"


def test_dash_separated() -> None:
    """Take the last six hex digits of a dash-separated MAC."""
    assert get_short_address("aa-bb-cc-dd-ee-ff") == "DDEEFF"


def test_uppercased() -> None:
    """Upper-case a lowercase MAC address."""
    assert get_short_address("aa:bb:cc:11:22:33") == "112233"


async def test_with_product_and_credentials() -> None:
    """Use the product name when product info is available."""

    async def get_creds(address: str) -> TuyaBLEDeviceCredentials | None:
        return make_credentials()

    mgr = SimpleNamespace(get_device_credentials=get_creds)
    discovery = SimpleNamespace(
        address="AA:BB:CC:DD:EE:FF", device=SimpleNamespace(name="BleName")
    )
    name = await get_device_readable_name(
        cast(BluetoothServiceInfoBleak, discovery),
        cast(AbstractTuyaBLEDeviceManager | None, mgr),
    )
    # category 'wk' product 'drlajpqc' -> Thermostatic Radiator Valve
    assert name == "Thermostatic Radiator Valve DDEEFF"


async def test_with_credentials_no_product() -> None:
    """Use the device name when no product matches the credentials."""

    async def get_creds(address: str) -> TuyaBLEDeviceCredentials | None:
        return make_credentials(category="unknown", product_id="unknown")

    mgr = SimpleNamespace(get_device_credentials=get_creds)
    discovery = SimpleNamespace(
        address="AA:BB:CC:DD:EE:FF", device=SimpleNamespace(name="BleName")
    )
    name = await get_device_readable_name(
        cast(BluetoothServiceInfoBleak, discovery),
        cast(AbstractTuyaBLEDeviceManager | None, mgr),
    )
    assert name == "Device DDEEFF"


async def test_no_manager() -> None:
    """Fall back to the broadcast name when no manager is given."""
    discovery = SimpleNamespace(
        address="AA:BB:CC:DD:EE:FF", device=SimpleNamespace(name="BleName")
    )
    name = await get_device_readable_name(
        cast(BluetoothServiceInfoBleak, discovery), None
    )
    assert name == "BleName DDEEFF"


async def test_credentials_none() -> None:
    """Fall back to the broadcast name when credentials are missing."""

    async def get_creds(address: str) -> TuyaBLEDeviceCredentials | None:
        return None

    mgr = SimpleNamespace(get_device_credentials=get_creds)
    discovery = SimpleNamespace(
        address="AA:BB:CC:DD:EE:FF", device=SimpleNamespace(name="BleName")
    )
    name = await get_device_readable_name(
        cast(BluetoothServiceInfoBleak, discovery),
        cast(AbstractTuyaBLEDeviceManager | None, mgr),
    )
    assert name == "BleName DDEEFF"


def test_get_device_info() -> None:
    """Map cloud names and available versions into the device registry."""
    dev = make_device()
    dev._device_info = make_credentials()
    dev._device_version = "1.0"
    dev._protocol_version_str = "2.0"
    dev._hardware_version = "h1"
    info = get_device_info(dev)
    assert info is not None
    assert dev.address in {k for _, k in info["connections"]}
    assert (DEVICES_DOMAIN, dev.address) in info["identifiers"]
    assert info["name"] == "Device"
    assert info["model"] == "Product"
    assert info["hw_version"] == "h1"
    assert info["sw_version"] == "1.0 (protocol 2.0)"


def test_get_device_info_no_product() -> None:
    """Fall back cleanly when cloud and version metadata are unavailable."""
    dev = make_device()
    info = get_device_info(dev)
    assert info is not None
    assert info["manufacturer"] == "tuya"
    assert info["name"] == "TestDevice"
    assert info["model"] is None
    assert info["hw_version"] is None
    assert info["sw_version"] is None


def _make_coord(hass: HomeAssistant) -> tuple[TuyaBLECoordinator, TuyaBLEDevice]:
    """Build a coordinator and device wired together for tests."""
    dev = make_device()
    dev._device_info = make_credentials()
    return TuyaBLECoordinator(hass, dev), dev


def test_initial_state_disconnected(hass: HomeAssistant) -> None:
    """Verify the coordinator starts disconnected."""
    coord, _ = _make_coord(hass)
    assert coord.connected is False


def test_connect_twice_runs_cleaner(hass: HomeAssistant) -> None:
    """Run the previous disconnect unsubscriber when reconnecting."""
    coord, _ = _make_coord(hass)
    cleaner = MagicMock()
    coord._unsub_disconnect = cleaner
    coord._async_handle_connect()
    cleaner.assert_called_once()
    assert coord.connected is True


def test_connect_when_disconnected(hass: HomeAssistant) -> None:
    """Connect when no disconnect timer is currently scheduled."""
    coord, _ = _make_coord(hass)
    coord._unsub_disconnect = None
    coord._async_handle_connect()
    assert coord.connected is True


def test_set_disconnected(hass: HomeAssistant) -> None:
    """Clear the disconnect timer when marked disconnected."""
    coord, _ = _make_coord(hass)
    coord._disconnected = False
    coord._unsub_disconnect = MagicMock()
    coord._set_disconnected(None)
    assert coord.connected is False
    assert coord._unsub_disconnect is None


def test_handle_disconnect_schedules_timer(hass: HomeAssistant) -> None:
    """Schedule the delayed disconnect callback via async_call_later."""
    coord, _ = _make_coord(hass)
    assert coord._unsub_disconnect is None
    with patch("custom_components.tuya_ble.coordinator.async_call_later") as acl:
        coord._async_handle_disconnect()
    assert acl.called
    assert coord._unsub_disconnect is not None


def test_handle_disconnect_already_scheduled(hass: HomeAssistant) -> None:
    """Do not schedule a second timer when one is already pending."""
    coord, _ = _make_coord(hass)
    coord._unsub_disconnect = MagicMock()
    with patch("custom_components.tuya_ble.coordinator.async_call_later") as acl:
        coord._async_handle_disconnect()
    assert not acl.called


async def test_async_shutdown_cancels_pending_timer(hass: HomeAssistant) -> None:
    """async_shutdown unsubscribes a pending disconnect timer."""
    coord, _ = _make_coord(hass)
    cleaner = MagicMock()
    coord._unsub_disconnect = cleaner
    await coord.async_shutdown()
    cleaner.assert_called_once()
    assert coord._unsub_disconnect is None


async def test_async_shutdown_without_timer(hass: HomeAssistant) -> None:
    """async_shutdown completes cleanly when no timer is pending."""
    coord, _ = _make_coord(hass)
    coord._unsub_disconnect = None
    await coord.async_shutdown()


async def test_handle_update_fires_fingerbot_event(hass: HomeAssistant) -> None:
    """Fire the fingerbot button event when the switch datapoint changes."""
    coordinator, device = _make_coord(hass)
    fingerbot = TuyaBLEFingerbotInfo(
        switch=2,
        mode=1,
        up_position=5,
        down_position=6,
        hold_time=3,
        reverse_positions=4,
        manual_control=7,
    )
    product = TuyaBLEProductInfo(name="Fingerbot", fingerbot=fingerbot)
    with patch(
        "custom_components.tuya_ble.coordinator.get_device_product_info",
        return_value=product,
    ):
        device.datapoints.update_from_device(
            2, 1000.0, 0, TuyaBLEDataPointType.DT_BOOL, False
        )
        device.datapoints.update_from_device(
            2, 1001.0, 0, TuyaBLEDataPointType.DT_BOOL, True
        )
        update = cast(TuyaBLEDataPoint, device.datapoints[2])
        captured: list[Event[Any]] = []
        hass.bus.async_listen(FINGERBOT_BUTTON_EVENT, captured.append)
        coordinator._async_handle_update([update])
        await hass.async_block_till_done()
    assert captured
    assert captured[0].data[CONF_ADDRESS] == device.address
    assert captured[0].data[CONF_DEVICE_ID] == device.device_id


async def test_handle_update_no_fingerbot_event(hass: HomeAssistant) -> None:
    """Do not fire an event for devices without fingerbot specs."""
    coordinator, _ = _make_coord(hass)
    captured: list[Event[Any]] = []
    hass.bus.async_listen(FINGERBOT_BUTTON_EVENT, captured.append)
    with patch(
        "custom_components.tuya_ble.coordinator.get_device_product_info",
        return_value=None,
    ):
        coordinator._async_handle_update([])
        await hass.async_block_till_done()
    assert not captured


def _make_desc(translation_key: str | None = None) -> EntityDescription:
    """Create a FakeDescription with the given translation_key."""

    class FakeDescription(EntityDescription):
        """Fake description for testing translation_key logic."""

    return FakeDescription(key="test_key", translation_key=translation_key)


def test_translation_key_set_on_entity() -> None:
    """When translation_key is None, _attr_translation_key is set."""
    dev = make_device()
    dev._device_info = make_credentials()
    coordinator = MagicMock()
    coordinator.connected = True
    coordinator.async_request_refresh = MagicMock(return_value=MagicMock())

    desc = _make_desc(translation_key=None)
    entity = TuyaBLEEntity(MagicMock(), coordinator, dev, MagicMock(), desc)
    assert entity._attr_translation_key == "test_key"


def test_base_entity_does_not_force_sensor_domain() -> None:
    """Leave entity ID assignment to the entity platform."""
    dev = make_device()
    dev._device_info = make_credentials()
    coordinator = MagicMock()
    coordinator.connected = True

    entity = TuyaBLEEntity(MagicMock(), coordinator, dev, MagicMock(), _make_desc())

    assert entity.entity_id is None
    assert entity.unique_id == "device123-test_key"


def test_translation_key_not_overridden() -> None:
    """When translation_key is set, _attr_translation_key is not overwritten."""
    dev = make_device()
    dev._device_info = make_credentials()
    coordinator = MagicMock()
    coordinator.connected = True
    coordinator.async_request_refresh = MagicMock(return_value=MagicMock())

    desc = _make_desc(translation_key="my_key")
    entity = TuyaBLEEntity(MagicMock(), coordinator, dev, MagicMock(), desc)
    # When translation_key is "my_key", _attr_translation_key should NOT be
    # set to "test_key" (it should remain whatever the base class default is)
    assert (
        not hasattr(entity, "_attr_translation_key")
        or getattr(entity, "_attr_translation_key", None) != "test_key"
    )


def test_already_connected_no_update(hass: HomeAssistant) -> None:
    """When _disconnected is False, async_update_listeners is not called."""
    dev = make_device()
    dev._device_info = make_credentials()
    coordinator = TuyaBLECoordinator(hass, dev)
    # First connect sets _disconnected = False
    coordinator._async_handle_connect()
    assert coordinator.connected is True
    # Second connect: _disconnected is already False, so no update
    with patch.object(coordinator, "async_update_listeners") as mock_update:
        coordinator._async_handle_connect()
        mock_update.assert_not_called()
    assert coordinator.connected is True


def test_calls_async_write_ha_state(hass: HomeAssistant) -> None:
    """_handle_coordinator_update calls async_write_ha_state."""
    dev = make_device()
    dev._device_info = make_credentials()
    coordinator = TuyaBLECoordinator(hass, dev)
    coordinator._disconnected = False

    desc = EntityDescription(key="test_key", translation_key=None)
    entity = TuyaBLEEntity(hass, coordinator, dev, MagicMock(), desc)
    entity.hass = hass
    with patch.object(entity, "async_write_ha_state") as mock_write:
        entity._handle_coordinator_update()
        mock_write.assert_called_once()


async def test_no_event_when_not_changed_by_device(hass: HomeAssistant) -> None:
    """Do not fire fingerbot event when changed_by_device is False."""
    dev = make_device()
    dev._device_info = make_credentials()
    coordinator = TuyaBLECoordinator(hass, dev)
    fingerbot = TuyaBLEFingerbotInfo(
        switch=2,
        mode=1,
        up_position=5,
        down_position=6,
        hold_time=3,
        reverse_positions=4,
        manual_control=7,
    )
    product = TuyaBLEProductInfo(name="Fingerbot", fingerbot=fingerbot)
    # Create a datapoint with changed_by_device=False
    device = dev
    device.datapoints.update_from_device(
        2, 1000.0, 0, TuyaBLEDataPointType.DT_BOOL, True
    )
    update = cast(TuyaBLEDataPoint, device.datapoints[2])
    assert update.changed_by_device is False
    captured: list[Event[Any]] = []
    hass.bus.async_listen(FINGERBOT_BUTTON_EVENT, captured.append)
    with patch(
        "custom_components.tuya_ble.coordinator.get_device_product_info",
        return_value=product,
    ):
        coordinator._async_handle_update([update])
        await hass.async_block_till_done()
    assert not captured


def _make_entity_with_device_find_dpid() -> tuple[TuyaBLEEntity, TuyaBLEDevice]:
    """Build an entity with status_range/function dicts for dp id lookups."""
    dev = make_device()
    dev._device_info = make_credentials()
    dev.append_functions(
        [
            {"code": "temp_set", "dp_id": 2, "type": "Enum", "values": ""},
        ],
        [
            {"code": "temp_current", "dp_id": 3, "type": "Integer", "values": ""},
            {"code": "switch_1", "dp_id": 1, "type": "Boolean", "values": None},
        ],
    )
    coordinator = MagicMock()
    coordinator.connected = True
    coordinator.async_request_refresh = MagicMock(return_value=MagicMock())
    desc = EntityDescription(key="test_key", translation_key=None)
    entity = TuyaBLEEntity(MagicMock(), coordinator, dev, MagicMock(), desc)
    return entity, dev


def test_find_dpid_in_status_range() -> None:
    """Find a dp_id that exists in status_range."""
    entity, _ = _make_entity_with_device_find_dpid()
    assert entity.find_dpid(DPCode.TEMP_CURRENT) == 3


def test_find_dpid_in_function() -> None:
    """Find a dp_id that exists only in function."""
    entity, _ = _make_entity_with_device_find_dpid()
    assert entity.find_dpid(DPCode.TEMP_SET) == 2


def test_find_dpid_prefer_function() -> None:
    """With prefer_function=True, prefer function over status_range."""
    entity, _ = _make_entity_with_device_find_dpid()
    assert entity.find_dpid(DPCode.TEMP_SET, prefer_function=True) == 2


def test_find_dpid_not_found() -> None:
    """Return None when dpcode does not exist anywhere."""
    entity, _ = _make_entity_with_device_find_dpid()
    assert entity.find_dpid(DPCode.BRIGHT_VALUE) is None


def test_find_dpid_none_input() -> None:
    """Return None when dpcode is None."""
    entity, _ = _make_entity_with_device_find_dpid()
    assert entity.find_dpid(None) is None


def _make_entity_with_device_find_dpcode() -> tuple[TuyaBLEEntity, TuyaBLEDevice]:
    """Build an entity with status_range/function dicts and status dp."""
    dev = make_device()
    dev._device_info = make_credentials()
    dev.append_functions(
        [
            {
                "code": "temp_set",
                "dp_id": 2,
                "type": "Enum",
                "values": '{"range":["low","high"]}',
            },
        ],
        [
            {
                "code": "temp_current",
                "dp_id": 3,
                "type": "Integer",
                "values": '{"min":0,"max":500,"scale":1,"step":1}',
            },
            {
                "code": "switch_1",
                "dp_id": 1,
                "type": "Boolean",
                "values": None,
            },
        ],
    )
    dev.status = {  # type: ignore[attr-defined]
        "dp_test": SimpleNamespace(dp_id=99, type=DPType.STRING, values="test"),
    }
    coordinator = MagicMock()
    coordinator.connected = True
    coordinator.async_request_refresh = MagicMock(return_value=MagicMock())
    desc = EntityDescription(key="test_key", translation_key=None)
    entity = TuyaBLEEntity(MagicMock(), coordinator, dev, MagicMock(), desc)
    return entity, dev


def test_find_dpcode_single() -> None:
    """Find a dpcode from a single DPCode."""
    entity, _ = _make_entity_with_device_find_dpcode()
    result = entity.find_dpcode(DPCode.TEMP_CURRENT)
    assert result == DPCode.TEMP_CURRENT


def test_find_dpcode_tuple() -> None:
    """Find a dpcode from a tuple of DPCodes."""
    entity, _ = _make_entity_with_device_find_dpcode()
    result = entity.find_dpcode((DPCode.SWITCH_1, DPCode.TEMP_CURRENT))
    assert result in (DPCode.SWITCH_1, DPCode.TEMP_CURRENT)


def test_find_dpcode_string() -> None:
    """Find a dpcode from a string."""
    entity, _ = _make_entity_with_device_find_dpcode()
    result = entity.find_dpcode("switch_1")
    assert result == DPCode.SWITCH_1


def test_find_dpcode_not_found() -> None:
    """Return None when dpcode does not exist."""
    entity, _ = _make_entity_with_device_find_dpcode()
    assert entity.find_dpcode(DPCode.BRIGHT_VALUE) is None


def test_find_dpcode_none() -> None:
    """Return None when dpcodes is None."""
    entity, _ = _make_entity_with_device_find_dpcode()
    assert entity.find_dpcode(None) is None


def test_find_dpcode_with_integer_dptype() -> None:
    """Find an integer-type dpcode."""
    entity, _ = _make_entity_with_device_find_dpcode()
    result = entity.find_dpcode(DPCode.TEMP_CURRENT, dptype=DPType.INTEGER)
    assert result is not None
    assert hasattr(result, "dpcode")
    assert result.dpcode == DPCode.TEMP_CURRENT


def test_find_dpcode_with_enum_dptype() -> None:
    """Find an enum-type dpcode."""
    entity, _ = _make_entity_with_device_find_dpcode()
    result = entity.find_dpcode(DPCode.TEMP_SET, dptype=DPType.ENUM)
    assert result is not None
    assert hasattr(result, "dpcode")
    assert result.dpcode == DPCode.TEMP_SET
    assert hasattr(result, "range")
    assert result.range == ["low", "high"]


def _make_entity_with_device_get_dptype() -> tuple[TuyaBLEEntity, TuyaBLEDevice]:
    """Build an entity with status_range and function dicts."""
    dev = make_device()
    dev._device_info = make_credentials()
    dev.append_functions(
        [
            {"code": "temp_set", "dp_id": 2, "type": "Enum", "values": ""},
        ],
        [
            {"code": "temp_current", "dp_id": 3, "type": "Integer", "values": ""},
        ],
    )
    coordinator = MagicMock()
    coordinator.connected = True
    coordinator.async_request_refresh = MagicMock(return_value=MagicMock())
    desc = EntityDescription(key="test_key", translation_key=None)
    entity = TuyaBLEEntity(MagicMock(), coordinator, dev, MagicMock(), desc)
    return entity, dev


def test_get_dptype_in_status_range() -> None:
    """Return the DPType from status_range."""
    entity, _ = _make_entity_with_device_get_dptype()
    assert entity.get_dptype(DPCode.TEMP_CURRENT) == DPType.INTEGER


def test_get_dptype_in_function() -> None:
    """Return the DPType from function."""
    entity, _ = _make_entity_with_device_get_dptype()
    assert entity.get_dptype(DPCode.TEMP_SET) == DPType.ENUM


def test_get_dptype_not_found() -> None:
    """Return None when dpcode does not exist."""
    entity, _ = _make_entity_with_device_get_dptype()
    assert entity.get_dptype(DPCode.BRIGHT_VALUE) is None


def test_get_dptype_none_input() -> None:
    """Return None when dpcode is None."""
    entity, _ = _make_entity_with_device_get_dptype()
    assert entity.get_dptype(None) is None


def test_get_dptype_prefer_function() -> None:
    """With prefer_function=True, look in function first."""
    entity, _ = _make_entity_with_device_get_dptype()
    assert entity.get_dptype(DPCode.TEMP_SET, prefer_function=True) == DPType.ENUM
