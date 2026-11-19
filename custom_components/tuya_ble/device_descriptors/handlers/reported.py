"""Strict read-only report handlers for experimental schema-only subsets."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...tuya_ble import TuyaBLEDataPointType

if TYPE_CHECKING:
    from ...binary_sensor import TuyaBLEBinarySensor
    from ...sensor import TuyaBLESensor


def numeric(sensor: TuyaBLESensor) -> None:
    """Read only the declared wire type; never assign guessed enum labels."""
    datapoint = sensor.device.datapoints[sensor.dp_id]
    expected = TuyaBLEDataPointType(sensor.handler_options["report_type"])
    value = datapoint.reported_value if datapoint is not None else None
    if (
        datapoint is None
        or datapoint.dp_type != expected
        or not isinstance(value, int)
        or isinstance(value, bool)
    ):
        sensor.set_native_value(None)
        return
    sensor.set_native_value(value / sensor.coefficient)


def boolean(entity: TuyaBLEBinarySensor) -> None:
    """Read only an actual BOOL report, keeping invalid reports unknown."""
    datapoint = entity.device.datapoints[entity.dp_id]
    value = datapoint.reported_value if datapoint is not None else None
    entity.set_is_on(
        value
        if datapoint is not None
        and datapoint.dp_type == TuyaBLEDataPointType.DT_BOOL
        and isinstance(value, bool)
        else None
    )
