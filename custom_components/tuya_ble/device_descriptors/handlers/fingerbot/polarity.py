"""Fingerbot switch handlers for actuators that report inverted polarity."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ....tuya_ble import TuyaBLEDataPointType

if TYPE_CHECKING:
    from ....entity import TuyaBLEEntity, TuyaBLEProductInfo


def get_inverted_switch(
    entity: TuyaBLEEntity, product: TuyaBLEProductInfo
) -> bool | None:
    """Return the switch state with the reported boolean inverted.

    Returns None until the device has reported the switch, so a missing report
    is not shown as on.
    """
    dp_id = product.fingerbot_switch_dp_id
    if dp_id is None:
        return None
    datapoint = entity.device.datapoints[dp_id]
    if datapoint is None:
        return None
    return not bool(datapoint.value)


def set_inverted_switch(
    entity: TuyaBLEEntity, product: TuyaBLEProductInfo, value: bool
) -> None:
    """Write the requested switch state with inverted polarity."""
    entity.send_dp_value(
        product.fingerbot_switch_dp_id, TuyaBLEDataPointType.DT_BOOL, not value
    )
