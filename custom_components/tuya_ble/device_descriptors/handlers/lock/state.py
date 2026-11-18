"""Lock ``read`` handlers: derive ``is_locked`` from what the lock reports.

Each returns ``None`` (unknown) until the lock has reported the data points it
relies on, so a restart or reconnect never defaults the entity to locked.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .. import requires_dp_ids

if TYPE_CHECKING:
    from ....entity import TuyaBLEProductInfo
    from ....lock import TuyaBLELock


def automatic_lock(lock: TuyaBLELock, _product: TuyaBLEProductInfo) -> bool | None:
    """Report locked while automatic locking (the entity's DP) is on.

    Turning automatic locking off is free passage. A lock without a bolt
    report stays locked while a fingerprint or momentary open briefly releases
    it (ha-tuya-ble/ha_tuya_ble#353).
    """
    datapoint = lock.device.datapoints[lock.dp_id]
    if datapoint is None:
        return None
    return bool(datapoint.value)


@requires_dp_ids("passage_dp_id")
def passage_or_bolt(lock: TuyaBLELock, _product: TuyaBLEProductInfo) -> bool | None:
    """Report unlocked while passage mode or the bolt report (entity DP) is on.

    The bolt report is only sent when it changes, while passage mode is
    re-reported on every connection, so either alone is enough to know the
    lock is open (ha-tuya-ble/ha_tuya_ble#327).
    """
    passage = lock.device.datapoints[lock.descriptor_dp_id("passage_dp_id")]
    bolt = lock.device.datapoints[lock.dp_id]
    if passage is None and bolt is None:
        return None
    if passage is not None and passage.value:
        return False
    return not (bolt is not None and bolt.value)
