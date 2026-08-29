"""Climate platform for Tuya BLE thermostatic radiator valves."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityDescription,
)
from homeassistant.components.climate.const import (
    PRESET_AWAY,
    PRESET_NONE,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .device_registry import EntityDescriptor, get_registry
from .devices import TuyaBLECoordinator, TuyaBLEData, TuyaBLEEntity, TuyaBLEProductInfo
from .tuya_ble import TuyaBLEDataPoint, TuyaBLEDataPointType, TuyaBLEDevice


@dataclass
class TuyaBLEClimateMapping:
    """Mapping of Tuya BLE data points to a Home Assistant climate entity."""

    description: ClimateEntityDescription

    hvac_mode_dp_id: int = 0
    hvac_modes: list[str] | None = None

    hvac_switch_dp_id: int = 0
    hvac_switch_mode: HVACMode | None = None

    preset_mode_dp_ids: dict[str, int] | None = None

    temperature_unit: str = UnitOfTemperature.CELSIUS
    current_temperature_dp_id: int = 0
    current_temperature_coefficient: float = 1.0
    target_temperature_dp_id: int = 0
    target_temperature_coefficient: float = 1.0
    target_temperature_max: float = 30.0
    target_temperature_min: float = 5
    target_temperature_step: float = 1.0

    current_humidity_dp_id: int = 0
    current_humidity_coefficient: float = 1.0
    target_humidity_dp_id: int = 0
    target_humidity_coefficient: float = 1.0
    target_humidity_max: float = 100.0
    target_humidity_min: float = 0.0


@dataclass
class TuyaBLECategoryClimateMapping:
    """Container for product-specific and default climate mappings."""

    products: dict[str, list[TuyaBLEClimateMapping]] | None = None
    mapping: list[TuyaBLEClimateMapping] | None = None


def _climate_description(desc: EntityDescriptor) -> ClimateEntityDescription:
    """Build a ClimateEntityDescription from a registry descriptor."""
    return ClimateEntityDescription(
        key=desc.translation_key or "climate",
        icon=desc.icon,
    )


def _build_climate_mapping(desc: EntityDescriptor) -> TuyaBLEClimateMapping:
    """Construct a climate mapping from a registry descriptor."""
    extra = desc.extra
    kwargs: dict[str, Any] = {"description": _climate_description(desc)}
    for field_name in (
        "hvac_mode_dp_id",
        "hvac_switch_dp_id",
        "current_temperature_dp_id",
        "current_temperature_coefficient",
        "target_temperature_dp_id",
        "target_temperature_coefficient",
        "target_temperature_max",
        "target_temperature_min",
        "target_temperature_step",
        "current_humidity_dp_id",
        "current_humidity_coefficient",
        "target_humidity_dp_id",
        "target_humidity_coefficient",
        "target_humidity_max",
        "target_humidity_min",
    ):
        if field_name in extra:
            kwargs[field_name] = extra[field_name]
    if "hvac_modes" in extra:
        kwargs["hvac_modes"] = [HVACMode(m) for m in extra["hvac_modes"]]
    if "hvac_switch_mode" in extra:
        kwargs["hvac_switch_mode"] = HVACMode(extra["hvac_switch_mode"])
    if "preset_mode_dp_ids" in extra:
        kwargs["preset_mode_dp_ids"] = dict(extra["preset_mode_dp_ids"])
    if "temperature_unit" in extra:
        kwargs["temperature_unit"] = extra["temperature_unit"]
    return TuyaBLEClimateMapping(**kwargs)


def _build_mapping() -> dict[str, TuyaBLECategoryClimateMapping]:
    """Build the climate mappings dict from the device registry."""
    result: dict[str, TuyaBLECategoryClimateMapping] = {}
    for device_entities in get_registry().products.values():
        descriptors = device_entities.get("climate")
        if not descriptors:
            continue
        category_mapping = result.setdefault(
            device_entities.category,
            TuyaBLECategoryClimateMapping(products={}),
        )
        assert category_mapping.products is not None
        category_mapping.products[device_entities.product_id] = [
            _build_climate_mapping(desc) for desc in descriptors
        ]
    return result


mapping: dict[str, TuyaBLECategoryClimateMapping] = _build_mapping()


def get_mapping_by_device(device: TuyaBLEDevice) -> list[TuyaBLEClimateMapping]:
    """Get the climate mappings for a device."""
    category = mapping.get(device.category)
    if category is not None and category.products is not None:
        product_mapping = category.products.get(device.product_id)
        if product_mapping is not None:
            return product_mapping
        if category.mapping is not None:
            return category.mapping
        return []
    return []


class TuyaBLEClimate(TuyaBLEEntity, ClimateEntity):
    """Representation of a Tuya BLE Climate."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: TuyaBLECoordinator,
        device: TuyaBLEDevice,
        product: TuyaBLEProductInfo,
        climate_mapping: TuyaBLEClimateMapping,
    ) -> None:
        super().__init__(
            hass, coordinator, device, product, climate_mapping.description
        )
        self._mapping = climate_mapping
        self._attr_hvac_mode = HVACMode.HEAT
        self._attr_preset_mode = PRESET_NONE
        self._attr_hvac_action = HVACAction.HEATING

        if climate_mapping.hvac_mode_dp_id and climate_mapping.hvac_modes:
            self._attr_hvac_modes = [HVACMode(m) for m in climate_mapping.hvac_modes]
        elif climate_mapping.hvac_switch_dp_id and climate_mapping.hvac_switch_mode:
            self._attr_hvac_modes = [HVACMode.OFF, climate_mapping.hvac_switch_mode]

        if climate_mapping.preset_mode_dp_ids:
            self._attr_supported_features |= ClimateEntityFeature.PRESET_MODE
            self._attr_preset_modes = list(climate_mapping.preset_mode_dp_ids)

        if climate_mapping.target_temperature_dp_id != 0:
            self._attr_supported_features |= ClimateEntityFeature.TARGET_TEMPERATURE
            self._attr_temperature_unit = climate_mapping.temperature_unit
            self._attr_max_temp = climate_mapping.target_temperature_max
            self._attr_min_temp = climate_mapping.target_temperature_min
            self._attr_target_temperature_step = climate_mapping.target_temperature_step

        if climate_mapping.target_humidity_dp_id != 0:
            self._attr_supported_features |= ClimateEntityFeature.TARGET_HUMIDITY
            self._attr_max_humidity = climate_mapping.target_humidity_max
            self._attr_min_humidity = climate_mapping.target_humidity_min

    @callback
    def _read_scaled_value(self, dp_id: int, coefficient: float) -> float | None:
        """Read a numeric datapoint and convert it to its scaled value."""
        if dp_id == 0:
            return None
        datapoint = self.device.datapoints[dp_id]
        if not datapoint or not isinstance(datapoint.value, int):
            return None
        return datapoint.value / coefficient

    @callback
    def _read_scaled_attr(self, attr: str, dp_id: int, coefficient: float) -> None:
        """Read a scaled datapoint and assign it to the named attribute."""
        value = self._read_scaled_value(dp_id, coefficient)
        if value is not None:
            setattr(self, attr, value)

    @callback
    def _read_scaled_attrs(self) -> None:
        """Update temperature and humidity attributes from the device."""
        for attr, dp_id, coefficient in (
            (
                "_attr_current_temperature",
                self._mapping.current_temperature_dp_id,
                self._mapping.current_temperature_coefficient,
            ),
            (
                "_attr_target_temperature",
                self._mapping.target_temperature_dp_id,
                self._mapping.target_temperature_coefficient,
            ),
            (
                "_attr_current_humidity",
                self._mapping.current_humidity_dp_id,
                self._mapping.current_humidity_coefficient,
            ),
            (
                "_attr_target_humidity",
                self._mapping.target_humidity_dp_id,
                self._mapping.target_humidity_coefficient,
            ),
        ):
            self._read_scaled_attr(attr, dp_id, coefficient)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Re-read datapoints, determine HVAC and preset modes."""

        self._read_scaled_attrs()
        self._read_hvac_mode()
        self._read_preset_mode()

        if (
            self._attr_preset_mode == PRESET_AWAY
            or self._attr_hvac_mode == HVACMode.OFF
            or (
                self._attr_target_temperature is not None
                and self._attr_current_temperature is not None
                and self._attr_target_temperature <= self._attr_current_temperature
            )
        ):
            self._attr_hvac_action = HVACAction.IDLE
        else:
            self._attr_hvac_action = HVACAction.HEATING

        self.async_write_ha_state()

    @callback
    def _read_hvac_mode(self) -> None:
        """Update the current hvac mode from the device datapoints."""
        if self._mapping.hvac_mode_dp_id != 0 and self._mapping.hvac_modes:
            datapoint = self.device.datapoints[self._mapping.hvac_mode_dp_id]
            self._attr_hvac_mode = self._mode_from_value(datapoint)
            return
        if self._mapping.hvac_switch_dp_id != 0 and self._mapping.hvac_switch_mode:
            datapoint = self.device.datapoints[self._mapping.hvac_switch_dp_id]
            if datapoint:
                self._attr_hvac_mode = (
                    self._mapping.hvac_switch_mode if datapoint.value else HVACMode.OFF
                )

    @callback
    def _mode_from_value(self, datapoint: TuyaBLEDataPoint | None) -> HVACMode | None:
        """Return the hvac mode for a mode datapoint value."""
        hvac_modes = self._mapping.hvac_modes
        if not hvac_modes or not datapoint or not isinstance(datapoint.value, int):
            return None
        if datapoint.value >= len(hvac_modes):
            return None
        return HVACMode(hvac_modes[datapoint.value])

    @callback
    def _read_preset_mode(self) -> None:
        """Update the current preset mode from the device datapoints."""
        if not self._mapping.preset_mode_dp_ids:
            return
        current_preset_mode = PRESET_NONE
        for preset_mode, dp_id in self._mapping.preset_mode_dp_ids.items():
            datapoint = self.device.datapoints[dp_id]
            if datapoint and datapoint.value:
                current_preset_mode = preset_mode
                break
        self._attr_preset_mode = current_preset_mode

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature."""
        if self._mapping.target_temperature_dp_id != 0:
            int_value = int(
                kwargs["temperature"] * self._mapping.target_temperature_coefficient
            )
            datapoint = self.device.datapoints.get_or_create(
                self._mapping.target_temperature_dp_id,
                TuyaBLEDataPointType.DT_VALUE,
                int_value,
            )
            self.hass.create_task(datapoint.set_value(int_value))

    async def async_set_humidity(self, humidity: int) -> None:
        """Set new target humidity."""
        if self._mapping.target_humidity_dp_id != 0:
            int_value = int(humidity * self._mapping.target_humidity_coefficient)
            datapoint = self.device.datapoints.get_or_create(
                self._mapping.target_humidity_dp_id,
                TuyaBLEDataPointType.DT_VALUE,
                int_value,
            )
            self.hass.create_task(datapoint.set_value(int_value))

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target hvac mode."""
        if (
            self._mapping.hvac_mode_dp_id != 0
            and self._mapping.hvac_modes
            and hvac_mode in self._mapping.hvac_modes
        ):
            int_value = self._mapping.hvac_modes.index(hvac_mode)
            datapoint = self.device.datapoints.get_or_create(
                self._mapping.target_humidity_dp_id,
                TuyaBLEDataPointType.DT_VALUE,
                int_value,
            )
            self.hass.create_task(datapoint.set_value(int_value))
        elif self._mapping.hvac_switch_dp_id != 0 and self._mapping.hvac_switch_mode:
            bool_value = hvac_mode == self._mapping.hvac_switch_mode
            datapoint = self.device.datapoints.get_or_create(
                self._mapping.hvac_switch_dp_id,
                TuyaBLEDataPointType.DT_BOOL,
                bool_value,
            )
            self.hass.create_task(datapoint.set_value(bool_value))

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set new preset mode."""
        if not self._mapping.preset_mode_dp_ids:
            return

        keys = list(self._mapping.preset_mode_dp_ids)
        values = list(self._mapping.preset_mode_dp_ids.values())
        datapoint: TuyaBLEDataPoint

        if all(values[0] == elem for elem in values) and keys[0] == PRESET_AWAY:
            # TRVs with only Away and None modes can be set with a single
            # datapoint and share a single DP ID
            bool_value = preset_mode == PRESET_AWAY
            datapoint = self.device.datapoints.get_or_create(
                values[0],
                TuyaBLEDataPointType.DT_BOOL,
                bool_value,
            )
        else:
            bool_value = False
            for dp_preset_mode, dp_id in self._mapping.preset_mode_dp_ids.items():
                bool_value = dp_preset_mode == preset_mode
                datapoint = self.device.datapoints.get_or_create(
                    dp_id,
                    TuyaBLEDataPointType.DT_BOOL,
                    bool_value,
                )

        self.hass.create_task(
            datapoint.set_value(bool_value),
        )

    def set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature. Handled by async_set_temperature."""

    def set_humidity(self, humidity: int) -> None:
        """Set new target humidity. Handled by async_set_humidity."""

    def set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new hvac mode. Handled by async_set_hvac_mode."""

    def set_preset_mode(self, preset_mode: str) -> None:
        """Set new preset mode. Handled by async_set_preset_mode."""

    def set_fan_mode(self, fan_mode: str) -> None:
        """Set new fan mode. Not supported by this device."""

    def set_swing_mode(self, swing_mode: str) -> None:
        """Set new swing mode. Not supported by this device."""

    def set_swing_horizontal_mode(self, swing_horizontal_mode: str) -> None:
        """Set new horizontal swing mode. Not supported by this device."""

    def turn_on(self) -> None:
        """Turn the entity on. Not supported by this device."""

    def turn_off(self) -> None:
        """Turn the entity off. Not supported by this device."""

    def toggle(self) -> None:
        """Toggle the entity. Not supported by this device."""


async def async_setup_entry(  # noqa: S7503
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tuya BLE climate entities for the config entry."""
    data: TuyaBLEData = hass.data[DOMAIN][entry.entry_id]
    mappings = get_mapping_by_device(data.device)
    entities: list[TuyaBLEClimate] = []
    for climate_mapping in mappings:
        entities.append(
            TuyaBLEClimate(
                hass,
                data.coordinator,
                data.device,
                data.product,
                climate_mapping,
            )
        )
    async_add_entities(entities)
