"""Fingerbot program getter/setter handlers."""

from __future__ import annotations

from struct import pack, unpack
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ....entity import TuyaBLEEntity, TuyaBLEProductInfo
    from ....tuya_ble import TuyaBLEDataPoint


# repeat count (2 bytes) + idle position (1 byte) + step count (1 byte).
_HEADER_LENGTH = 4
_STEP_LENGTH = 3
# Accessors for the repeat count and idle position only read the first 3 bytes.
_FIELDS_LENGTH = _HEADER_LENGTH - 1
# The step count occupies a single byte, so the payload can hold no more.
MAX_STEPS = 255


def _program(
    entity: TuyaBLEEntity,
    product: TuyaBLEProductInfo,
    min_length: int = _FIELDS_LENGTH,
) -> tuple[TuyaBLEDataPoint, bytes] | None:
    """Return the program data point and its payload, if it holds one right now.

    The program is one byte payload of repeat count, idle position and steps, so
    every accessor and mutator needs it to actually be bytes. Returns None when
    the product has no program entity, when the device has not reported one
    yet, or when the payload is too short for the fields the caller reads.
    Indexing past the end otherwise raises from a native_value property, which
    HA only surfaces as a state error on every update.
    """
    dp_id = product.fingerbot_program_dp_id
    if dp_id is None:
        return None
    datapoint = entity.device.datapoints[dp_id]
    if datapoint is None or not isinstance(datapoint.value, bytes):
        return None
    if len(datapoint.value) < min_length:
        return None
    return datapoint, datapoint.value


def _step_count(payload: bytes) -> int:
    """Return how many steps the payload actually carries.

    The step count is device-declared and unverified, so it is clamped to the
    bytes present rather than trusted: a device reporting more steps than it
    sent must not produce IndexError or struct.error.
    """
    declared = payload[3]
    available = (len(payload) - _HEADER_LENGTH) // _STEP_LENGTH
    return min(declared, available)


def get_repeat_count(
    entity: TuyaBLEEntity, product: TuyaBLEProductInfo
) -> float | None:
    """Get the repeat count from the fingerbot program data point."""
    program = _program(entity, product)
    if program is None:
        return None
    _, payload = program
    return int.from_bytes(payload[0:2], "big") * 1.0


def set_repeat_count(
    entity: TuyaBLEEntity, product: TuyaBLEProductInfo, value: float
) -> None:
    """Set the repeat count in the fingerbot program data point."""
    program = _program(entity, product)
    if program is None:
        return
    datapoint, payload = program
    new_value = int.to_bytes(int(value), 2, "big") + payload[2:]
    entity.hass.create_task(datapoint.set_value(new_value))


def get_repeat_forever(
    entity: TuyaBLEEntity, product: TuyaBLEProductInfo
) -> bool | None:
    """Return whether the fingerbot program repeats forever."""
    program = _program(entity, product)
    if program is None:
        return None
    _, payload = program
    return int.from_bytes(payload[0:2], "big") == 0xFFFF


def set_repeat_forever(
    entity: TuyaBLEEntity, product: TuyaBLEProductInfo, value: bool
) -> None:
    """Set whether the fingerbot program repeats forever."""
    program = _program(entity, product)
    if program is None:
        return
    datapoint, payload = program
    new_value = int.to_bytes(0xFFFF if value else 1, 2, "big") + payload[2:]
    entity.hass.create_task(datapoint.set_value(new_value))


def get_position(entity: TuyaBLEEntity, product: TuyaBLEProductInfo) -> float | None:
    """Get the position from the fingerbot program data point."""
    program = _program(entity, product)
    if program is None:
        return None
    _, payload = program
    return payload[2] * 1.0


def set_position(
    entity: TuyaBLEEntity, product: TuyaBLEProductInfo, value: float
) -> None:
    """Set the position in the fingerbot program data point."""
    program = _program(entity, product)
    if program is None:
        return
    datapoint, payload = program
    new_value = payload[0:2] + int.to_bytes(int(value), 1, "big") + payload[3:]
    entity.hass.create_task(datapoint.set_value(new_value))


def _format_program_step(program_bytes: bytes, step: int) -> str:
    """Format a single program step into its string representation."""
    step_pos = _HEADER_LENGTH + step * _STEP_LENGTH
    step_data = program_bytes[step_pos : step_pos + _STEP_LENGTH]
    position, delay = unpack(">BH", step_data)
    delay = min(delay, 9999)
    return (
        (";" if step > 0 else "")
        + str(position)
        + (("/" + str(delay)) if delay > 0 else "")
    )


def get_program(entity: TuyaBLEEntity, product: TuyaBLEProductInfo) -> str | None:
    """Get the fingerbot program as a formatted string."""
    program = _program(entity, product, _HEADER_LENGTH)
    if program is None:
        return None
    _, payload = program
    steps = ""
    for step in range(_step_count(payload)):
        steps += _format_program_step(payload, step)
    return steps


def set_program(entity: TuyaBLEEntity, product: TuyaBLEProductInfo, value: str) -> None:
    """Set the fingerbot program from a formatted string."""
    program = _program(entity, product)
    if program is None:
        return
    datapoint, payload = program
    new_value = bytearray(payload[0 : _HEADER_LENGTH - 1])
    steps = [step for step in value.split(";") if step][:MAX_STEPS]
    new_value += int.to_bytes(len(steps), 1, "big")
    for step in steps:
        step_values = step.split("/")
        position = int(step_values[0])
        delay = int(step_values[1]) if len(step_values) > 1 else 0
        new_value += pack(">BH", min(position, 255), min(delay, 9999))
    entity.hass.create_task(datapoint.set_value(bytes(new_value)))
