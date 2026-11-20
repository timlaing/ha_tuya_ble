"""Lock ``write`` / ``open`` handlers and lock button ``write`` handlers.

Lock ``write`` handlers are coroutines called as ``(lock, product, locked)``;
``open`` handlers and button ``write`` handlers as ``(entity, product)``. Each
awaits the data point write, so a rejected command fails the service call.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from ....const import DOMAIN
from ....tuya_ble import TuyaBLEDataPointType, build_timestamped_unlock_command
from .. import requires_dp_ids

if TYPE_CHECKING:
    from ....button import TuyaBLEButton
    from ....entity import TuyaBLEEntity, TuyaBLEProductInfo
    from ....lock import TuyaBLELock


async def _write(
    entity: TuyaBLEEntity,
    dp_id: int,
    dp_type: TuyaBLEDataPointType,
    value: bytes | bool,
) -> None:
    """Write *value* to *dp_id* and wait for the device to take it."""
    datapoint = entity.device.datapoints.get_or_create(dp_id, dp_type, value)
    await datapoint.set_value(value)


async def _timestamped_unlock_command(entity: TuyaBLEEntity) -> bytes:
    """Build the DP 71 command from the stored cloud ``ble_unlock_check``.

    The check comes from the device's credentials, which at runtime are the
    ones stored on the config entry. Raises before anything is written when it
    is missing or malformed. Neither the check nor the command is ever logged.
    """
    device = entity.device
    credentials = await device.device_manager.get_device_credentials(device.address)
    check = credentials.ble_unlock_check if credentials is not None else None
    if not check:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="ble_unlock_check_missing"
        )
    try:
        return build_timestamped_unlock_command(
            check, int(dt_util.utcnow().timestamp())
        )
    except ValueError:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="ble_unlock_check_invalid"
        ) from None


# --- lock write handlers ----------------------------------------------------


async def automatic_lock(
    lock: TuyaBLELock, _product: TuyaBLEProductInfo, locked: bool
) -> None:
    """Lock by enabling automatic locking, unlock by disabling it (free passage).

    The entity's DP is the automatic-lock switch (ha-tuya-ble/ha_tuya_ble#353).
    """
    await _write(lock, lock.dp_id, TuyaBLEDataPointType.DT_BOOL, locked)


@requires_dp_ids("manual_lock_dp_id", "passage_dp_id")
async def manual_lock_or_passage(
    lock: TuyaBLELock, _product: TuyaBLEProductInfo, locked: bool
) -> None:
    """Lock with ``manual_lock`` on, unlock by turning passage mode on.

    For a lever lock that relocks by itself after a momentary open: writing
    ``manual_lock`` off does nothing, and locking also ends passage mode
    (ha-tuya-ble/ha_tuya_ble#324).
    """
    if locked:
        await _write(
            lock,
            lock.descriptor_dp_id("manual_lock_dp_id"),
            TuyaBLEDataPointType.DT_BOOL,
            True,
        )
        return
    await _write(
        lock,
        lock.descriptor_dp_id("passage_dp_id"),
        TuyaBLEDataPointType.DT_BOOL,
        True,
    )


@requires_dp_ids("manual_lock_dp_id", "unlock_dp_id")
async def manual_lock_or_raw_unlock(
    lock: TuyaBLELock, _product: TuyaBLEProductInfo, locked: bool
) -> None:
    """Lock with ``manual_lock`` on, unlock with an empty raw unlock trigger.

    Writing ``manual_lock`` off does not move the bolt on these locks
    (ha-tuya-ble/ha_tuya_ble#315, #339).
    """
    if locked:
        await _write(
            lock,
            lock.descriptor_dp_id("manual_lock_dp_id"),
            TuyaBLEDataPointType.DT_BOOL,
            True,
        )
        return
    await _write(
        lock,
        lock.descriptor_dp_id("unlock_dp_id"),
        TuyaBLEDataPointType.DT_RAW,
        b"",
    )


# --- lock open handlers -----------------------------------------------------


@requires_dp_ids("open_dp_id")
async def momentary_open(lock: TuyaBLELock, _product: TuyaBLEProductInfo) -> None:
    """Release the lock briefly with a boolean trigger; it relocks by itself."""
    await _write(
        lock,
        lock.descriptor_dp_id("open_dp_id"),
        TuyaBLEDataPointType.DT_BOOL,
        True,
    )


@requires_dp_ids("open_dp_id")
async def timestamped_open(lock: TuyaBLELock, _product: TuyaBLEProductInfo) -> None:
    """Release the lock briefly with the timestamped ``ble_unlock_check`` command."""
    command = await _timestamped_unlock_command(lock)
    await _write(
        lock,
        lock.descriptor_dp_id("open_dp_id"),
        TuyaBLEDataPointType.DT_RAW,
        command,
    )


# --- button write handlers --------------------------------------------------
# A plain boolean trigger needs no handler: use the button's ``press_value``.


async def press_raw_unlock(button: TuyaBLEButton, _product: TuyaBLEProductInfo) -> None:
    """Send an empty raw unlock trigger (ha-tuya-ble/ha_tuya_ble#315)."""
    await _write(button, button.dp_id, TuyaBLEDataPointType.DT_RAW, b"")


async def press_timestamped_unlock(
    button: TuyaBLEButton, _product: TuyaBLEProductInfo
) -> None:
    """Send the timestamped ``ble_unlock_check`` command.

    For a lock with no truthful lock state, which gets a button instead of a
    lock entity (ha-tuya-ble/ha_tuya_ble#360).
    """
    command = await _timestamped_unlock_command(button)
    await _write(button, button.dp_id, TuyaBLEDataPointType.DT_RAW, command)
