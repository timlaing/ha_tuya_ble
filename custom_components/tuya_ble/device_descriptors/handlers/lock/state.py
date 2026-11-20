"""Lock ``read`` handlers: derive ``is_locked`` from what the lock reports.

Each reads only what the lock itself reported (``reported_value``), never an
optimistic local write, and returns ``None`` (unknown) until the lock has
reported the data points it relies on, so a restart, reconnect or unconfirmed
command never defaults the entity to locked.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .. import requires_dp_ids

if TYPE_CHECKING:
    from ....entity import TuyaBLEProductInfo
    from ....lock import TuyaBLELock


def _reported(lock: TuyaBLELock, dp_id: int) -> bool | None:
    """Return the lock's last reported boolean for *dp_id*, or None if unreported."""
    datapoint = lock.device.datapoints[dp_id]
    if datapoint is None or datapoint.reported_value is None:
        return None
    return bool(datapoint.reported_value)


def automatic_lock(lock: TuyaBLELock, _product: TuyaBLEProductInfo) -> bool | None:
    """Report locked while automatic locking (the entity's DP) is on.

    Turning automatic locking off is free passage. A lock without a bolt
    report stays locked while a fingerprint or momentary open briefly releases
    it (ha-tuya-ble/ha_tuya_ble#353).
    """
    return _reported(lock, lock.dp_id)


@requires_dp_ids("passage_dp_id")
def passage_or_bolt(lock: TuyaBLELock, _product: TuyaBLEProductInfo) -> bool | None:
    """Report unlocked while passage mode or the bolt report (entity DP) is on.

    The bolt report is only sent when it changes, while passage mode is
    re-reported on every connection, so either alone is enough to know the
    lock is open (ha-tuya-ble/ha_tuya_ble#327). One negative report is not
    enough to call it locked: the unreported value could be open.
    """
    passage = _reported(lock, lock.descriptor_dp_id("passage_dp_id"))
    bolt = _reported(lock, lock.dp_id)
    if passage is True or bolt is True:
        return False
    if passage is False and bolt is False:
        return True
    return None
