"""The Tuya BLE integration."""
from __future__ import annotations
from dataclasses import dataclass

import logging
from homeassistant.const import CONF_ADDRESS, CONF_DEVICE_ID

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import (
    DeviceInfo,
    EntityDescription,
    generate_entity_id,
)
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from home_assistant_bluetooth import BluetoothServiceInfoBleak
from .tuya_ble import (
    AbstaractTuyaBLEDeviceManager,
    TuyaBLEDataPoint,
    TuyaBLEDevice,
    TuyaBLEDeviceCredentials,
)

from .cloud import HASSTuyaBLEDeviceManager
from .const import (
    DEVICE_DEF_MANUFACTURER,
    DOMAIN,
    FINGERBOT_BUTTON_EVENT,
    SET_DISCONNECTED_DELAY,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class TuyaBLEFingerbotInfo:
    switch: int
    mode: int
    up_position: int
    down_position: int
    hold_time: int
    reverse_positions: int
    manual_control: int = 0
    program: int = 0


@dataclass
class TuyaBLEProductInfo:
    name: str
    manufacturer: str = DEVICE_DEF_MANUFACTURER
    fingerbot: TuyaBLEFingerbotInfo | None = None


class TuyaBLEEntity(CoordinatorEntity):
    """Tuya BLE base entity."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: TuyaBLECoordinator,
        device: TuyaBLEDevice,
        product: TuyaBLEProductInfo,
        description: EntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self._hass = hass
        self._coordinator = coordinator
        self._device = device
        self._product = product
        if description.translation_key is None:
            self._attr_translation_key = description.key
        self.entity_description = description
        self._attr_has_entity_name = True
        self._attr_device_info = get_device_info(self._device)
        self._attr_unique_id = f"{self._device.device_id}-{description.key}"
        self.entity_id = generate_entity_id(
            "sensor.{}", self._attr_unique_id, hass=hass
        )

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self._coordinator.connected

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()


class TuyaBLECoordinator(DataUpdateCoordinator[None]):
    """Data coordinator for receiving Tuya BLE updates."""

    def __init__(self, hass: HomeAssistant, device: TuyaBLEDevice) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
        )
        self._device = device
        self._disconnected: bool = True
        self._unsub_disconnect: CALLBACK_TYPE | None = None
        device.register_connected_callback(self._async_handle_connect)
        device.register_callback(self._async_handle_update)
        device.register_disconnected_callback(self._async_handle_disconnect)

    @property
    def connected(self) -> bool:
        return not self._disconnected

    @callback
    def _async_handle_connect(self) -> None:
        if self._unsub_disconnect is not None:
            self._unsub_disconnect()
        if self._disconnected:
            self._disconnected = False
            self.async_update_listeners()

    @callback
    def _async_handle_update(self, updates: list[TuyaBLEDataPoint]) -> None:
        """Just trigger the callbacks."""
        self._async_handle_connect()
        self.async_set_updated_data(None)
        info = get_device_product_info(self._device)
        if info and info.fingerbot and info.fingerbot.manual_control != 0:
            for update in updates:
                if update.id == info.fingerbot.switch and update.changed_by_device:
                    self.hass.bus.fire(
                        FINGERBOT_BUTTON_EVENT,
                        {
                            CONF_ADDRESS: self._device.address,
                            CONF_DEVICE_ID: self._device.device_id,
                        },
                    )

    @callback
    def _set_disconnected(self, _: None) -> None:
        """Invoke the idle timeout callback, called when the alarm fires."""
        self._disconnected = True
        self._unsub_disconnect = None
        self.async_update_listeners()

    @callback
    def _async_handle_disconnect(self) -> None:
        """Trigger the callbacks for disconnected."""
        if self._unsub_disconnect is None:
            delay: float = SET_DISCONNECTED_DELAY
            self._unsub_disconnect = async_call_later(
                self.hass, delay, self._set_disconnected
            )


@dataclass
class TuyaBLEData:
    """Data for the Tuya BLE integration."""

    title: str
    device: TuyaBLEDevice
    product: TuyaBLEProductInfo
    manager: HASSTuyaBLEDeviceManager
    coordinator: TuyaBLECoordinator


@dataclass
class TuyaBLECategoryInfo:
    products: dict[str, TuyaBLEProductInfo]
    info: TuyaBLEProductInfo | None = None


devices_database: dict[str, TuyaBLECategoryInfo] = {
    "co2bj": TuyaBLECategoryInfo(
        products={
            "59s19z5m": TuyaBLEProductInfo(  # device product_id
                name="CO2 Detector",
            ),
        },
    ),
    "ms": TuyaBLECategoryInfo(
        products={
            **dict.fromkeys(
                [
                    "ludzroix",
                    "isk2p555",
                    "gumrixyt",
                    "uamrw6h3",
                    "sidhzylo",
                    "mqc2hevy",
                    "a6nttc41",
                    "okkyfgfs",
                    "k53ok3u9",
                    "bvclwu9b",
                ],
                    TuyaBLEProductInfo(  # device product_id
                    name="Smart Lock",
                ),
            ),
        },
    ),
    "szjqr": TuyaBLECategoryInfo(
        products={
            "3yqdo5yt": TuyaBLEProductInfo(  # device product_id
                name="CUBETOUCH 1s",
                fingerbot=TuyaBLEFingerbotInfo(
                    switch=1,
                    mode=2,
                    up_position=5,
                    down_position=6,
                    hold_time=3,
                    reverse_positions=4,
                ),
            ),
            "xhf790if": TuyaBLEProductInfo(  # device product_id
                name="CubeTouch II",
                fingerbot=TuyaBLEFingerbotInfo(
                    switch=1,
                    mode=2,
                    up_position=5,
                    down_position=6,
                    hold_time=3,
                    reverse_positions=4,
                ),
            ),
            **dict.fromkeys(
                [
                    "blliqpsj",
                    "ndvkgsrm",
                    "yiihr7zh", 
                    "neq16kgd"
                ],  # device product_ids
                TuyaBLEProductInfo(
                    name="Fingerbot Plus",
                    fingerbot=TuyaBLEFingerbotInfo(
                        switch=2,
                        mode=8,
                        up_position=15,
                        down_position=9,
                        hold_time=10,
                        reverse_positions=11,
                        manual_control=17,
                        program=121,
                    ),
                ),
            ),
            **dict.fromkeys(
                [
                    "ltak7e1p",
                    "y6kttvd6",
                    "yrnk7mnn",
                    "nvr2rocq",
                    "bnt7wajf",
                    "rvdceqjh",
                    "5xhbk964",
                ],  # device product_ids
                TuyaBLEProductInfo(
                    name="Fingerbot",
                    fingerbot=TuyaBLEFingerbotInfo(
                        switch=2,
                        mode=8,
                        up_position=15,
                        down_position=9,
                        hold_time=10,
                        reverse_positions=11,
                        program=121,
                    ),
                ),
            ),
        },
    ),
    "jtmspro": TuyaBLECategoryInfo(
        products={
            "xicdxood": TuyaBLEProductInfo(  # device product_id
                name="Smart Lock",
            ),
            "rlyxv7pe": TuyaBLEProductInfo(  # device product_id
                name="Smart Lock",
            ),
            "oyqux5vv": TuyaBLEProductInfo(  # device product_id
                name="Smart Lock",
            ),
            "ebd5e0uauqx0vfsp": TuyaBLEProductInfo(  # device product_id
                name="Smart Lock",
            ),
            "ajk32biq": TuyaBLEProductInfo(  # device product_id
                name="Smart Lock",
            ),
            "z7lj676i": TuyaBLEProductInfo(  # device product_id
                name="Smart Lock",
            ),
            "hs21i377": TuyaBLEProductInfo(  # device product_id
                name="Smart Lock",
            ),
        },
    ),
    "kg": TuyaBLECategoryInfo(
        products={
            "mknd4lci": TuyaBLEProductInfo(  # device product_id
                name="Fingerbot",
            ),
            "riecov42": TuyaBLEProductInfo(  # device product_id
                name="Fingerbot",
            ),
            "bs3ubslo": TuyaBLEProductInfo(  # device product_id
                name="Fingerbot",
            ),
            "4ctjfrzq": TuyaBLEProductInfo(  # device product_id
                name="Fingerbot",
            ),
        },
    ),
    "wk": TuyaBLECategoryInfo(
        products={
            **dict.fromkeys(
            [
            "drlajpqc",
            "nhj2j7su",
            "zmachryv",
            ],  # device product_id
            TuyaBLEProductInfo(
                name="Thermostatic Radiator Valve",
                ),
            ),
        },
    ),
    "wsdcg": TuyaBLECategoryInfo(
        products={
            "ojzlzzsw": TuyaBLEProductInfo(  # device product_id
                name="Soil moisture sensor",
            ),
            "iv7hudlj": TuyaBLEProductInfo(  # device product_id
                name="Temperature and Humidity Sensor",
            ),
            "jm6iasmb": TuyaBLEProductInfo(  # device product_id
                name="Temperature and Humidity Sensor",
            ),
            "tv6peegl": TuyaBLEProductInfo(  # device product_id
                name="Temperature and Humidity Sensor",
            ),
            "vlzqwckk": TuyaBLEProductInfo(  # device product_id
                name="Temperature and Humidity Sensor",
            ),
            "tr0kabuq": TuyaBLEProductInfo(  # device product_id
                name="Temperature and Humidity Sensor",
            ),
        },
    ),
    "zwjcy": TuyaBLECategoryInfo(
        products={
            "jabotj1z": TuyaBLEProductInfo(  # device product_id
                name="Plant Sensor",
            ),
            "gvygg3m8": TuyaBLEProductInfo(  # device product_id
                name="Plant Sensor",
            ),
        },
    ),
    "dcb": TuyaBLECategoryInfo(
        products={
            "z5ztlw3k": TuyaBLEProductInfo(  # device product_id
                name="Smart Battery",
            ),
            "ajrhf1aj": TuyaBLEProductInfo(  # device product_id
                name="Smart Battery",
            ),
        },
    ),
    "cl": TuyaBLECategoryInfo(
        products={
            "4pbr8eig": TuyaBLEProductInfo(  # device product_id
                name="BLE Blind Controller",
            ),
            "vlwf3ud6": TuyaBLEProductInfo(  # device product_id
                name="BLE Blind Controller",
            ),
            "kcy0x4pi": TuyaBLEProductInfo(  # device product_id
                name="BLE Curtain Controller",
            ),
            "dy4dh1q0": TuyaBLEProductInfo(  # device product_id
                name="BLE Venetian Blind Motor",
            ),
            "qqdxfdht": TuyaBLEProductInfo(  # device product_id
                name="BLE Blind Controller",
            ),
            "ulughw4g": TuyaBLEProductInfo(  # device product_id
                name="BLE Blind Controller",
            ),
        },
    ),
    "dd": TuyaBLECategoryInfo(
        products={
            "nvfrtxlq": TuyaBLEProductInfo(  # device product_id
                name="LED Strip Light",
            ),
            "umzu0c2y": TuyaBLEProductInfo(  # device product_id
                name="LED Strip Light",
            ),
            "6jxcdae1": TuyaBLEProductInfo(  # device product_id
                name="LED Strip Light",
            ),
            "0qgrjxum": TuyaBLEProductInfo(  # device product_id
                name="LED Strip Light",
            ),
        },
    ),
    "znhsb": TuyaBLECategoryInfo(
        products={
            "cdlandip":  # device product_id
            TuyaBLEProductInfo(
                name="Smart water bottle",
            ),
        },
    ),
    "ggq": TuyaBLECategoryInfo(
        products={
            "6pahkcau":  # device product_id
            TuyaBLEProductInfo(
                name="Irrigation computer",
            ),
            "hfgdqhho": TuyaBLEProductInfo(  # device product_id
                name="Irrigation computer",
            ),
            "qycalacn": TuyaBLEProductInfo(  # device product_id
                name="Irrigation computer",
            ),
            "fnlw6npo": TuyaBLEProductInfo(  # device product_id
                name="Irrigation computer",
            ),
            "jjqi2syk": TuyaBLEProductInfo(  # device product_id
                name="Irrigation computer",
            ),
        },
    ),
    "sfkzq": TuyaBLECategoryInfo(
        products={
            "fdrbxxbg": TuyaBLEProductInfo(
                name="Diivoo WT-05 dual water timer",
                manufacturer="Diivoo",
            ),
            "nxquc5lb": TuyaBLEProductInfo(
                name="SOP10 water timer",
            ),
            "c8800fd30884068f": TuyaBLEProductInfo(
                name="HCT-611 Water Timer",
            ),
            "so5ybnw9": TuyaBLEProductInfo(
                name="Water Timer",
            ),
            "tqzkwarw": TuyaBLEProductInfo(
                name="HCT-611 Water Timer",
            ),
            "1fcnd8xk": TuyaBLEProductInfo(
                name="Water Timer",
            ),
            "16wgjvck": TuyaBLEProductInfo(
                name="Aldi Water Timer",
                manufacturer="Aldi",
            ),
            "svhikeyq": TuyaBLEProductInfo(
                name="Water Timer",
            ),
            "0axr5s0b": TuyaBLEProductInfo(
                name="Water Timer",
            ),
            "46zia2nz": TuyaBLEProductInfo(
                name="Water Timer",
            ),
            "ldcdnigc": TuyaBLEProductInfo(
                name="Water Timer",
            ),
        },
    ),
}


def get_product_info_by_ids(
    category: str, product_id: str
) -> TuyaBLEProductInfo | None:
    category_info = devices_database.get(category)
    if category_info is not None:
        product_info = category_info.products.get(product_id)
        if product_info is not None:
            return product_info
        return category_info.info
    else:
        return None


def get_device_product_info(device: TuyaBLEDevice) -> TuyaBLEProductInfo | None:
    return get_product_info_by_ids(device.category, device.product_id)


def get_short_address(address: str) -> str:
    results = address.replace("-", ":").upper().split(":")
    return f"{results[-3]}{results[-2]}{results[-1]}"[-6:]


async def get_device_readable_name(
    discovery_info: BluetoothServiceInfoBleak,
    manager: AbstaractTuyaBLEDeviceManager | None,
) -> str:
    credentials: TuyaBLEDeviceCredentials | None = None
    product_info: TuyaBLEProductInfo | None = None
    if manager:
        credentials = await manager.get_device_credentials(discovery_info.address)
        if credentials:
            product_info = get_product_info_by_ids(
                credentials.category,
                credentials.product_id,
            )
    short_address = get_short_address(discovery_info.address)
    if product_info:
        return "%s %s" % (product_info.name, short_address)
    if credentials:
        return "%s %s" % (credentials.device_name, short_address)
    return "%s %s" % (discovery_info.device.name, short_address)


def get_device_info(device: TuyaBLEDevice) -> DeviceInfo | None:
    product_info = None
    if device.category and device.product_id:
        product_info = get_product_info_by_ids(device.category, device.product_id)
    product_name: str
    if product_info:
        product_name = product_info.name
    else:
        product_name = device.name
    result = DeviceInfo(
        connections={(dr.CONNECTION_BLUETOOTH, device.address)},
        hw_version=device.hardware_version,
        identifiers={(DOMAIN, device.address)},
        manufacturer=(
            product_info.manufacturer if product_info else DEVICE_DEF_MANUFACTURER
        ),
        model=("%s (%s)")
        % (
            device.product_model or product_name,
            device.product_id,
        ),
        name=("%s %s")
        % (
            product_name,
            get_short_address(device.address),
        ),
        sw_version=("%s (protocol %s)")
        % (
            device.device_version,
            device.protocol_version,
        ),
    )
    return result
