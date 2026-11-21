"""Shared mode-controlled lights with atomic white-mode brightness writes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import ServiceValidationError

from ...tuya_ble import TuyaBLEDataPointType, TuyaBLEDevice
from ...util import remap_value

if TYPE_CHECKING:
    from ...light import TuyaBLELightMapping


def mode_is_on(device: TuyaBLEDevice, mapping: TuyaBLELightMapping) -> bool:
    """Read the descriptor's explicit on-mode value, not its power switch.

    Only consider the mode DP as "on" when its wire type is ``DT_ENUM`` and
    its value matches the descriptor's ``on_value``. A ``DT_BOOL`` value of
    ``True`` is explicitly rejected (it would compare equal to ``1`` and
    incorrectly report the light as on).
    """
    descriptor = mapping.descriptor
    assert descriptor is not None
    datapoint = device.datapoints[descriptor.extra["mode_dp_id"]]
    return (
        datapoint is not None
        and datapoint.dp_type is TuyaBLEDataPointType.DT_ENUM
        and datapoint.value == descriptor.extra["on_value"]
    )


async def _atomic_check_and_write(
    device: TuyaBLEDevice,
    mapping: TuyaBLELightMapping,
    on: bool,
    attributes: dict[str, Any],
) -> None:
    """Atomically check mode state and write updates under the device lock.

    This serialises the no-op decision with the batch write so that a
    concurrent request cannot observe an optimistic value and return success
    without waiting for the pending write's outcome.
    """
    descriptor = mapping.descriptor
    assert descriptor is not None
    extra = descriptor.extra
    updates: dict[int, bytes | bool | int | str] = {}

    # Serially acquire the per-device write lock. Any concurrent write will
    # complete (success or rollback) before we proceed, so we see the
    # confirmed state.
    # pylint: disable=protected-access
    async with device._datapoints._write_lock:
        datapoint = device.datapoints[extra["mode_dp_id"]]
        if not on or datapoint is None or datapoint.value != extra["on_value"]:
            updates[extra["mode_dp_id"]] = (
                extra["on_value"] if on else extra["off_value"]
            )
        if on and "brightness" in attributes:
            updates[mapping.color_mode_dp_id] = extra["white_value"]
            updates[mapping.brightness_dp_id] = _brightness_value(
                mapping, attributes["brightness"]
            )

    if not updates:
        return

    types = {}
    for dp_id in updates:
        dp_type = (
            TuyaBLEDataPointType.DT_VALUE
            if dp_id == mapping.brightness_dp_id
            else TuyaBLEDataPointType.DT_ENUM
        )
        datapoint = device.datapoints[dp_id]
        if datapoint is not None and datapoint.dp_type != dp_type:
            raise ServiceValidationError("Light datapoint has an unexpected wire type")
        types[dp_id] = dp_type
    initial_values = {
        extra["mode_dp_id"]: extra["off_value"],
        mapping.color_mode_dp_id: extra["white_value"],
        mapping.brightness_dp_id: mapping.brightness_min,
    }
    for dp_id, dp_type in types.items():
        device.datapoints.get_or_create(dp_id, dp_type, initial_values[dp_id])
    await device.set_multiple_values(updates)


async def write_mode(
    device: TuyaBLEDevice,
    mapping: TuyaBLELightMapping,
    on: bool,
    attributes: dict[str, Any],
) -> None:
    """Write mode and optional brightness together; never replay failed writes."""
    await _atomic_check_and_write(device, mapping, on, attributes)


def _brightness_value(mapping: TuyaBLELightMapping, brightness: Any) -> int:
    """Validate HA brightness before mapping it to the descriptor's range."""
    if (
        not isinstance(brightness, int)
        or isinstance(brightness, bool)
        or not 0 <= brightness <= 255
    ):
        raise ServiceValidationError("Brightness must be an integer from 0 to 255")
    return round(
        remap_value(brightness, 0, 255, mapping.brightness_min, mapping.brightness_max)
    )
