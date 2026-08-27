"""Device registry, coordinator, entity base class, and product database."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from home_assistant_bluetooth import BluetoothServiceInfoBleak
from homeassistant.const import CONF_ADDRESS, CONF_DEVICE_ID
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import (
    EntityDescription,
    generate_entity_id,
)
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .base import EnumTypeData, IntegerTypeData
from .const import (
    DEVICE_DEF_MANUFACTURER,
    DOMAIN,
    FINGERBOT_BUTTON_EVENT,
    SET_DISCONNECTED_DELAY,
    DPCode,
    DPType,
)
from .tuya_ble import (
    AbstractTuyaBLEDeviceManager,
    TuyaBLEDataPoint,
    TuyaBLEDataPointType,
    TuyaBLEDevice,
    TuyaBLEDeviceCredentials,
)

_LOGGER = logging.getLogger(__name__)

PRODUCT_NAME_TH_SENSOR = "Temperature Humidity Sensor"


@dataclass
class TuyaBLEFingerbotInfo:
    """Information about fingerbot datapoint IDs for a device."""

    switch: int
    mode: int
    up_position: int
    down_position: int
    hold_time: int
    reverse_positions: int
    manual_control: int = 0
    program: int = 0


@dataclass
class TuyaBLEWaterValveInfo:
    """Information about water valve datapoint IDs for a device."""

    switch: int
    countdown: int
    weather_delay: int
    smart_weather: int
    use_time: int


@dataclass
class TuyaBLEProductInfo:
    """Product information for a Tuya BLE device."""

    name: str
    manufacturer: str = DEVICE_DEF_MANUFACTURER
    fingerbot: TuyaBLEFingerbotInfo | None = None
    watervalve: TuyaBLEWaterValveInfo | None = None
    lock: int | None = None


class TuyaBLEEntity(CoordinatorEntity["TuyaBLECoordinator"]):
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
        self._coordinator = coordinator
        self.device = device
        self._product = product
        if description.translation_key is None:
            self._attr_translation_key = description.key
        self.entity_description = description
        self._attr_has_entity_name = True
        self._attr_device_info = get_device_info(self.device)
        self._attr_unique_id = f"{self.device.device_id}-{description.key}"
        self.entity_id = generate_entity_id(
            "sensor.{}", self._attr_unique_id, hass=hass
        )

    @property
    def available(self) -> bool:
        """True when coordinator is connected and the availability predicate passes."""
        return self._coordinator.connected

    @callback
    def _handle_coordinator_update(self) -> None:
        """Push the latest HA state when the coordinator fires an update."""
        self.async_write_ha_state()

    def send_dp_value(
        self,
        key: int | None,
        dp_type: TuyaBLEDataPointType,
        value: bytes | bool | int | str | None = None,
    ) -> None:
        """Send a data point value to the device."""
        if key is None or value is None:
            return
        datapoint = self.device.datapoints.get_or_create(
            key,
            dp_type,
            value,
        )
        self.hass.create_task(datapoint.set_value(value))

    def send_multiple_dp_values(
        self,
        updates: list[tuple[int, TuyaBLEDataPointType, bytes | bool | int | str]],
    ) -> None:
        """Send multiple data point values to the device atomically."""
        dp_updates: dict[int, bytes | bool | int | str] = {}
        for key, dp_type, value in updates:
            self.device.datapoints.get_or_create(key, dp_type, value)
            dp_updates[key] = value
        self.hass.create_task(self.device.set_multiple_values(dp_updates))

    def find_dpid(
        self, dpcode: DPCode | None, prefer_function: bool = False
    ) -> int | None:
        """Return the dp id for the given code."""
        if dpcode is None:
            return None

        order = ["status_range", "function"]
        if prefer_function:
            order = ["function", "status_range"]
        for key in order:
            if dpcode in getattr(self.device, key):
                return int(getattr(self.device, key)[dpcode].dp_id)

        return None

    def find_dpcode(
        self,
        dpcodes: str | DPCode | tuple[DPCode, ...] | None,
        *,
        prefer_function: bool = False,
        dptype: DPType | None = None,
    ) -> DPCode | EnumTypeData | IntegerTypeData | None:
        """Find a matching DP code available on this device."""
        if dpcodes is None:
            return None

        if isinstance(dpcodes, str):
            dpcodes = (DPCode(dpcodes),)
        elif not isinstance(dpcodes, tuple):
            dpcodes = (dpcodes,)

        order = ["status_range", "function"]
        if prefer_function:
            order = ["function", "status_range"]

        if not dptype:
            order.append("status")

        for dpcode in dpcodes:
            result = self._match_dpcode(dpcode, order, dptype)
            if result is not None:
                return result

        return None

    def _match_dpcode(
        self,
        dpcode: DPCode,
        order: list[str],
        dptype: DPType | None,
    ) -> DPCode | EnumTypeData | IntegerTypeData | None:
        """Check a single dpcode against ordered device attribute dicts."""
        for key in order:
            attrs = getattr(self.device, key)
            if dpcode not in attrs:
                continue
            entry = attrs[dpcode]
            if dptype == DPType.ENUM and entry.type == DPType.ENUM:
                parsed = EnumTypeData.from_json(dpcode, entry.values)
                if parsed is not None:
                    return parsed
            elif dptype == DPType.INTEGER and entry.type == DPType.INTEGER:
                parsed = IntegerTypeData.from_json(  # type: ignore[assignment]
                    dpcode, entry.values
                )
                if parsed is not None:
                    return parsed
            elif dptype not in (DPType.ENUM, DPType.INTEGER):
                return dpcode
        return None

    def get_dptype(
        self, dpcode: DPCode | None, prefer_function: bool = False
    ) -> DPType | None:
        """Return the cloud spec data type for the given code."""
        if dpcode is None:
            return None

        order = ["status_range", "function"]
        if prefer_function:
            order = ["function", "status_range"]
        for key in order:
            if dpcode in getattr(self.device, key):
                return DPType(getattr(self.device, key)[dpcode].type)

        return None

    def _send_command(self, commands: list[dict[str, Any]]) -> None:
        """Send commands to the device."""
        for command in commands:
            dp_id = command.get("dp_id")
            dp_type = command.get("dp_type")
            value = command.get("value")
            if dp_id is not None and dp_type is not None and value is not None:
                self.send_dp_value(dp_id, dp_type, value)


class TuyaBLECoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Data coordinator for receiving Tuya BLE updates."""

    def __init__(self, hass: HomeAssistant, device: TuyaBLEDevice) -> None:
        """Register connect, update, and disconnect callbacks on the BLE device."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
        )
        self.device = device
        self._disconnected: bool = True
        self._unsub_disconnect: CALLBACK_TYPE | None = None
        device.register_connected_callback(self._async_handle_connect)
        device.register_callback(self._async_handle_update)
        device.register_disconnected_callback(self._async_handle_disconnect)

    @property
    def connected(self) -> bool:
        """Return whether the device is currently connected."""
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
        """Broadcast coordinator listeners and fire fingerbot button events."""
        self._async_handle_connect()
        self.async_set_updated_data({})
        info = get_device_product_info(self.device)
        if info and info.fingerbot and info.fingerbot.manual_control != 0:
            for update in updates:
                if update.dp_id == info.fingerbot.switch and update.changed_by_device:
                    self.hass.bus.fire(
                        FINGERBOT_BUTTON_EVENT,
                        {
                            CONF_ADDRESS: self.device.address,
                            CONF_DEVICE_ID: self.device.device_id,
                        },
                    )

    @callback
    def _set_disconnected(self, _: Any) -> None:
        """Invoke the idle timeout callback, called when the alarm fires."""
        self._disconnected = True
        self._unsub_disconnect = None
        self.async_update_listeners()

    @callback
    def _async_handle_disconnect(self) -> None:
        """Schedule a delayed transition to disconnected state."""
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
    manager: AbstractTuyaBLEDeviceManager
    coordinator: TuyaBLECoordinator


@dataclass
class TuyaBLECategoryInfo:
    """Category information for a group of Tuya BLE devices."""

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
                ],
                TuyaBLEProductInfo(  # device product_id
                    name="Smart Lock",
                    lock=1,
                ),
            ),
            "okkyfgfs": TuyaBLEProductInfo(
                name="TEKXDD Fingerprint Smart Lock",
                lock=1,
            ),
            "k53ok3u9": TuyaBLEProductInfo(
                name="Fingerprint Smart Lock",
                lock=1,
            ),
        },
    ),
    "dcb": TuyaBLECategoryInfo(
        products={
            "z5ztlw3k": TuyaBLEProductInfo(  # device product_id
                name="PARKSIDE Smart battery 4Ah",
            ),
            "ajrhf1aj": TuyaBLEProductInfo(  # device product_id
                name="PARKSIDE Smart battery 8Ah",
            ),
        },
    ),
    "jtmspro": TuyaBLECategoryInfo(
        products={
            "xicdxood": TuyaBLEProductInfo(name="Raycube K7 Pro+", lock=1),
            "oyqux5vv": TuyaBLEProductInfo(name="LA-01 Smart lock", lock=1),
            "rlyxv7pe": TuyaBLEProductInfo(name="A1 PRO MAX", lock=1),
            "ebd5e0uauqx0vfsp": TuyaBLEProductInfo(name="CentralAcesso"),
            "ajk32biq": TuyaBLEProductInfo(name="B16", lock=1),
            "z7lj676i": TuyaBLEProductInfo(name="Smart Cylinder Lock", lock=1),
            "hs21i377": TuyaBLEProductInfo(name="Smart Cylinder Lock"),
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
                    "neq16kgd",
                    "6jcvqwh0",
                    "riecov42",
                    "h8kdwywx",
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
            "yn4x5fa7": TuyaBLEProductInfo(
                name="Nedis SmartLife Finger Robot",
                fingerbot=TuyaBLEFingerbotInfo(
                    switch=1,
                    mode=2,
                    up_position=4,
                    down_position=5,
                    hold_time=3,
                    reverse_positions=6,
                ),
            ),
        },
    ),
    "kg": TuyaBLECategoryInfo(
        products={
            **dict.fromkeys(
                ["mknd4lci", "riecov42", "bs3ubslo"],  # device product_ids
                TuyaBLEProductInfo(
                    name="Fingerbot Plus",
                    fingerbot=TuyaBLEFingerbotInfo(
                        switch=1,
                        mode=101,
                        up_position=106,
                        down_position=102,
                        hold_time=103,
                        reverse_positions=104,
                        manual_control=107,
                        program=109,
                    ),
                ),
            ),
            "4ctjfrzq": TuyaBLEProductInfo(
                name="Switch Robot",
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
            "ojzlzzsw": TuyaBLEProductInfo(name="Soil moisture sensor"),
            "iv7hudlj": TuyaBLEProductInfo(name=PRODUCT_NAME_TH_SENSOR),
            "jm6iasmb": TuyaBLEProductInfo(name=PRODUCT_NAME_TH_SENSOR),
            "tv6peegl": TuyaBLEProductInfo(name="Soil Thermo-Hygrometer"),
            "vlzqwckk": TuyaBLEProductInfo(name=PRODUCT_NAME_TH_SENSOR),
            "tr0kabuq": TuyaBLEProductInfo(name=PRODUCT_NAME_TH_SENSOR),
        },
    ),
    "znhsb": TuyaBLECategoryInfo(
        products={
            "cdlandip": TuyaBLEProductInfo(name="Smart water bottle"),
        },
    ),
    "sfkzq": TuyaBLECategoryInfo(
        products={
            "16wgjvck": TuyaBLEProductInfo(
                name="Aldi/Ferrex Smart Water Valve",
                manufacturer="Ferrex",
                watervalve=TuyaBLEWaterValveInfo(
                    switch=1,
                    countdown=11,
                    weather_delay=10,
                    smart_weather=13,
                    use_time=15,
                ),
            ),
            **dict.fromkeys(
                [
                    "6pahkcau",
                    "hfgdqhho",
                    "qycalacn",
                    "fnlw6npo",
                    "jjqi2syk",
                ],  # device product_ids
                TuyaBLEProductInfo(
                    name="Irrigation computer",
                ),
            ),
            **dict.fromkeys(
                [
                    "svhikeyq",
                    "0axr5s0b",
                ],  # device product_id
                TuyaBLEProductInfo(
                    name="Valve controller",
                    watervalve=TuyaBLEWaterValveInfo(
                        switch=1,
                        countdown=11,
                        weather_delay=10,
                        smart_weather=13,
                        use_time=15,
                    ),
                ),
            ),
            **dict.fromkeys(
                [
                    "nxquc5lb",
                    "46zia2nz",
                    "1fcnd8xk",
                ],
                TuyaBLEProductInfo(
                    name="Water valve controller",
                    watervalve=TuyaBLEWaterValveInfo(
                        switch=1,
                        countdown=8,
                        weather_delay=10,
                        smart_weather=13,
                        use_time=9,
                    ),
                ),
            ),
            "ldcdnigc": TuyaBLEProductInfo(
                name="ZX-7378 Smart Irrigation Controller",
            ),
            "fdrbxxbg": TuyaBLEProductInfo(
                name="Diivoo WT-05 dual water timer",
                manufacturer="Diivoo",
            ),
        },
    ),
    "ggq": TuyaBLECategoryInfo(
        products={
            **dict.fromkeys(
                ["6pahkcau", "hfgdqhho"],
                TuyaBLEProductInfo(
                    name="Irrigation computer",
                ),
            ),
        },
    ),
    "dd": TuyaBLECategoryInfo(
        products={
            "nvfrtxlq": TuyaBLEProductInfo(
                name="LGB102 Magic Strip Lights",
                manufacturer="Magiacous",
            ),
            "umzu0c2y": TuyaBLEProductInfo(
                name="Floor Lamp",
                manufacturer="Magiacous",
            ),
            "6jxcdae1": TuyaBLEProductInfo(
                name="Sunset Lamp",
                manufacturer="Comfamoli",
            ),
            "0qgrjxum": TuyaBLEProductInfo(name="RGB Strip Light"),
        },
        info=TuyaBLEProductInfo(
            name="Lights",
        ),
    ),
    "cl": TuyaBLECategoryInfo(
        products={
            **dict.fromkeys(
                ["4pbr8eig", "vlwf3ud6"], TuyaBLEProductInfo(name="Blind Controller")
            ),
            "kcy0x4pi": TuyaBLEProductInfo(name="Curtain Controller"),
            "dy4dh1q0": TuyaBLEProductInfo(name="AOK AM24 Venetian Blinds Motor"),
        },
    ),
    "zwjcy": TuyaBLECategoryInfo(
        products={
            "gvygg3m8": TuyaBLEProductInfo(
                name="Smartlife Plant Sensor SGS01",
            ),
            "jabotj1z": TuyaBLEProductInfo(
                name="SRB-PM01 Soil Moisture Sensor",
            ),
        },
    ),
}


def get_product_info_by_ids(
    category: str, product_id: str
) -> TuyaBLEProductInfo | None:
    """Look up product info by category and product ID."""
    category_info = devices_database.get(category)
    if category_info is not None:
        product_info = category_info.products.get(product_id)
        if product_info is not None:
            return product_info
        return category_info.info
    return None


def get_device_product_info(device: TuyaBLEDevice) -> TuyaBLEProductInfo | None:
    """Get product info for a Tuya BLE device."""
    return get_product_info_by_ids(device.category, device.product_id)


def get_short_address(address: str) -> str:
    """Get a short formatted address from a Bluetooth MAC address."""
    results = address.replace("-", ":").upper().split(":")
    return f"{results[-3]}{results[-2]}{results[-1]}"[-6:]


async def get_device_readable_name(
    discovery_info: BluetoothServiceInfoBleak,
    manager: AbstractTuyaBLEDeviceManager | None,
) -> str:
    """Get a human-readable name for a discovered BLE device."""
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
        return f"{product_info.name} {short_address}"
    if credentials:
        return f"{credentials.device_name} {short_address}"
    return f"{discovery_info.device.name} {short_address}"


def get_device_info(device: TuyaBLEDevice) -> DeviceInfo | None:
    """Get Home Assistant device registry info for a Tuya BLE device."""
    product_info = None
    if device.category and device.product_id:
        product_info = get_product_info_by_ids(device.category, device.product_id)
    product_name: str
    product_name = product_info.name if product_info else device.name
    result = DeviceInfo(
        connections={(dr.CONNECTION_BLUETOOTH, device.address)},
        hw_version=device.hardware_version,
        identifiers={(DOMAIN, device.address)},
        manufacturer=(
            product_info.manufacturer if product_info else DEVICE_DEF_MANUFACTURER
        ),
        model=(f"{device.product_model or product_name} ({device.product_id})"),
        name=(f"{product_name} {get_short_address(device.address)}"),
        sw_version=(f"{device.device_version} (protocol {device.protocol_version})"),
    )
    return result
