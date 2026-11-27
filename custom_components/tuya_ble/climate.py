"""Climate platform for Tuya BLE thermostats and heaters."""

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
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
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
    hvac_mode_dp_type: TuyaBLEDataPointType = TuyaBLEDataPointType.DT_VALUE
    # Explicit wire values avoid assigning a normal HVAC mode to full-open.
    hvac_mode_values: list[int] | None = None
    hvac_action_dp_id: int = 0

    hvac_switch_dp_id: int = 0
    hvac_switch_mode: HVACMode | None = None
    # Report hvac_action from the switch alone (off, or the action matching
    # hvac_switch_mode while on), for devices without a room temperature.
    hvac_action_from_switch: bool = False

    preset_mode_dp_ids: dict[str, int] | None = None
    preset_enum_dp_id: int = 0
    preset_enum_values: dict[str, int] | None = None

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


# The action a switch-driven climate reports while its switch is on. Only
# these modes have an unambiguous running action; the registry rejects any
# other ``hvac_switch_mode`` combined with ``hvac_action_from_switch``.
SWITCH_MODE_ACTIONS: dict[HVACMode, HVACAction] = {
    HVACMode.HEAT: HVACAction.HEATING,
    HVACMode.COOL: HVACAction.COOLING,
    HVACMode.DRY: HVACAction.DRYING,
    HVACMode.FAN_ONLY: HVACAction.FAN,
}


@dataclass
class TuyaBLECategoryClimateMapping:
    """Container for product-specific and default climate mappings."""

    products: dict[str, list[TuyaBLEClimateMapping]] | None = None
    mapping: list[TuyaBLEClimateMapping] | None = None


def _climate_description(desc: EntityDescriptor) -> ClimateEntityDescription:
    """Build a ClimateEntityDescription from a registry descriptor."""
    return ClimateEntityDescription(
        key=desc.translation_key or "climate",
        name=desc.name,
        icon=desc.icon,
    )


