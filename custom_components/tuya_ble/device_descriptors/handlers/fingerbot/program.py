"""Fingerbot program getter/setter handlers."""

from __future__ import annotations

from struct import pack, unpack
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ....entity import TuyaBLEEntity
    from ....products import TuyaBLEProductInfo


def get_repeat_count(
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


def set_repeat_count(
    entity: TuyaBLEEntity, product: TuyaBLEProductInfo, value: float
) -> None:
    """Set the repeat count in the fingerbot program data point."""
    if product.fingerbot and product.fingerbot.program:
        datapoint = entity.device.datapoints[product.fingerbot.program]
        if datapoint and isinstance(datapoint.value, bytes):
            new_value = int.to_bytes(int(value), 2, "big") + datapoint.value[2:]
            entity.hass.create_task(datapoint.set_value(new_value))


def get_repeat_forever(
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


def set_repeat_forever(
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


def get_position(entity: TuyaBLEEntity, product: TuyaBLEProductInfo) -> float | None:
    """Get the position from the fingerbot program data point."""
    result: float | None = None
    if product.fingerbot and product.fingerbot.program:
        datapoint = entity.device.datapoints[product.fingerbot.program]
        if datapoint and isinstance(datapoint.value, bytes):
            result = datapoint.value[2] * 1.0

    return result


def set_position(
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


def _format_program_step(program_bytes: bytes, step: int) -> str:
    """Format a single program step into its string representation."""
    step_pos = 4 + step * 3
    step_data = program_bytes[step_pos : step_pos + 3]
    position, delay = unpack(">BH", step_data)
    delay = min(delay, 9999)
    return (
        (";" if step > 0 else "")
        + str(position)
        + (("/" + str(delay)) if delay > 0 else "")
    )


def get_program(entity: TuyaBLEEntity, product: TuyaBLEProductInfo) -> str | None:
    """Get the fingerbot program as a formatted string."""
    result: str | None = None
    if product.fingerbot and product.fingerbot.program:
        datapoint = entity.device.datapoints[product.fingerbot.program]
        if datapoint and isinstance(datapoint.value, bytes):
            result = ""
            step_count: int = datapoint.value[3]
            for step in range(step_count):
                result += _format_program_step(datapoint.value, step)
    return result


def set_program(entity: TuyaBLEEntity, product: TuyaBLEProductInfo, value: str) -> None:
    """Set the fingerbot program from a formatted string."""
    if product.fingerbot and product.fingerbot.program:
        datapoint = entity.device.datapoints[product.fingerbot.program]
        if datapoint and isinstance(datapoint.value, bytes):
            new_value = bytearray(datapoint.value[0:3])
            steps = value.split(";")
            new_value += int.to_bytes(len(steps), 1, "big")
            for step in steps:
                step_values = step.split("/")
                position = int(step_values[0])
                delay = int(step_values[1]) if len(step_values) > 1 else 0
                new_value += pack(">BH", position, delay)
            entity.hass.create_task(datapoint.set_value(bytes(new_value)))
