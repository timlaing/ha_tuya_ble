"""Sensor platform for Tuya BLE devices (temperature, humidity, battery, RSSI)."""

# pylint: disable=too-many-lines

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import ExtraStoredData

from .const import (
    DOMAIN,
)
from .device_descriptors.handlers import rssi as rssi_handler
from .device_registry import (
    EntityDescriptor,
    get_registry,
)
from .devices import (
    TuyaBLECoordinator,
    TuyaBLEData,
    TuyaBLEProductInfo,
    TuyaBLERestoreEntity,
)
from .tuya_ble import TuyaBLEDataPoint, TuyaBLEDataPointType, TuyaBLEDevice

_LOGGER = logging.getLogger(__name__)

SIGNAL_STRENGTH_DP_ID = -1


ICON_BATTERY = "mdi:battery"
ICON_BATTERY_CHECK = "mdi:battery-check"
ICON_COUNTER = "mdi:counter"
ICON_FINGERPRINT = "mdi:fingerprint"

UNIT_CONVERSIONS: dict[str, str] = {
    "%": PERCENTAGE,
    "s": UnitOfTime.SECONDS,
    "min": UnitOfTime.MINUTES,
    "h": UnitOfTime.HOURS,
    "°C": UnitOfTemperature.CELSIUS,
    "℃": UnitOfTemperature.CELSIUS,
}


TuyaBLESensorIsAvailable = Callable[["TuyaBLESensor", TuyaBLEProductInfo], bool] | None


@dataclass
class SensorRestoreData(ExtraStoredData):
    """Extra restore data holding the last native value of a sensor.

    Only the value is stored: the unit comes from the entity description, so a
    stored unit from an older configuration cannot leak into a changed one.
    """

    native_value: Any

    def as_dict(self) -> dict[str, Any]:
        """Return a dict representation of the stored value.

        Sensor values here are numbers, strings or ``None``. Anything else is
        stored as text rather than breaking the state dump.
        """
        if self.native_value is None or isinstance(
            self.native_value, bool | int | float | str
        ):
            return {"native_value": self.native_value}
        return {"native_value": str(self.native_value)}


@dataclass
class TuyaBLESensorMapping:
    """Map a Tuya datapoint to a Home Assistant sensor entity."""

    dp_id: int
    description: SensorEntityDescription
    force_add: bool = True
    restore: bool = False
    dp_type: TuyaBLEDataPointType | None = None
    getter: Callable[[TuyaBLESensor], None] | None = None
    coefficient: float = 1.0
    icons: list[str] | None = None
    is_available: TuyaBLESensorIsAvailable = None


@dataclass
class TuyaBLEBatteryMapping(TuyaBLESensorMapping):
    """Sensor mapping with default battery entity description."""

    description: SensorEntityDescription = field(
        default_factory=lambda: SensorEntityDescription(
            key="battery",
            device_class=SensorDeviceClass.BATTERY,
            native_unit_of_measurement=PERCENTAGE,
            entity_category=EntityCategory.DIAGNOSTIC,
            state_class=SensorStateClass.MEASUREMENT,
        )
    )


@dataclass
class TuyaBLETemperatureMapping(TuyaBLESensorMapping):
    """Sensor mapping with default temperature entity description."""

    description: SensorEntityDescription = field(
        default_factory=lambda: SensorEntityDescription(
            key="temperature",
            device_class=SensorDeviceClass.TEMPERATURE,
            native_unit_of_measurement=UnitOfTemperature.CELSIUS,
            state_class=SensorStateClass.MEASUREMENT,
        )
    )


@dataclass
class TuyaBLECategorySensorMapping:
    """Hold product-specific and category-level sensor mappings."""

    products: dict[str, list[TuyaBLESensorMapping]] | None = None
    mapping: list[TuyaBLESensorMapping] | None = None


def _sensor_description(desc: EntityDescriptor) -> SensorEntityDescription:
    """Build a SensorEntityDescription from a registry descriptor."""
    return SensorEntityDescription(
        key=desc.translation_key or str(desc.dp_id),
        name=desc.name,
        icon=desc.icon,
        device_class=(
            SensorDeviceClass(desc.device_class)
            if desc.device_class is not None
            else None
        ),
        native_unit_of_measurement=(
            UNIT_CONVERSIONS.get(desc.unit, desc.unit)
            if desc.unit is not None
            else None
        ),
        state_class=(
            SensorStateClass(desc.state_class) if desc.state_class is not None else None
        ),
        entity_category=(
            EntityCategory(desc.entity_category)
            if desc.entity_category is not None
            else None
        ),
        options=desc.options,
        suggested_display_precision=desc.suggested_display_precision,
        entity_registry_enabled_default=desc.enabled_by_default is not False,
    )


_KIND_CLASSES: dict[str, type[TuyaBLESensorMapping]] = {
    "battery": TuyaBLEBatteryMapping,
    "temperature": TuyaBLETemperatureMapping,
}


def _build_sensor_mapping(desc: EntityDescriptor) -> TuyaBLESensorMapping:
    """Construct a sensor mapping from a registry descriptor."""
    cls = _KIND_CLASSES.get(desc.kind or "", TuyaBLESensorMapping)
    return cls(
        dp_id=desc.dp_id,
        description=_sensor_description(desc),
        force_add=desc.force_add,
        restore=desc.restore,
        dp_type=(
            TuyaBLEDataPointType(desc.dp_type) if desc.dp_type is not None else None
        ),
        getter=desc.resolved_handler("read"),
        coefficient=desc.coefficient,
        icons=desc.extra.get("icons"),
        is_available=desc.resolved_handler("when"),
    )


