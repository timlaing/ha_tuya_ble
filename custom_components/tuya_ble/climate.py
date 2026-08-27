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


mapping: dict[str, TuyaBLECategoryClimateMapping] = {
    "wk": TuyaBLECategoryClimateMapping(
        products={
            k: [
                # Thermostatic Radiator Valve
                # - [x] 8   - Window
                # - [x] 10  - Antifreeze
                # - [x] 27  - Calibration
                # - [x] 40  - Lock
                # - [x] 101 - Switch
                # - [x] 102 - Current
                # - [x] 103 - Target
                # - [ ] 104 - Heating time
                # - [x] 105 - Battery power alarm
                # - [x] 106 - Away
                # - [x] 107 - Programming mode
                # - [x] 108 - Programming switch
                # - [ ] 109 - Programming data (deprecated - do not delete)
                # - [ ] 110 - Historical data protocol (Day-Target temperature)
                # - [ ] 111 - System Time Synchronization
                # - [ ] 112 - Historical data (Week-Target temperature)
                # - [ ] 113 - Historical data (Month-Target temperature)
                # - [ ] 114 - Historical data (Year-Target temperature)
                # - [ ] 115 - Historical data (Day-Current temperature)
                # - [ ] 116 - Historical data (Week-Current temperature)
                # - [ ] 117 - Historical data (Month-Current temperature)
                # - [ ] 118 - Historical data (Year-Current temperature)
                # - [ ] 119 - Historical data (Day-motor opening degree)
                # - [ ] 120 - Historical data (Week-motor opening degree)
                # - [ ] 121 - Historical data (Month-motor opening degree)
                # - [ ] 122 - Historical data (Year-motor opening degree)
                # - [ ] 123 - Programming data (Monday)
                # - [ ] 124 - Programming data (Tuesday)
                # - [ ] 125 - Programming data (Wednesday)
                # - [ ] 126 - Programming data (Thursday)
                # - [ ] 127 - Programming data (Friday)
                # - [ ] 128 - Programming data (Saturday)
                # - [ ] 129 - Programming data (Sunday)
                # - [x] 130 - Water scale
                TuyaBLEClimateMapping(
                    description=ClimateEntityDescription(
                        key="thermostatic_radiator_valve",
                    ),
                    hvac_switch_dp_id=101,
                    hvac_switch_mode=HVACMode.HEAT,
                    hvac_modes=[HVACMode.OFF, HVACMode.HEAT],
                    preset_mode_dp_ids={PRESET_AWAY: 106, PRESET_NONE: 106},
                    current_temperature_dp_id=102,
                    current_temperature_coefficient=10.0,
                    target_temperature_coefficient=10.0,
                    target_temperature_step=0.5,
                    target_temperature_dp_id=103,
                    target_temperature_min=5.0,
                    target_temperature_max=30.0,
                ),
            ]
            for k in ["drlajpqc", "nhj2j7su"]
        },
    ),
}


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

        if all(values[0] == elem for elem in values) and keys[0] == PRESET_AWAY:
            # TRVs with only Away and None modes can be set with a single
            # datapoint and share a single DP ID
            bool_value = preset_mode == PRESET_AWAY
            datapoint: TuyaBLEDataPoint | None
            datapoint = self.device.datapoints.get_or_create(
                values[0],
                TuyaBLEDataPointType.DT_BOOL,
                bool_value,
            )
        else:
            bool_value = False
            datapoint = None
            for dp_preset_mode, dp_id in self._mapping.preset_mode_dp_ids.items():
                bool_value = dp_preset_mode == preset_mode
                datapoint = self.device.datapoints.get_or_create(
                    dp_id,
                    TuyaBLEDataPointType.DT_BOOL,
                    bool_value,
                )
        self.hass.create_task(
            datapoint.set_value(bool_value),  # type: ignore[union-attr]
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
