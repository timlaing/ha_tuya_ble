"""Switch platform for Tuya BLE devices (fingerbots, water valves, locks, TRVs)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from homeassistant.components.switch import (
    SwitchEntity,
    SwitchEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .devices import TuyaBLECoordinator, TuyaBLEData, TuyaBLEEntity, TuyaBLEProductInfo
from .tuya_ble import TuyaBLEDataPoint, TuyaBLEDataPointType, TuyaBLEDevice

ICON_REPEAT = "mdi:repeat"

TuyaBLESwitchGetter = (
    Callable[["TuyaBLESwitch", TuyaBLEProductInfo], bool | None] | None
)


TuyaBLESwitchIsAvailable = Callable[["TuyaBLESwitch", TuyaBLEProductInfo], bool] | None


TuyaBLESwitchSetter = Callable[["TuyaBLESwitch", TuyaBLEProductInfo, bool], None] | None


@dataclass
class TuyaBLESwitchMapping:
    """Mapping of a Tuya data point to a switch entity."""

    dp_id: int
    description: SwitchEntityDescription
    force_add: bool = True
    dp_type: TuyaBLEDataPointType | None = None
    bitmap_mask: bytes | None = None
    is_available: TuyaBLESwitchIsAvailable = None
    getter: TuyaBLESwitchGetter = None
    setter: TuyaBLESwitchSetter = None


def is_fingerbot_in_program_mode(
    self: TuyaBLESwitch, product: TuyaBLEProductInfo
) -> bool:
    """Return True if the fingerbot is in program mode."""
    result: bool = True
    if product.fingerbot:
        datapoint = self.device.datapoints[product.fingerbot.mode]
        if datapoint:
            result = datapoint.value == 2
    return result


def is_fingerbot_in_switch_mode(
    self: TuyaBLESwitch, product: TuyaBLEProductInfo
) -> bool:
    """Return True if the fingerbot is in switch mode."""
    result: bool = True
    if product.fingerbot:
        datapoint = self.device.datapoints[product.fingerbot.mode]
        if datapoint:
            result = datapoint.value == 1
    return result


def get_fingerbot_program_repeat_forever(
    self: TuyaBLESwitch, product: TuyaBLEProductInfo
) -> bool | None:
    """Return whether the fingerbot program repeats forever."""
    result: bool | None = None
    if product.fingerbot and product.fingerbot.program:
        datapoint = self.device.datapoints[product.fingerbot.program]
        if datapoint and isinstance(datapoint.value, bytes):
            repeat_count = int.from_bytes(datapoint.value[0:2], "big")
            result = repeat_count == 0xFFFF
    return result


def set_fingerbot_program_repeat_forever(
    self: TuyaBLESwitch, product: TuyaBLEProductInfo, value: bool
) -> None:
    """Set whether the fingerbot program repeats forever."""
    if product.fingerbot and product.fingerbot.program:
        datapoint = self.device.datapoints[product.fingerbot.program]
        if datapoint and isinstance(datapoint.value, bytes):
            new_value = (
                int.to_bytes(0xFFFF if value else 1, 2, "big") + datapoint.value[2:]
            )
            self.hass.create_task(datapoint.set_value(new_value))


def is_water_valve_in_switch_mode(
    self: TuyaBLESwitch, product: TuyaBLEProductInfo
) -> bool:
    """Return True if the product is a water valve."""
    return product.watervalve is not None


def set_16wgjvck_water_valve(
    self: TuyaBLESwitch, product: TuyaBLEProductInfo, value: bool
) -> None:
    """Set the Aldi/Ferrex Smart Water Valve state."""
    if value:
        dp_11_val = 60
        dp15 = self.device.datapoints[15]
        dp11 = self.device.datapoints[11]
        if dp15 and dp15.value:
            dp_11_val = int(dp15.value)
        elif dp11 and dp11.value:
            dp_11_val = int(dp11.value)
        if dp_11_val <= 0:
            dp_11_val = 60

        dp_2_val = 100
        dp2 = self.device.datapoints[2]
        if dp2 and dp2.value is not None:
            dp_2_val = int(dp2.value)
        if dp_2_val <= 0:
            dp_2_val = 100

        self.send_multiple_dp_values([
            (1, TuyaBLEDataPointType.DT_BOOL, True),
            (2, TuyaBLEDataPointType.DT_VALUE, dp_2_val),
            (11, TuyaBLEDataPointType.DT_VALUE, dp_11_val),
        ])
    else:
        self.send_dp_value(1, TuyaBLEDataPointType.DT_BOOL, False)


@dataclass
class TuyaBLEFingerbotSwitchMapping(TuyaBLESwitchMapping):
    """Switch mapping for fingerbot devices."""

    description: SwitchEntityDescription = field(
        default_factory=lambda: SwitchEntityDescription(
            key="switch",
        )
    )
    is_available: TuyaBLESwitchIsAvailable = is_fingerbot_in_switch_mode


@dataclass
class TuyaBLEReversePositionsMapping(TuyaBLESwitchMapping):
    """Switch mapping for fingerbot reverse positions."""

    description: SwitchEntityDescription = field(
        default_factory=lambda: SwitchEntityDescription(
            key="reverse_positions",
            icon="mdi:arrow-up-down-bold",
            entity_category=EntityCategory.CONFIG,
        )
    )
    is_available: TuyaBLESwitchIsAvailable = is_fingerbot_in_switch_mode


@dataclass
class TuyaBLEWaterValveSwitchMapping(TuyaBLESwitchMapping):
    """Switch mapping for water valve devices."""

    description: SwitchEntityDescription = field(
        default_factory=lambda: SwitchEntityDescription(
            key="water_valve",
        )
    )
    is_available: TuyaBLESwitchIsAvailable = is_water_valve_in_switch_mode


@dataclass
class TuyaLockMotorStateMapping(TuyaBLESwitchMapping):
    """Switch mapping for lock motor state."""

    description: SwitchEntityDescription = field(
        default_factory=lambda: SwitchEntityDescription(
            key="lock_motor_state",
        )
    )


@dataclass
class TuyaBLEWaterValveWeatherSwitchMapping(TuyaBLESwitchMapping):
    """Switch mapping for water valve weather switch."""

    description: SwitchEntityDescription = field(
        default_factory=lambda: SwitchEntityDescription(
            key="weather_switch",
            icon="mdi:cloud-question",
        )
    )


@dataclass
class TuyaBLECategorySwitchMapping:
    """Mapping of product IDs to switch mappings within a device category."""

    products: dict[str, list[TuyaBLESwitchMapping]] | None = None
    mapping: list[TuyaBLESwitchMapping] | None = None


mapping: dict[str, TuyaBLECategorySwitchMapping] = {
    "co2bj": TuyaBLECategorySwitchMapping(
        products={
            "59s19z5m": [  # CO2 Detector
                TuyaBLESwitchMapping(
                    dp_id=11,
                    description=SwitchEntityDescription(
                        key="carbon_dioxide_severely_exceed_alarm",
                        icon="mdi:molecule-co2",
                        entity_category=EntityCategory.CONFIG,
                        entity_registry_enabled_default=False,
                    ),
                    bitmap_mask=b"\x01",
                ),
                TuyaBLESwitchMapping(
                    dp_id=11,
                    description=SwitchEntityDescription(
                        key="low_battery_alarm",
                        icon="mdi:battery-alert",
                        entity_category=EntityCategory.CONFIG,
                        entity_registry_enabled_default=False,
                    ),
                    bitmap_mask=b"\x02",
                ),
                TuyaBLESwitchMapping(
                    dp_id=13,
                    description=SwitchEntityDescription(
                        key="carbon_dioxide_alarm_switch",
                        icon="mdi:molecule-co2",
                        entity_category=EntityCategory.CONFIG,
                    ),
                ),
            ],
        },
    ),
    "ms": TuyaBLECategorySwitchMapping(
        products={
            **{
                k: [TuyaLockMotorStateMapping(dp_id=47)]
                for k in ["ludzroix", "isk2p555", "gumrixyt", "sidhzylo"]
            },
            **{
                k: [
                    TuyaLockMotorStateMapping(dp_id=47),
                    TuyaBLESwitchMapping(
                        dp_id=46, description=SwitchEntityDescription(key="manual_lock")
                    ),
                ]
                for k in ["uamrw6h3", "mqc2hevy"]
            },
            "a6nttc41": [TuyaLockMotorStateMapping(dp_id=33)],
        }
    ),
    "szjqr": TuyaBLECategorySwitchMapping(
        products={
            **{
                k: [
                    TuyaBLEFingerbotSwitchMapping(dp_id=1),
                    TuyaBLEReversePositionsMapping(dp_id=4),
                ]
                for k in ["3yqdo5yt", "xhf790if"]
            },
            **{
                k: [
                    TuyaBLEFingerbotSwitchMapping(dp_id=2),
                    TuyaBLEReversePositionsMapping(dp_id=11),
                    TuyaBLESwitchMapping(
                        dp_id=17,
                        description=SwitchEntityDescription(
                            key="manual_control",
                            icon="mdi:gesture-tap-box",
                            entity_category=EntityCategory.CONFIG,
                        ),
                    ),
                    TuyaBLESwitchMapping(
                        dp_id=2,
                        description=SwitchEntityDescription(
                            key="program", icon=ICON_REPEAT
                        ),
                        is_available=is_fingerbot_in_program_mode,
                    ),
                    TuyaBLESwitchMapping(
                        dp_id=121,
                        description=SwitchEntityDescription(
                            key="program_repeat_forever",
                            icon=ICON_REPEAT,
                            entity_category=EntityCategory.CONFIG,
                        ),
                        getter=get_fingerbot_program_repeat_forever,
                        is_available=is_fingerbot_in_program_mode,
                        setter=set_fingerbot_program_repeat_forever,
                    ),
                ]
                for k in [
                    "blliqpsj",
                    "ndvkgsrm",
                    "yiihr7zh",
                    "neq16kgd",
                    "6jcvqwh0",
                    "riecov42",
                    "h8kdwywx",
                ]
            },
            **{
                k: [
                    TuyaBLEFingerbotSwitchMapping(dp_id=2),
                    TuyaBLEReversePositionsMapping(dp_id=11),
                ]
                for k in [
                    "ltak7e1p",
                    "y6kttvd6",
                    "yrnk7mnn",
                    "nvr2rocq",
                    "bnt7wajf",
                    "rvdceqjh",
                    "5xhbk964",
                ]
            },
            "yn4x5fa7": [
                TuyaBLEFingerbotSwitchMapping(dp_id=1),
                TuyaBLEReversePositionsMapping(dp_id=6),
            ],
        },
    ),
    "wk": TuyaBLECategorySwitchMapping(
        products={
            **{
                k: [
                    TuyaBLESwitchMapping(
                        dp_id=8,
                        description=SwitchEntityDescription(
                            key="window_check",
                            icon="mdi:window-closed",
                            entity_category=EntityCategory.CONFIG,
                        ),
                    ),
                    TuyaBLESwitchMapping(
                        dp_id=10,
                        description=SwitchEntityDescription(
                            key="antifreeze",
                            icon="mdi:snowflake-off",
                            entity_category=EntityCategory.CONFIG,
                        ),
                    ),
                    TuyaBLESwitchMapping(
                        dp_id=40,
                        description=SwitchEntityDescription(
                            key="child_lock",
                            icon="mdi:account-lock",
                            entity_category=EntityCategory.CONFIG,
                        ),
                    ),
                    TuyaBLESwitchMapping(
                        dp_id=130,
                        description=SwitchEntityDescription(
                            key="water_scale_proof",
                            icon="mdi:water-check",
                            entity_category=EntityCategory.CONFIG,
                        ),
                    ),
                    TuyaBLESwitchMapping(
                        dp_id=107,
                        description=SwitchEntityDescription(
                            key="programming_mode",
                            icon="mdi:calendar-edit",
                            entity_category=EntityCategory.CONFIG,
                        ),
                    ),
                    TuyaBLESwitchMapping(
                        dp_id=108,
                        description=SwitchEntityDescription(
                            key="programming_switch",
                            icon="mdi:calendar-clock",
                            entity_category=EntityCategory.CONFIG,
                        ),
                    ),
                ]
                for k in ["drlajpqc", "nhj2j7su", "zmachryv"]
            },
        },
    ),
    "wsdcg": TuyaBLECategorySwitchMapping(
        products={
            "ojzlzzsw": [  # Soil moisture sensor
                TuyaBLESwitchMapping(
                    dp_id=21,
                    description=SwitchEntityDescription(
                        key="switch",
                        icon="mdi:thermometer",
                        entity_category=EntityCategory.CONFIG,
                        entity_registry_enabled_default=False,
                    ),
                ),
            ],
        },
    ),
    "sfkzq": TuyaBLECategorySwitchMapping(
        products={
            **{
                k: [
                    TuyaBLESwitchMapping(
                        dp_id=1,
                        description=SwitchEntityDescription(
                            key="water_valve", entity_registry_enabled_default=True
                        ),
                    )
                ]
                for k in ["0axr5s0b", "46zia2nz", "1fcnd8xk"]
            },
            "ldcdnigc": [
                TuyaBLESwitchMapping(
                    dp_id=1,
                    description=SwitchEntityDescription(
                        key="water_valve",
                        icon="mdi:valve",
                    ),
                ),
            ],
            "16wgjvck": [  # Aldi/Ferrex Smart Water Valve
                TuyaBLESwitchMapping(
                    dp_id=1,
                    description=SwitchEntityDescription(
                        key="water_valve",
                        icon="mdi:valve",
                    ),
                    setter=set_16wgjvck_water_valve,
                ),
            ],
            **{
                k: [
                    TuyaBLEWaterValveSwitchMapping(dp_id=1),
                    TuyaBLEWaterValveWeatherSwitchMapping(dp_id=14),
                ]
                for k in ["nxquc5lb", "svhikeyq"]
            },
        },
    ),
    "ggq": TuyaBLECategorySwitchMapping(
        products={
            "6pahkcau": [  # Irrigation computer PARKSIDE PPB A1
                TuyaBLESwitchMapping(
                    dp_id=1,
                    description=SwitchEntityDescription(
                        key="water_valve",
                        entity_registry_enabled_default=True,
                    ),
                ),
            ],
            **{
                k: [
                    TuyaBLESwitchMapping(
                        dp_id=105,
                        description=SwitchEntityDescription(
                            key="water_valve_z1", entity_registry_enabled_default=True
                        ),
                    ),
                    TuyaBLESwitchMapping(
                        dp_id=104,
                        description=SwitchEntityDescription(
                            key="water_valve_z2", entity_registry_enabled_default=True
                        ),
                    ),
                ]
                for k in ["hfgdqhho", "qycalacn", "fnlw6npo", "jjqi2syk"]
            },
        },
    ),
    "dcb": TuyaBLECategorySwitchMapping(
        products={
            **{
                k: [
                    TuyaBLESwitchMapping(
                        dp_id=12,
                        description=SwitchEntityDescription(
                            key="upper_temp_switch",
                            icon="mdi:thermometer-chevron-up",
                            entity_category=EntityCategory.CONFIG,
                        ),
                    ),
                    TuyaBLESwitchMapping(
                        dp_id=22,
                        description=SwitchEntityDescription(
                            key="security_switch",
                            icon="mdi:shield-lock-outline",
                            entity_category=EntityCategory.CONFIG,
                        ),
                    ),
                    TuyaBLESwitchMapping(
                        dp_id=155,
                        description=SwitchEntityDescription(
                            key="kick_back_switch",
                            icon="mdi:car-esp",
                            entity_category=EntityCategory.CONFIG,
                        ),
                    ),
                    TuyaBLESwitchMapping(
                        dp_id=163,
                        description=SwitchEntityDescription(
                            key="lamp_switch",
                            icon="mdi:lightbulb",
                            entity_category=EntityCategory.CONFIG,
                        ),
                    ),
                    TuyaBLESwitchMapping(
                        dp_id=170,
                        description=SwitchEntityDescription(
                            key="cw_or_ccw_control",
                            icon="mdi:rotate-right",
                            entity_category=EntityCategory.CONFIG,
                        ),
                    ),
                    TuyaBLESwitchMapping(
                        dp_id=185,
                        description=SwitchEntityDescription(
                            key="laser_switch",
                            icon="mdi:laser-pointer",
                            entity_category=EntityCategory.CONFIG,
                        ),
                    ),
                    TuyaBLESwitchMapping(
                        dp_id=186,
                        description=SwitchEntityDescription(
                            key="laser_pulse_switch",
                            icon="mdi:pulse",
                            entity_category=EntityCategory.CONFIG,
                        ),
                    ),
                ]
                for k in ["ajrhf1aj", "z5ztlw3k"]
            },
        },
    ),
    "kg": TuyaBLECategorySwitchMapping(
        products={
            **{
                k: [
                    TuyaBLEFingerbotSwitchMapping(dp_id=1),
                    TuyaBLEReversePositionsMapping(dp_id=104),
                    TuyaBLESwitchMapping(
                        dp_id=107,
                        description=SwitchEntityDescription(
                            key="manual_control",
                            icon="mdi:gesture-tap-box",
                            entity_category=EntityCategory.CONFIG,
                        ),
                    ),
                    TuyaBLESwitchMapping(
                        dp_id=1,
                        description=SwitchEntityDescription(
                            key="program", icon=ICON_REPEAT
                        ),
                        is_available=is_fingerbot_in_program_mode,
                    ),
                    TuyaBLESwitchMapping(
                        dp_id=109,
                        description=SwitchEntityDescription(
                            key="program_repeat_forever",
                            icon=ICON_REPEAT,
                            entity_category=EntityCategory.CONFIG,
                        ),
                        getter=get_fingerbot_program_repeat_forever,
                        is_available=is_fingerbot_in_program_mode,
                        setter=set_fingerbot_program_repeat_forever,
                    ),
                ]
                for k in ["mknd4lci", "riecov42", "bs3ubslo"]
            },
            "4ctjfrzq": [
                TuyaBLESwitchMapping(
                    dp_id=1,
                    description=SwitchEntityDescription(
                        key="switch",
                    ),
                ),
            ],
        },
    ),
}


def get_mapping_by_device(device: TuyaBLEDevice) -> list[TuyaBLESwitchMapping]:
    """Return the switch mappings for a given device."""
    category = mapping.get(device.category)
    if category is not None and category.products is not None:
        product_mapping = category.products.get(device.product_id)
        if product_mapping is not None:
            return product_mapping
        if category.mapping is not None:
            return category.mapping
        return []
    return []


class TuyaBLESwitch(TuyaBLEEntity, SwitchEntity):
    """Representation of a Tuya BLE Switch."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: TuyaBLECoordinator,
        device: TuyaBLEDevice,
        product: TuyaBLEProductInfo,
        switch_mapping: TuyaBLESwitchMapping,
    ) -> None:
        super().__init__(hass, coordinator, device, product, switch_mapping.description)
        self._mapping = switch_mapping

    @property
    def is_on(self) -> bool:
        """Return true if switch is on."""

        if self._mapping.getter is not None:
            return self._mapping.getter(self, self._product) or False

        datapoint = self.device.datapoints[self._mapping.dp_id]
        if not datapoint:
            return False
        if (
            datapoint.dp_type
            in [TuyaBLEDataPointType.DT_RAW, TuyaBLEDataPointType.DT_BITMAP]
            and self._mapping.bitmap_mask
        ):
            return self._read_bitmap_on(datapoint)
        return bool(datapoint.value)

    def _read_bitmap_on(self, datapoint: TuyaBLEDataPoint) -> bool:
        """Return True if any masked bit is set in a bitmap datapoint."""
        bitmap_mask = self._mapping.bitmap_mask
        if not isinstance(datapoint.value, (bytes, bytearray)) or bitmap_mask is None:
            return False
        bitmap_value = bytes(datapoint.value)
        return any((v & m) != 0 for v, m in zip(bitmap_value, bitmap_mask, strict=True))

    def _read_bitmap(self, datapoint: TuyaBLEDataPoint) -> bytes:
        """Return the current bitmap value from a datapoint."""
        return (
            bytes(datapoint.value)
            if isinstance(datapoint.value, (bytes, bytearray))
            else b""
        )

    def _write_bitmap(self, transform: Callable[[int, int], int]) -> None:
        """Write the bitmap datapoint after applying a transform to each byte."""
        bitmap_mask = self._mapping.bitmap_mask
        if bitmap_mask is None:
            return
        datapoint = self.device.datapoints.get_or_create(
            self._mapping.dp_id,
            TuyaBLEDataPointType.DT_BITMAP,
            bitmap_mask,
        )
        bitmap_value = self._read_bitmap(datapoint)
        new_value = bytes(
            transform(v, m) for (v, m) in zip(bitmap_value, bitmap_mask, strict=True)
        )
        self.hass.create_task(datapoint.set_value(new_value))

    def turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        if self._mapping.setter:
            self._mapping.setter(self, self._product, True)
            return

        if self._mapping.bitmap_mask:
            self._write_bitmap(lambda v, m: v | m)
            return
        datapoint = self.device.datapoints.get_or_create(
            self._mapping.dp_id,
            TuyaBLEDataPointType.DT_BOOL,
            True,
        )
        self.hass.create_task(datapoint.set_value(True))

    def turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        if self._mapping.setter:
            self._mapping.setter(self, self._product, False)
            return

        if self._mapping.bitmap_mask:
            self._write_bitmap(lambda v, m: v & ~m)
            return
        datapoint = self.device.datapoints.get_or_create(
            self._mapping.dp_id,
            TuyaBLEDataPointType.DT_BOOL,
            False,
        )
        self.hass.create_task(datapoint.set_value(False))

    @property
    def available(self) -> bool:
        """True when coordinator is connected and the availability predicate passes."""
        result = super().available
        if result and self._mapping.is_available:
            result = self._mapping.is_available(self, self._product)
        return result


async def async_setup_entry(  # noqa: S7503
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tuya BLE switch entities for the config entry."""
    data: TuyaBLEData = hass.data[DOMAIN][entry.entry_id]
    mappings = get_mapping_by_device(data.device)
    entities: list[TuyaBLESwitch] = []
    for switch_mapping in mappings:
        if switch_mapping.force_add or data.device.datapoints.has_id(
            switch_mapping.dp_id, switch_mapping.dp_type
        ):
            entities.append(
                TuyaBLESwitch(
                    hass,
                    data.coordinator,
                    data.device,
                    data.product,
                    switch_mapping,
                )
            )
    async_add_entities(entities)
