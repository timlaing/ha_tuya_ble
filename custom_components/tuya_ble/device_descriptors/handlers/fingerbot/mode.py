"""Fingerbot mode availability handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .program import get_repeat_forever

if TYPE_CHECKING:
    from ....entity import TuyaBLEEntity, TuyaBLEProductInfo


def _mode(entity: TuyaBLEEntity, product: TuyaBLEProductInfo) -> int | None:
    """Return the fingerbot's current mode, or None when it is not reported.

    A None result means "do not restrict on mode": either this is not a
    fingerbot, or the device has not reported its mode yet, and in both cases
    the entity stays available.
    """
    dp_id = product.fingerbot_mode_dp_id
    if dp_id is None:
        return None
    datapoint = entity.device.datapoints[dp_id]
    if datapoint is None or not isinstance(datapoint.value, int):
        return None
    return datapoint.value


def in_program_mode(entity: TuyaBLEEntity, product: TuyaBLEProductInfo) -> bool:
    """Return True if the fingerbot is in program mode."""
    mode = _mode(entity, product)
    return mode is None or mode == 2


def not_in_program_mode(entity: TuyaBLEEntity, product: TuyaBLEProductInfo) -> bool:
    """Return True if the fingerbot is not in program mode."""
    mode = _mode(entity, product)
    return mode is None or mode != 2


def in_switch_mode(entity: TuyaBLEEntity, product: TuyaBLEProductInfo) -> bool:
    """Return True if the fingerbot is in switch mode."""
    mode = _mode(entity, product)
    return mode is None or mode == 1


def in_push_mode(entity: TuyaBLEEntity, product: TuyaBLEProductInfo) -> bool:
    """Return True if the fingerbot is in push mode."""
    mode = _mode(entity, product)
    return mode is None or mode == 0


def repeat_count_available(entity: TuyaBLEEntity, product: TuyaBLEProductInfo) -> bool:
    """Return whether the fingerbot program repeat count is available.

    A count only means anything while a program is running, and the device
    reports 0xFFFF as "repeating forever" instead of a count -- so the count is
    available exactly when the fingerbot is not repeating forever.
    """
    mode = _mode(entity, product)
    if mode is not None and mode != 2:
        return False
    return not get_repeat_forever(entity, product)
