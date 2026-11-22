"""Shared thermostat state and conditional control helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...devices import TuyaBLEProductInfo
    from ...number import TuyaBLENumber
    from ...sensor import TuyaBLESensor


def in_eco_mode(entity: TuyaBLENumber, product: TuyaBLEProductInfo) -> bool:
    """Enable deviation control only after the eco preset has been reported."""
    if product.entities is None:
        return False
    for descriptor in product.entities.get("climate"):
        dp_id = descriptor.extra.get("preset_enum_dp_id")
        eco_value = descriptor.extra.get("preset_enum_values", {}).get("eco")
        if dp_id is None or eco_value is None:
            continue
        datapoint = entity.device.datapoints[dp_id]
        return datapoint is not None and datapoint.reported_value == eco_value
    return False


def child_lock_state(entity: TuyaBLESensor) -> None:
    """Translate a reported Boolean lock state to diagnostic enum text."""
    datapoint = entity.device.datapoints[entity.dp_id]
    if datapoint is None or not isinstance(datapoint.reported_value, bool):
        entity.set_native_value(None)
        return
    entity.set_native_value("enabled" if datapoint.reported_value else "disabled")
