"""Sensor platform for Tuya BLE devices (temperature, humidity, battery, RSSI)."""

# pylint: disable=too-many-lines

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

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
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
)
from .device_descriptors.handlers import rssi as rssi_handler
from .device_registry import (
    EntityDescriptor,
    get_registry,
)
from .devices import TuyaBLECoordinator, TuyaBLEData, TuyaBLEEntity, TuyaBLEProductInfo
from .tuya_ble import TuyaBLEDataPoint, TuyaBLEDataPointType, TuyaBLEDevice

SIGNAL_STRENGTH_DP_ID = -1

ICON_BATTERY = "mdi:battery"
ICON_BATTERY_CHECK = "mdi:battery-check"
ICON_COUNTER = "mdi:counter"
ICON_FINGERPRINT = "mdi:fingerprint"


TuyaBLESensorIsAvailable = Callable[["TuyaBLESensor", TuyaBLEProductInfo], bool] | None


@dataclass
class TuyaBLESensorMapping:
    """Map a Tuya datapoint to a Home Assistant sensor entity."""

    dp_id: int
    description: SensorEntityDescription
    force_add: bool = True
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


_DESCRIPTION_ATTRS = (
    ("icon", "icon"),
    ("device_class", "device_class"),
    ("unit", "native_unit_of_measurement"),
    ("state_class", "state_class"),
    ("entity_category", "entity_category"),
    ("options", "options"),
    ("suggested_display_precision", "suggested_display_precision"),
)


def _sensor_description(desc: EntityDescriptor) -> SensorEntityDescription:
    """Build a SensorEntityDescription from a descriptor."""
    kwargs: dict[str, object] = {
        "key": desc.translation_key or str(desc.dp_id),
    }
    for attr, key in _DESCRIPTION_ATTRS:
        value = getattr(desc, attr)
        if value is not None:
            kwargs[key] = value
    if desc.enabled_by_default is False:
        kwargs["entity_registry_enabled_default"] = False
    return SensorEntityDescription(**kwargs)  # type: ignore[arg-type]


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


class TuyaBLESensor(TuyaBLEEntity, SensorEntity):
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
        if self.entity_description.options is not None:
            if isinstance(datapoint.value, int) and 0 <= datapoint.value < len(
                self.entity_description.options
            ):
                self._attr_native_value = self.entity_description.options[
                    datapoint.value
                ]
            else:
                self._attr_native_value = str(datapoint.value)
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

    def set_native_value(self, value: float | int | None) -> None:
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
