"""Product registry and device info helpers for Tuya BLE devices."""

from __future__ import annotations

from dataclasses import dataclass

from home_assistant_bluetooth import BluetoothServiceInfoBleak

from .const import DEVICE_DEF_MANUFACTURER
from .tuya_ble import (
    AbstractTuyaBLEDeviceManager,
    TuyaBLEDevice,
    TuyaBLEDeviceCredentials,
)

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
