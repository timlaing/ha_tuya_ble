"""Light platform for Tuya BLE RGB and color-temperature lights."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.light import (  # type: ignore[attr-defined]
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_HS_COLOR,
    ColorMode,
    LightEntity,
    LightEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import color as color_util

from .const import DOMAIN
from .devices import (
    TuyaBLECoordinator,
    TuyaBLEData,
    TuyaBLEEntity,
    TuyaBLEProductInfo,
)
from .tuya_ble import TuyaBLEDataPointType, TuyaBLEDevice


def _remap_value(
    value: float | int,
    from_min: float = 0,
    from_max: float = 255,
    to_min: float = 0,
    to_max: float = 255,
    reverse: bool = False,
) -> float:
    """Remap a value from one range to another."""
    if reverse:
        value = from_max - value + from_min
    return ((value - from_min) / (from_max - from_min)) * (to_max - to_min) + to_min


@dataclass
class TuyaBLELightMapping:
    """Mapping of Tuya BLE data points to a Home Assistant light entity."""

    description: LightEntityDescription
    switch_dp_id: int = 0
    brightness_dp_id: int = 0
    color_temp_dp_id: int = 0
    color_data_dp_id: int = 0
    color_mode_dp_id: int = 0
    brightness_min: int = 0
    brightness_max: int = 255
    color_temp_min: int = 0
    color_temp_max: int = 100


@dataclass
class TuyaBLECategoryLightMapping:
    """Container for product-specific and default light mappings."""

    products: dict[str, list[TuyaBLELightMapping]] | None = None
    mapping: list[TuyaBLELightMapping] | None = None


mapping: dict[str, TuyaBLECategoryLightMapping] = {
    "dd": TuyaBLECategoryLightMapping(
        products={
            "nvfrtxlq": [
                TuyaBLELightMapping(
                    description=LightEntityDescription(
                        key="switch_led",
                        name=None,
                    ),
                    switch_dp_id=1,
                    color_mode_dp_id=2,
                    brightness_dp_id=3,
                    color_temp_dp_id=4,
                    color_data_dp_id=5,
                    brightness_min=0,
                    brightness_max=1000,
                ),
            ],
        },
        mapping=[
            TuyaBLELightMapping(
                description=LightEntityDescription(
                    key="switch_led",
                    name=None,
                ),
                switch_dp_id=1,
                color_mode_dp_id=2,
                brightness_dp_id=3,
                color_temp_dp_id=4,
                color_data_dp_id=5,
                brightness_min=0,
                brightness_max=1000,
            ),
        ],
    ),
}


def get_mapping_by_device(device: TuyaBLEDevice) -> list[TuyaBLELightMapping]:
    """Get the light mappings for a device."""
    category = mapping.get(device.category)
    if category is not None and category.products is not None:
        product_mapping = category.products.get(device.product_id)
        if product_mapping is not None:
            return product_mapping
        if category.mapping is not None:
            return category.mapping
        return []
    return []


class TuyaBLELight(TuyaBLEEntity, LightEntity):
    """Representation of a Tuya BLE Light."""

    entity_description: LightEntityDescription

    _attr_min_mireds = 153
    _attr_max_mireds = 370

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: TuyaBLECoordinator,
        device: TuyaBLEDevice,
        product: TuyaBLEProductInfo,
        light_mapping: TuyaBLELightMapping,
    ) -> None:
        super().__init__(hass, coordinator, device, product, light_mapping.description)
        self._mapping = light_mapping
        self._attr_supported_color_modes: set[ColorMode] = set()

        if self._mapping.color_data_dp_id != 0:
            self._attr_supported_color_modes.add(ColorMode.HS)
        if self._mapping.color_temp_dp_id != 0:
            self._attr_supported_color_modes.add(ColorMode.COLOR_TEMP)
        if self._mapping.brightness_dp_id != 0 and not self._attr_supported_color_modes:
            self._attr_supported_color_modes.add(ColorMode.BRIGHTNESS)
        if not self._attr_supported_color_modes:
            self._attr_supported_color_modes = {ColorMode.ONOFF}

    @callback
    def _handle_coordinator_update(self) -> None:
        """Write HA state — all properties read datapoints on demand."""
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        """Return true if light is on."""
        if self._mapping.switch_dp_id != 0:
            datapoint = self.device.datapoints[self._mapping.switch_dp_id]
            if datapoint is not None:
                return bool(datapoint.value)
        return False

    @property
    def brightness(self) -> int | None:
        """Return the brightness of the light (0-255)."""
        if self.color_mode == ColorMode.HS:
            color_data = self._get_color_data()
            if color_data is not None:
                _h, _s, v = color_data
                return round(_remap_value(v, 0, 1000, 0, 255))

        if self._mapping.brightness_dp_id == 0:
            return None
        datapoint = self.device.datapoints[self._mapping.brightness_dp_id]
        if datapoint is None:
            return None
        return round(
            _remap_value(
                int(datapoint.value),
                self._mapping.brightness_min,
                self._mapping.brightness_max,
                0,
                255,
            )
        )

    @property
    def color_temp_kelvin(self) -> int | None:
        """Return the color temperature in Kelvin."""
        if self._mapping.color_temp_dp_id == 0:
            return None
        datapoint = self.device.datapoints[self._mapping.color_temp_dp_id]
        if datapoint is None:
            return None
        mireds = _remap_value(
            int(datapoint.value),
            self._mapping.color_temp_min,
            self._mapping.color_temp_max,
            self._attr_min_mireds,
            self._attr_max_mireds,
            reverse=True,
        )
        return color_util.color_temperature_mired_to_kelvin(round(mireds))

    @property
    def hs_color(self) -> tuple[float, float] | None:
        """Return the HS color."""
        if self.color_mode != ColorMode.HS:
            return None
        color_data = self._get_color_data()
        if color_data is None:
            return None
        h, s, _v = color_data
        return (
            _remap_value(h, 0, 360, 0, 360),
            _remap_value(s, 0, 1000, 0, 100),
        )

    @property
    def color_mode(self) -> ColorMode:
        """Return the current color mode."""
        if self._mapping.color_mode_dp_id != 0 and self._mapping.color_data_dp_id != 0:
            datapoint = self.device.datapoints[self._mapping.color_mode_dp_id]
            if datapoint is not None and str(datapoint.value).lower() != "white":
                return ColorMode.HS
        if self._mapping.color_temp_dp_id != 0:
            return ColorMode.COLOR_TEMP
        if self._mapping.brightness_dp_id != 0:
            return ColorMode.BRIGHTNESS
        return ColorMode.ONOFF

    def turn_on(self, **kwargs: Any) -> None:
        """Turn on the light."""
        commands: list[dict[str, Any]] = []

        if self._mapping.switch_dp_id != 0:
            commands.append({
                "dp_id": self._mapping.switch_dp_id,
                "dp_type": TuyaBLEDataPointType.DT_BOOL,
                "value": True,
            })

        if ATTR_COLOR_TEMP_KELVIN in kwargs and self._mapping.color_temp_dp_id != 0:
            if self._mapping.color_mode_dp_id != 0:
                commands.append({
                    "dp_id": self._mapping.color_mode_dp_id,
                    "dp_type": TuyaBLEDataPointType.DT_ENUM,
                    "value": "white",
                })
            mireds = color_util.color_temperature_kelvin_to_mired(
                kwargs[ATTR_COLOR_TEMP_KELVIN]
            )
            value = round(
                _remap_value(
                    mireds,
                    self._attr_min_mireds,
                    self._attr_max_mireds,
                    self._mapping.color_temp_min,
                    self._mapping.color_temp_max,
                    reverse=True,
                )
            )
            commands.append({
                "dp_id": self._mapping.color_temp_dp_id,
                "dp_type": TuyaBLEDataPointType.DT_VALUE,
                "value": value,
            })

        if ATTR_HS_COLOR in kwargs and self._mapping.color_data_dp_id != 0:
            if self._mapping.color_mode_dp_id != 0:
                commands.append({
                    "dp_id": self._mapping.color_mode_dp_id,
                    "dp_type": TuyaBLEDataPointType.DT_ENUM,
                    "value": "colour",
                })
            hs_color = kwargs[ATTR_HS_COLOR]
            brightness = kwargs.get(ATTR_BRIGHTNESS, self.brightness or 0)
            h = round(_remap_value(hs_color[0], 0, 360, 0, 360))
            s = round(_remap_value(hs_color[1], 0, 100, 0, 1000))
            v = round(_remap_value(brightness, 0, 255, 0, 1000))
            colorstr = f"{h:04x}{s:04x}{v:04x}"
            commands.append({
                "dp_id": self._mapping.color_data_dp_id,
                "dp_type": TuyaBLEDataPointType.DT_STRING,
                "value": colorstr,
            })
        elif ATTR_BRIGHTNESS in kwargs and self._mapping.brightness_dp_id != 0:
            value = round(
                _remap_value(
                    kwargs[ATTR_BRIGHTNESS],
                    0,
                    255,
                    self._mapping.brightness_min,
                    self._mapping.brightness_max,
                )
            )
            commands.append({
                "dp_id": self._mapping.brightness_dp_id,
                "dp_type": TuyaBLEDataPointType.DT_VALUE,
                "value": value,
            })

        self._send_command(commands)

    def turn_off(self, **kwargs: Any) -> None:
        """Turn off the light."""
        if self._mapping.switch_dp_id != 0:
            self._send_command([
                {
                    "dp_id": self._mapping.switch_dp_id,
                    "dp_type": TuyaBLEDataPointType.DT_BOOL,
                    "value": False,
                }
            ])

    def _get_color_data(self) -> tuple[int, int, int] | None:
        """Parse the color data DP value into (h, s, v)."""
        if self._mapping.color_data_dp_id == 0:
            return None
        datapoint = self.device.datapoints[self._mapping.color_data_dp_id]
        if datapoint is None or not datapoint.value:
            return None
        status_data = str(datapoint.value)
        if len(status_data) == 12:
            h = int(status_data[:4], 16)
            s = int(status_data[4:8], 16)
            v = int(status_data[8:], 16)
            return (h, s, v)
        return None


async def async_setup_entry(  # noqa: S7503
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Tuya BLE lights."""
    data: TuyaBLEData = hass.data[DOMAIN][entry.entry_id]
    mappings = get_mapping_by_device(data.device)
    entities: list[TuyaBLELight] = []
    for light_mapping in mappings:
        entities.append(
            TuyaBLELight(
                hass,
                data.coordinator,
                data.device,
                data.product,
                light_mapping,
            )
        )
    async_add_entities(entities)
