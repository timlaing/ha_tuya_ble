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


async def write_mode(
    device: TuyaBLEDevice,
    mapping: TuyaBLELightMapping,
    on: bool,
    attributes: dict[str, Any],
) -> None:
    """Write mode and optional brightness together; never replay failed writes.

    The update dict is built inside the device's rollback lock so it always
    reflects the confirmed state at send time. This avoids a race where a
    concurrent Off write could cause a subsequent On+brightness to omit the
    mode DP because it observed the optimistic Off value.
    """
    descriptor = mapping.descriptor
    assert descriptor is not None
    extra = descriptor.extra

    def build_updates() -> dict[int, bytes | bool | int | str]:
        """Build the update dict based on the current confirmed state.

        Uses `reported_value` (the last device-confirmed value) to avoid
        observing optimistic values from pending writes. A missing
        `reported_value` means the device hasn't reported this DP yet.
        """
        updates: dict[int, bytes | bool | int | str] = {}
        datapoint = device.datapoints[extra["mode_dp_id"]]
        # Check the confirmed state, not the optimistic value.
        is_on = (
            datapoint is not None
            and datapoint.reported_value is not None
            and datapoint.reported_value == extra["on_value"]
        )
        if not on or not is_on:
            updates[extra["mode_dp_id"]] = (
                extra["on_value"] if on else extra["off_value"]
            )
        if on and "brightness" in attributes:
            updates[mapping.color_mode_dp_id] = extra["white_value"]
            updates[mapping.brightness_dp_id] = _brightness_value(
                mapping, attributes["brightness"]
            )
        return updates

    # Validate types against the *current* datapoints (which may be None if
    # never created). Create any missing datapoints with no optimistic value
    # (value=None) so their reported_value stays None until the device reports.
    update_dict = build_updates()
    for dp_id in update_dict:
        dp_type = (
            TuyaBLEDataPointType.DT_VALUE
            if dp_id == mapping.brightness_dp_id
            else TuyaBLEDataPointType.DT_ENUM
        )
        datapoint = device.datapoints[dp_id]
        if datapoint is not None and datapoint.dp_type != dp_type:
            raise ServiceValidationError("Light datapoint has an unexpected wire type")
        if datapoint is None:
            device.datapoints.get_or_create(dp_id, dp_type, value=None)

    # Pass a callable so the final update dict is evaluated inside the
    # rollback transaction, seeing the confirmed state.
    await device.set_multiple_values(build_updates)


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
