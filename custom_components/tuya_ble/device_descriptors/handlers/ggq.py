"""Diivoo (ggq) irrigation device handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...sensor import TuyaBLESensor

# Zone operation codes of the dual water timer, captured from the device while
# driving its valves: the zone reports 0 while the valve is open and 2 once the
# valve is closed. Any other code is reported as received so a firmware change
# stays visible instead of being hidden behind a wrong label.
WORK_STATES: dict[int, str] = {
    0: "watering",
    2: "idle",
}


def work_state(sensor: TuyaBLESensor) -> None:
    """Report the zone operation status as a label, falling back to its code."""
    datapoint = sensor.device.datapoints[sensor.dp_id]
    if datapoint is None:
        return
    value = datapoint.value
    if isinstance(value, int):
        sensor.set_native_value(WORK_STATES.get(value, str(value)))
    else:
        sensor.set_native_value(str(value))