def _build_mapping() -> dict[str, TuyaBLECategorySensorMapping]:
    """Build the sensor mappings dict from the device registry."""
    result: dict[str, TuyaBLECategorySensorMapping] = {}
    for device_entities in get_registry().products.values():
        descriptors = device_entities.get("sensor")
        if not descriptors:
            continue
        category_mapping = result.setdefault(
            device_entities.category,
            TuyaBLECategorySensorMapping(products={}),
        )
        assert category_mapping.products is not None
        category_mapping.products[device_entities.product_id] = [
            _build_sensor_mapping(desc) for desc in descriptors
        ]
    return result


mapping: dict[str, TuyaBLECategorySensorMapping] = _build_mapping()


rssi_mapping = TuyaBLESensorMapping(
    dp_id=SIGNAL_STRENGTH_DP_ID,
    description=SensorEntityDescription(
        key="signal_strength",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    getter=rssi_handler.rssi,
)


def get_mapping_by_device(device: TuyaBLEDevice) -> list[TuyaBLESensorMapping]:
    """Return sensor mappings for a given device by category and product."""
    category = mapping.get(device.category)
    if category is not None and category.products is not None:
        product_mapping = category.products.get(device.product_id)
        if product_mapping is not None:
            return product_mapping
        if category.mapping is not None:
            return category.mapping
    return []


class TuyaBLESensor(TuyaBLERestoreEntity, SensorEntity):
    """Representation of a Tuya BLE sensor."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: TuyaBLECoordinator,
        device: TuyaBLEDevice,
        product: TuyaBLEProductInfo,
        sensor_mapping: TuyaBLESensorMapping,
    ) -> None:
        super().__init__(hass, coordinator, device, product, sensor_mapping.description)
        self._mapping = sensor_mapping
        self._attr_restore = sensor_mapping.restore

    @property
    def dp_id(self) -> int:
        """Return the data point id this entity reports."""
        return self._mapping.dp_id

    @property
    def _restore_dp_id(self) -> int:
        """Return the data point id whose presence supersedes a restored value."""
        return self._mapping.dp_id

    @property
    def extra_restore_state_data(self) -> ExtraStoredData | None:
        """Return the native value to store for the next run."""
        return SensorRestoreData(self.native_value)

    async def _async_restore_state(self) -> None:
        """Apply the value stored by the previous run of Home Assistant."""
        if (last_data := await self.async_get_last_extra_data()) is None:
            return
        native_value = last_data.as_dict().get("native_value")
        if native_value is None:
            return
        options = self.entity_description.options
        if options is not None and native_value not in options:
            _LOGGER.debug(
                "%s: Discarded stored value %s for %s, it is no longer offered",
                self.device.address,
                native_value,
                self.entity_description.key,
            )
            return
        self._attr_native_value = native_value
        _LOGGER.debug(
            "%s: Restored value for %s",
            self.device.address,
            self.entity_description.key,
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Invoke the mapping getter or read the datapoint."""
        if self._mapping.getter is not None:
            self._mapping.getter(self)
        else:
            datapoint = self.device.datapoints[self._mapping.dp_id]
            if datapoint:
                self._update_from_datapoint(datapoint)
        self.async_write_ha_state()

    @callback
    def _update_from_datapoint(self, datapoint: TuyaBLEDataPoint) -> None:
        """Update attributes from a datapoint based on its type."""
        if datapoint.dp_type == TuyaBLEDataPointType.DT_ENUM:
            self._update_enum_value(datapoint)
        elif datapoint.dp_type == TuyaBLEDataPointType.DT_VALUE:
            if isinstance(datapoint.value, int):
                self._attr_native_value = datapoint.value / self._mapping.coefficient
        else:
            if isinstance(datapoint.value, (int, float, str)):
                self._attr_native_value = datapoint.value

    @callback
    def _update_enum_value(self, datapoint: TuyaBLEDataPoint) -> None:
        """Update attributes from an enum datapoint."""
        options = self.entity_description.options
        if options is None:
            self._attr_native_value = str(datapoint.value)
        elif isinstance(datapoint.value, int) and 0 <= datapoint.value < len(options):
            self._attr_native_value = options[datapoint.value]
        else:
            # A code outside the declared options would make the entity state
            # invalid, so report the value as unknown instead.
            self._attr_native_value = None
            _LOGGER.debug(
                "%s: Enum code %s for %s is outside the declared options",
                self.device.address,
                datapoint.value,
                self.entity_description.key,
            )
        if (
            self._mapping.icons is not None
            and isinstance(datapoint.value, int)
            and 0 <= datapoint.value < len(self._mapping.icons)
        ):
            self._attr_icon = self._mapping.icons[datapoint.value]

    @property
    def available(self) -> bool:
        """True when coordinator is connected and the availability predicate passes."""
        result = super().available
        if result and self._mapping.is_available is not None:
            result = self._mapping.is_available(self, self._product)
        return result

    def set_native_value(self, value: str | float | int | None) -> None:
        """Set the native value of the sensor."""
        self._attr_native_value = value


async def async_setup_entry(  # noqa: S7503
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Tuya BLE sensors."""
    data: TuyaBLEData = hass.data[DOMAIN][entry.entry_id]
    mappings = get_mapping_by_device(data.device)
    entities: list[TuyaBLESensor] = [
        TuyaBLESensor(
            hass,
            data.coordinator,
            data.device,
            data.product,
            rssi_mapping,
        )
    ]
    for sensor_mapping in mappings:
        if sensor_mapping.force_add or data.device.datapoints.has_id(
            sensor_mapping.dp_id, sensor_mapping.dp_type
        ):
            entities.append(
                TuyaBLESensor(
                    hass,
                    data.coordinator,
                    data.device,
                    data.product,
                    sensor_mapping,
                )
            )
    async_add_entities(entities)
