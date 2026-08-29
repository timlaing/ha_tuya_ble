"""CO2 sensor availability handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...products import TuyaBLEProductInfo
    from ...sensor import TuyaBLESensor

# Hardcoded datapoint used by the CO2 alarm availability check.
_CO2_ALARM_DP_ID = 13


def alarm_enabled(sensor: TuyaBLESensor, product: TuyaBLEProductInfo) -> bool:
    """Return whether the CO2 alarm is enabled, reading datapoint 13."""
    result: bool = True
    datapoint = sensor.device.datapoints[_CO2_ALARM_DP_ID]
    if datapoint:
        result = bool(datapoint.value)
    return result
