"""Water valve switch setter handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...tuya_ble import TuyaBLEDataPointType

if TYPE_CHECKING:
    from ...entity import TuyaBLEEntity
    from ...products import TuyaBLEProductInfo
    from ...switch import TuyaBLESwitch


def is_water_valve_in_switch_mode(
    entity: TuyaBLEEntity,  # noqa: S1172
    product: TuyaBLEProductInfo,
) -> bool:
    """Return True if the product is a water valve."""
    return product.watervalve is not None


def set_16wgjvck_water_valve(
    switch: TuyaBLESwitch,
    product: TuyaBLEProductInfo,  # noqa: S1172
    value: bool,
) -> None:
    """Set the Aldi/Ferrex Smart Water Valve state."""
    if value:
        dp_11_val = 60
        dp15 = switch.device.datapoints[15]
        dp11 = switch.device.datapoints[11]
        if dp15 and dp15.value:
            dp_11_val = int(dp15.value)
        elif dp11 and dp11.value:
            dp_11_val = int(dp11.value)
        if dp_11_val <= 0:
            dp_11_val = 60

        dp_2_val = 100
        dp2 = switch.device.datapoints[2]
        if dp2 and dp2.value is not None:
            dp_2_val = int(dp2.value)
        if dp_2_val <= 0:
            dp_2_val = 100

        switch.send_multiple_dp_values([
            (1, TuyaBLEDataPointType.DT_BOOL, True),
            (2, TuyaBLEDataPointType.DT_VALUE, dp_2_val),
            (11, TuyaBLEDataPointType.DT_VALUE, dp_11_val),
        ])
    else:
        switch.send_dp_value(1, TuyaBLEDataPointType.DT_BOOL, False)
