"""Battery enum sensor handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...sensor import TuyaBLESensor

# Hardcoded datapoint carrying the battery enum value (1..5).
_BATTERY_ENUM_DP_ID = 104


def battery_enum(sensor: TuyaBLESensor) -> None:
    """Read the battery enum datapoint and convert it to a percentage."""
    datapoint = sensor.device.datapoints[_BATTERY_ENUM_DP_ID]
    if datapoint and isinstance(datapoint.value, int):
        sensor.set_native_value(datapoint.value * 20.0)
