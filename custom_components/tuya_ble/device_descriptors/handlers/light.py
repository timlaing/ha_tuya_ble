"""Shared mode-controlled lights with atomic white-mode brightness writes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import ServiceValidationError

from ...tuya_ble import TuyaBLEDataPointType, TuyaBLEDevice
from ...util import remap_value

if TYPE_CHECKING:
    from ...light import TuyaBLELightMapping


def mode_is_on(device: TuyaBLEDevice, mapping: TuyaBLELightMapping) -> bool:
    """Read the descriptor's explicit on-mode value, not its power switch."""
    descriptor = mapping.descriptor
    assert descriptor is not None
    datapoint = device.datapoints[descriptor.extra["mode_dp_id"]]
    return datapoint is not None and datapoint.value == descriptor.extra["on_value"]


async def write_mode(
    device: TuyaBLEDevice,
    mapping: TuyaBLELightMapping,
    on: bool,
    attributes: dict[str, Any],
) -> None:
    """Write mode and optional brightness together; never replay failed writes."""
    descriptor = mapping.descriptor
    assert descriptor is not None
    extra = descriptor.extra
    updates: dict[int, bytes | bool | int | str] = {}
    if not on or not mode_is_on(device, mapping):
        updates[extra["mode_dp_id"]] = extra["on_value"] if on else extra["off_value"]
    if on and "brightness" in attributes:
        updates[mapping.color_mode_dp_id] = extra["white_value"]
        updates[mapping.brightness_dp_id] = _brightness_value(
            mapping, attributes["brightness"]
        )
    types = {}
    for dp_id in updates:
        dp_type = (
            TuyaBLEDataPointType.DT_VALUE
            if dp_id == mapping.brightness_dp_id
            else TuyaBLEDataPointType.DT_ENUM
        )
        datapoint = device.datapoints[dp_id]
        if datapoint is not None and datapoint.dp_type != dp_type:
            raise ServiceValidationError("Light datapoint has an unexpected wire type")
        types[dp_id] = dp_type
    initial_values = {
        extra["mode_dp_id"]: extra["off_value"],
        mapping.color_mode_dp_id: extra["white_value"],
        mapping.brightness_dp_id: mapping.brightness_min,
    }
    for dp_id, dp_type in types.items():
        # A new DP starts at a neutral value, not an unconfirmed command.
        device.datapoints.get_or_create(dp_id, dp_type, initial_values[dp_id])
    if updates:
        await device.set_multiple_values(updates)


def _brightness_value(mapping: TuyaBLELightMapping, brightness: Any) -> int:
    """Validate HA brightness before mapping it to the descriptor's range."""
    if (
        not isinstance(brightness, int)
        or isinstance(brightness, bool)
        or not 0 <= brightness <= 255
    ):
        raise ServiceValidationError("Brightness must be an integer from 0 to 255")
    return round(
        remap_value(brightness, 0, 255, mapping.brightness_min, mapping.brightness_max)
    )
