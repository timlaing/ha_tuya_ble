"""Tuya BLE integration for Home Assistant — local Bluetooth control of Tuya devices."""

from __future__ import annotations

from bleak_retry_connector import get_device
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth.match import BluetoothCallbackMatcher
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_ADDRESS,
    EVENT_HOMEASSISTANT_STOP,
    Platform,
)
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er

from .const import (
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
from .devices import TuyaBLECoordinator, TuyaBLEData, get_device_product_info
from .tuya_ble import (
    AbstractTuyaBLEDeviceManager,
    TuyaBLEDevice,
    TuyaBLEDeviceCredentials,
)

PLATFORMS: list[Platform] = [
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.COVER,
    Platform.LIGHT,
    Platform.LOCK,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SELECT,
    Platform.SWITCH,
    Platform.TEXT,
    Platform.VALVE,
]


class OfflineTuyaBLEDeviceManager(AbstractTuyaBLEDeviceManager):
    """Offline manager that returns stored credentials without cloud calls."""

    def __init__(self, credentials: TuyaBLEDeviceCredentials) -> None:
        """Initialize with pre-built credentials."""
        self._credentials = credentials

    async def get_device_credentials(
        self,
        address: str,
    ) -> TuyaBLEDeviceCredentials | None:
        """Return the stored credentials."""
        return self._credentials


def _build_credentials_from_entry(entry: ConfigEntry) -> TuyaBLEDeviceCredentials:
    """Build TuyaBLEDeviceCredentials from stored config entry data."""
    data = entry.data
    return TuyaBLEDeviceCredentials(
        uuid=data[CONF_UUID],
        local_key=data[CONF_LOCAL_KEY],
        device_id=data[CONF_DEVICE_ID],
        category=data[CONF_CATEGORY],
        product_id=data[CONF_PRODUCT_ID],
        device_name=data.get(CONF_DEVICE_NAME),
        product_model=data.get(CONF_PRODUCT_MODEL),
        product_name=data.get(CONF_PRODUCT_NAME),
        functions=data.get(CONF_FUNCTIONS),
        status_range=data.get(CONF_STATUS_RANGE),
    )


@callback
def _remove_legacy_sensor_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove sensor-domain entries superseded by correctly typed entities."""
    registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(registry, entry.entry_id)
    replacement_unique_ids = {
        entity.unique_id
        for entity in entries
        if entity.platform == DOMAIN and entity.domain != Platform.SENSOR
    }
    for entity in entries:
        if (
            entity.platform == DOMAIN
            and entity.domain == Platform.SENSOR
            and entity.unique_id in replacement_unique_ids
        ):
            registry.async_remove(entity.entity_id)


_QYCALACN_UNIQUE_ID_RENAMES = {
    "countdown_duration_z1": "countdown_zone1",
    "countdown_duration_z2": "countdown_zone2",
    "use_time_z1": "last_use_time_zone1",
    "use_time_z2": "last_use_time_zone2",
}


@callback
def _migrate_qycalacn_unique_ids(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Rename legacy qycalacn entity unique_ids to the new mapping keys."""
    if entry.data.get(CONF_PRODUCT_ID) != "qycalacn":
        return
    registry = er.async_get(hass)
    prefix = f"{entry.data[CONF_DEVICE_ID]}-"
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.platform != DOMAIN or not entity.unique_id.startswith(prefix):
            continue
        if new_key := _QYCALACN_UNIQUE_ID_RENAMES.get(
            entity.unique_id.removeprefix(prefix)
        ):
            registry.async_update_entity(
                entity.entity_id, new_unique_id=f"{prefix}{new_key}"
            )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Tuya BLE from a config entry."""
    address: str = entry.data[CONF_ADDRESS]
    ble_device = bluetooth.async_ble_device_from_address(
        hass, address.upper(), True
    ) or await get_device(address)
    if not ble_device:
        raise ConfigEntryNotReady(
            f"Could not find Tuya BLE device with address {address}"
        )

    credentials = _build_credentials_from_entry(entry)
    manager = OfflineTuyaBLEDeviceManager(credentials)
    device = TuyaBLEDevice(manager, ble_device)
    await device.initialize_with_credentials(credentials)

    product_info = get_device_product_info(device)
    if product_info is None:
        raise ConfigEntryNotReady(f"Unknown device: {device.product_id}")

    coordinator = TuyaBLECoordinator(hass, device)

    hass.add_job(device.update())

    @callback
    def _async_update_ble(
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        """Push updated BLE advertisement data into the device."""
        device.set_ble_device_and_advertisement_data(
            service_info.device, service_info.advertisement
        )

    entry.async_on_unload(
        bluetooth.async_register_callback(
            hass,
            _async_update_ble,
            BluetoothCallbackMatcher({"address": address}),
            bluetooth.BluetoothScanningMode.ACTIVE,
        )
    )

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = TuyaBLEData(
        entry.title,
        device,
        product_info,
        manager,
        coordinator,
    )

    # Rename legacy qycalacn unique_ids before platforms forward, so the newly
    # created entities reuse the registry entries (history/automations kept).
    _migrate_qycalacn_unique_ids(hass, entry)

    # Remove known legacy-domain duplicates before platforms can load them.
    # Keep the second pass for an entry's first setup with the corrected domains,
    # when replacement registry entries do not exist until forwarding completes.
    _remove_legacy_sensor_entities(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _remove_legacy_sensor_entities(hass, entry)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    async def _async_stop(event: Event) -> None:
        """Close the connection."""
        await device.stop()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_stop)
    )
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry when the title changes in options."""
    data: TuyaBLEData = hass.data[DOMAIN][entry.entry_id]
    if entry.title != data.title:
        await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        data: TuyaBLEData = hass.data[DOMAIN].pop(entry.entry_id)
        await data.device.stop()

    return unload_ok
