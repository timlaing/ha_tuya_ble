"""Fingerbot mode availability handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...entity import TuyaBLEEntity
    from ...products import TuyaBLEProductInfo


def in_program_mode(entity: TuyaBLEEntity, product: TuyaBLEProductInfo) -> bool:
    """Return True if the fingerbot is in program mode."""
    result: bool = True
    if product.fingerbot:
        datapoint = entity.device.datapoints[product.fingerbot.mode]
        if datapoint:
            result = datapoint.value == 2
    return result


def not_in_program_mode(entity: TuyaBLEEntity, product: TuyaBLEProductInfo) -> bool:
    """Return True if the fingerbot is not in program mode."""
    result: bool = True
    if product.fingerbot:
        datapoint = entity.device.datapoints[product.fingerbot.mode]
        if datapoint:
            result = datapoint.value != 2
    return result


def in_switch_mode(entity: TuyaBLEEntity, product: TuyaBLEProductInfo) -> bool:
    """Return True if the fingerbot is in switch mode."""
    result: bool = True
    if product.fingerbot:
        datapoint = entity.device.datapoints[product.fingerbot.mode]
        if datapoint:
            result = datapoint.value == 1
    return result


def in_push_mode(entity: TuyaBLEEntity, product: TuyaBLEProductInfo) -> bool:
    """Return True if the fingerbot is in push mode."""
    result: bool = True
    if product.fingerbot:
        datapoint = entity.device.datapoints[product.fingerbot.mode]
        if datapoint:
            result = datapoint.value == 0
    return result


def repeat_count_available(entity: TuyaBLEEntity, product: TuyaBLEProductInfo) -> bool:
    """Return whether the fingerbot program repeat count is available."""
    result: bool = True
    if product.fingerbot and product.fingerbot.program:
        datapoint = entity.device.datapoints[product.fingerbot.mode]
        if datapoint:
            result = datapoint.value == 2
        if result:
            datapoint = entity.device.datapoints[product.fingerbot.program]
            if datapoint and isinstance(datapoint.value, bytes):
                repeat_count = int.from_bytes(datapoint.value[0:2], "big")
                result = repeat_count != 0xFFFF

    return result