def _build_climate_mapping(desc: EntityDescriptor) -> TuyaBLEClimateMapping:
    """Construct a climate mapping from a registry descriptor."""
    extra = desc.extra
    kwargs: dict[str, Any] = {"description": _climate_description(desc)}
    for field_name in (
        "hvac_mode_dp_id",
        "hvac_mode_values",
        "hvac_action_dp_id",
        "preset_enum_dp_id",
        "preset_enum_values",
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
    if "hvac_mode_dp_type" in extra:
        kwargs["hvac_mode_dp_type"] = TuyaBLEDataPointType(extra["hvac_mode_dp_type"])
    if "hvac_switch_mode" in extra:
        kwargs["hvac_switch_mode"] = HVACMode(extra["hvac_switch_mode"])
    if "preset_mode_dp_ids" in extra:
        kwargs["preset_mode_dp_ids"] = dict(extra["preset_mode_dp_ids"])
    if "temperature_unit" in extra:
        kwargs["temperature_unit"] = extra["temperature_unit"]
    if "hvac_action_from_switch" in extra:
        kwargs["hvac_action_from_switch"] = extra["hvac_action_from_switch"]
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
        if climate_mapping.hvac_action_dp_id:
            self._attr_hvac_mode = None
            self._attr_hvac_action = None

        if climate_mapping.hvac_mode_dp_id and climate_mapping.hvac_modes:
            self._attr_hvac_modes = [HVACMode(m) for m in climate_mapping.hvac_modes]
        elif climate_mapping.hvac_switch_dp_id and climate_mapping.hvac_switch_mode:
            self._attr_hvac_modes = [HVACMode.OFF, climate_mapping.hvac_switch_mode]

        if climate_mapping.preset_mode_dp_ids:
            self._attr_supported_features |= ClimateEntityFeature.PRESET_MODE
            self._attr_preset_modes = list(climate_mapping.preset_mode_dp_ids)
            # Explicit DP -> preset map, so a grouped bool DP means the same
            # thing regardless of YAML key order; PRESET_NONE is the fallback.
            self._preset_by_dp_id = {
                dp_id: preset_mode
                for preset_mode, dp_id in climate_mapping.preset_mode_dp_ids.items()
                if preset_mode != PRESET_NONE
            }
        else:
            self._preset_by_dp_id = {}
        if climate_mapping.preset_enum_values:
            self._attr_supported_features |= ClimateEntityFeature.PRESET_MODE
            self._attr_preset_modes = list(climate_mapping.preset_enum_values)
            self._attr_preset_mode = None

        if (
            climate_mapping.target_temperature_dp_id != 0
            or climate_mapping.current_temperature_dp_id != 0
        ):
            self._attr_native_temperature_unit = climate_mapping.temperature_unit

        if climate_mapping.target_temperature_dp_id != 0:
            self._attr_supported_features |= ClimateEntityFeature.TARGET_TEMPERATURE
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
        if self._mapping.hvac_action_dp_id:
            self._attr_hvac_action = self._reported_hvac_action()
        elif self._mapping.hvac_action_from_switch:
            self._attr_hvac_action = self._switch_hvac_action()
        else:
            self._attr_hvac_action = self._thermostat_hvac_action()
        self.async_write_ha_state()

    @callback
    def _reported_hvac_action(self) -> HVACAction | None:
        """Read a closed/opened work-state enum without guessing from setpoints."""
        datapoint = self.device.datapoints[self._mapping.hvac_action_dp_id]
        if self._attr_hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        if datapoint is None or datapoint.value not in (0, 1):
            return None
        return HVACAction.HEATING if datapoint.value == 1 else HVACAction.IDLE

    @callback
    def _thermostat_hvac_action(self) -> HVACAction:
        """Return the action inferred from preset, mode and temperatures."""
        if (
            self._attr_preset_mode == PRESET_AWAY
            or self._attr_hvac_mode == HVACMode.OFF
            or (
                self._attr_target_temperature is not None
                and self._attr_current_temperature is not None
                and self._attr_target_temperature <= self._attr_current_temperature
            )
        ):
            return HVACAction.IDLE
        return HVACAction.HEATING

    @callback
    def _switch_hvac_action(self) -> HVACAction | None:
        """Return the action reported by the on/off switch alone.

        Unknown until the switch has been reported, so a device that has not
        sent its state yet is not shown as running.
        """
        switch_mode = self._mapping.hvac_switch_mode
        if self._mapping.hvac_switch_dp_id == 0 or switch_mode is None:
            return None
        datapoint = self.device.datapoints[self._mapping.hvac_switch_dp_id]
        if datapoint is None:
            return None
        if not datapoint.value:
            return HVACAction.OFF
        return SWITCH_MODE_ACTIONS.get(switch_mode)

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
        if self._mapping.hvac_mode_values:
            if datapoint.value not in self._mapping.hvac_mode_values:
                return None
            return HVACMode(
                hvac_modes[self._mapping.hvac_mode_values.index(datapoint.value)]
            )
        if not 0 <= datapoint.value < len(hvac_modes):
            return None
        return HVACMode(hvac_modes[datapoint.value])

    @callback
    def _read_preset_mode(self) -> None:
        """Update the current preset mode from the device datapoints."""
        if self._mapping.preset_enum_values:
            datapoint = self.device.datapoints[self._mapping.preset_enum_dp_id]
            self._attr_preset_mode = next(
                (
                    preset
                    for preset, value in self._mapping.preset_enum_values.items()
                    if datapoint is not None and datapoint.value == value
                ),
                None,
            )
            return
        if not self._preset_by_dp_id:
            return
        current_preset_mode = PRESET_NONE
        for dp_id, preset_mode in self._preset_by_dp_id.items():
            datapoint = self.device.datapoints[dp_id]
            if datapoint and datapoint.value:
                current_preset_mode = preset_mode
                break
        self._attr_preset_mode = current_preset_mode

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if self._mapping.target_temperature_dp_id != 0 and temperature is not None:
            int_value = int(temperature * self._mapping.target_temperature_coefficient)
            datapoint = self.device.datapoints.get_or_create(
                self._mapping.target_temperature_dp_id,
                TuyaBLEDataPointType.DT_VALUE,
                int_value,
            )
            await datapoint.set_value(int_value)

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
            if self._mapping.hvac_mode_values:
                int_value = self._mapping.hvac_mode_values[int_value]
            datapoint = self.device.datapoints.get_or_create(
                self._mapping.hvac_mode_dp_id,
                self._mapping.hvac_mode_dp_type,
                int_value,
            )
            await datapoint.set_value(int_value)
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
        if self._mapping.preset_enum_values:
            if preset_mode not in self._mapping.preset_enum_values:
                raise ValueError(f"Unsupported preset: {preset_mode}")
            enum_value = self._mapping.preset_enum_values[preset_mode]
            enum_datapoint = self.device.datapoints.get_or_create(
                self._mapping.preset_enum_dp_id,
                TuyaBLEDataPointType.DT_ENUM,
                enum_value,
            )
            await enum_datapoint.set_value(enum_value)
            return
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
