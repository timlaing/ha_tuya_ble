"""Measurement sensor handlers gated by an on/off data point and throttled.

Configured from the sensor descriptor:

- ``active_dp_id``: a boolean data point; while it reports off the value is
  unknown, and a reading the device sent before switching off is not shown
  again after it switches back on.
- ``report_min_change`` / ``report_max_interval``: a new reading is shown only
  once it differs from the shown value by at least ``report_min_change`` or
  ``report_max_interval`` seconds have passed since the last shown reading.
  Both default to 0, which shows every reading.
"""

from __future__ import annotations

from time import monotonic
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...sensor import TuyaBLESensor

_STALE_REVISION = "stale_report_revision"
_LAST_SHOWN = "last_shown"


def _is_switched_off(sensor: TuyaBLESensor) -> bool:
    """Return True when the gating data point has reported off."""
    active_dp_id = sensor.handler_options.get("active_dp_id")
    if active_dp_id is None:
        return False
    datapoint = sensor.device.datapoints[active_dp_id]
    return datapoint is not None and not datapoint.value


def _should_show(sensor: TuyaBLESensor, value: float, now: float) -> bool:
    """Return True when *value* passes the change/interval throttle."""
    previous = sensor.native_value
    last_shown = sensor.handler_state.get(_LAST_SHOWN)
    if (
        not isinstance(previous, int | float)
        or isinstance(previous, bool)
        or last_shown is None
    ):
        return True
    options = sensor.handler_options
    min_change = float(options.get("report_min_change", 0))
    max_interval = float(options.get("report_max_interval", 0))
    return abs(value - previous) >= min_change or now - last_shown >= max_interval


def active_throttled(sensor: TuyaBLESensor) -> None:
    """Show a scaled numeric reading while active, throttling frequent reports."""
    state = sensor.handler_state
    datapoint = sensor.device.datapoints[sensor.dp_id]
    if _is_switched_off(sensor):
        sensor.set_native_value(None)
        state[_LAST_SHOWN] = None
        state[_STALE_REVISION] = (
            datapoint.report_revision if datapoint is not None else 0
        )
        return
    if (
        datapoint is None
        or not isinstance(datapoint.value, int)
        or isinstance(datapoint.value, bool)
    ):
        return
    stale_revision = state.get(_STALE_REVISION)
    if stale_revision is not None and datapoint.report_revision <= stale_revision:
        return
    value = datapoint.value / sensor.coefficient
    now = monotonic()
    if _should_show(sensor, value, now):
        sensor.set_native_value(value)
        state[_LAST_SHOWN] = now
