"""Battery enum sensor handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...sensor import TuyaBLESensor


def _clamp(value: int, low: int, high: int) -> int:
    """Clamp *value* into the inclusive [low, high] range."""
    return max(low, min(value, high))


def battery_enum(sensor: TuyaBLESensor) -> None:
    """Read the battery enum datapoint and convert it to a percentage."""
    datapoint = sensor.device.datapoints[sensor.dp_id]
    if (
        datapoint
        and isinstance(datapoint.value, int)
        and not isinstance(datapoint.value, bool)
    ):
        sensor.set_native_value(_clamp(datapoint.value, 1, 5) * 20.0)
