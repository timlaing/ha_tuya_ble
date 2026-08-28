"""Shared Fingerbot helper functions for Tuya BLE entities."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .entity import TuyaBLEEntity
    from .products import TuyaBLEProductInfo


def is_fingerbot_in_program_mode(
    entity: TuyaBLEEntity, product: TuyaBLEProductInfo
) -> bool:
    """Return True if the fingerbot is in program mode."""
    result: bool = True
    if product.fingerbot:
        datapoint = entity.device.datapoints[product.fingerbot.mode]
        if datapoint:
            result = datapoint.value == 2
    return result


def is_fingerbot_not_in_program_mode(
    entity: TuyaBLEEntity, product: TuyaBLEProductInfo
) -> bool:
    """Return True if the fingerbot is not in program mode."""
    result: bool = True
    if product.fingerbot:
        datapoint = entity.device.datapoints[product.fingerbot.mode]
        if datapoint:
            result = datapoint.value != 2
    return result


def is_fingerbot_in_switch_mode(
    entity: TuyaBLEEntity, product: TuyaBLEProductInfo
) -> bool:
    """Return True if the fingerbot is in switch mode."""
    result: bool = True
    if product.fingerbot:
        datapoint = entity.device.datapoints[product.fingerbot.mode]
        if datapoint:
            result = datapoint.value == 1
    return result


def is_fingerbot_in_push_mode(
    entity: TuyaBLEEntity, product: TuyaBLEProductInfo
) -> bool:
    """Return True if the fingerbot is in push mode."""
    result: bool = True
    if product.fingerbot:
        datapoint = entity.device.datapoints[product.fingerbot.mode]
        if datapoint:
            result = datapoint.value == 0
    return result


def is_fingerbot_repeat_count_available(
    entity: TuyaBLEEntity, product: TuyaBLEProductInfo
) -> bool:
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


def get_fingerbot_program_repeat_count(
    entity: TuyaBLEEntity, product: TuyaBLEProductInfo
) -> float | None:
    """Get the repeat count from the fingerbot program data point."""
    result: float | None = None
    if product.fingerbot and product.fingerbot.program:
        datapoint = entity.device.datapoints[product.fingerbot.program]
        if datapoint and isinstance(datapoint.value, bytes):
            repeat_count = int.from_bytes(datapoint.value[0:2], "big")
            result = repeat_count * 1.0

    return result


def set_fingerbot_program_repeat_count(
    entity: TuyaBLEEntity, product: TuyaBLEProductInfo, value: float
) -> None:
    """Set the repeat count in the fingerbot program data point."""
    if product.fingerbot and product.fingerbot.program:
        datapoint = entity.device.datapoints[product.fingerbot.program]
        if datapoint and isinstance(datapoint.value, bytes):
            new_value = int.to_bytes(int(value), 2, "big") + datapoint.value[2:]
            entity.hass.create_task(datapoint.set_value(new_value))


def get_fingerbot_program_repeat_forever(
    entity: TuyaBLEEntity, product: TuyaBLEProductInfo
) -> bool | None:
    """Return whether the fingerbot program repeats forever."""
    result: bool | None = None
    if product.fingerbot and product.fingerbot.program:
        datapoint = entity.device.datapoints[product.fingerbot.program]
        if datapoint and isinstance(datapoint.value, bytes):
            repeat_count = int.from_bytes(datapoint.value[0:2], "big")
            result = repeat_count == 0xFFFF
    return result


def set_fingerbot_program_repeat_forever(
    entity: TuyaBLEEntity, product: TuyaBLEProductInfo, value: bool
) -> None:
    """Set whether the fingerbot program repeats forever."""
    if product.fingerbot and product.fingerbot.program:
        datapoint = entity.device.datapoints[product.fingerbot.program]
        if datapoint and isinstance(datapoint.value, bytes):
            new_value = (
                int.to_bytes(0xFFFF if value else 1, 2, "big") + datapoint.value[2:]
            )
            entity.hass.create_task(datapoint.set_value(new_value))


def get_fingerbot_program_position(
    entity: TuyaBLEEntity, product: TuyaBLEProductInfo
) -> float | None:
    """Get the position from the fingerbot program data point."""
    result: float | None = None
    if product.fingerbot and product.fingerbot.program:
        datapoint = entity.device.datapoints[product.fingerbot.program]
        if datapoint and isinstance(datapoint.value, bytes):
            result = datapoint.value[2] * 1.0

    return result


def set_fingerbot_program_position(
    entity: TuyaBLEEntity, product: TuyaBLEProductInfo, value: float
) -> None:
    """Set the position in the fingerbot program data point."""
    if product.fingerbot and product.fingerbot.program:
        datapoint = entity.device.datapoints[product.fingerbot.program]
        if datapoint and isinstance(datapoint.value, bytes):
            new_value = (
                datapoint.value[0:2]
                + int.to_bytes(int(value), 1, "big")
                + datapoint.value[3:]
            )
            entity.hass.create_task(datapoint.set_value(new_value))
