"""Handlers for the Parkside PAMRC robot mower (``gcj`` / ``9hdajpiw``).

Ported from ha-tuya-ble/ha_tuya_ble#291 by @jsimonetti (MIT licence). The
layouts follow the structures declared by the Tuya product model, with
multi-byte integers in network byte order.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from ...entity import TuyaBLEEntity, TuyaBLEProductInfo
    from ...sensor import TuyaBLESensor

# Bit labels of the machine error bitmap, used to name the error log entries.
# Misspellings such as UNEXCEPECT_LOW and LIFTE are the manufacturer's own.
MACHINE_ERRORS = (
    "FAULT_LEAN",
    "FAULT_TOO_STEEP",
    "NO_SIGNAL",
    "L_MOTOR_ERROR",
    "R_MOTOR_ERROR",
    "BATTERY_VOL_HIG",
    "B_TEMP_ERROR",
    "DISCHAR_ERROR",
    "CHARGE_OVERTEMP",
    "BATTERY_DAMAGE",
    "BATTERY_LOWDIS",
    "CHARGE_CURDIS",
    "CHARGE_TEMP",
    "UNEXCEPECT_LOW",
    "B_ERROR_RESET",
    "IMU_INVALID",
    "EMS_INVALID",
    "L_MOTOR_HALL",
    "R_MOTOR_HALL",
    "STEEP_OVER_3S",
    "OUTSIDE_AREA",
    "LIFTE",
    "DTRAPPED",
    "B_MOTOR_ERROR",
    "OVERTURN",
    "MOTOR_OVERCUR",
    "MOTOR_HALL",
    "MOTOR_DISCON",
    "EMS_DISCONNECT",
    "MOTOR_ERROR",
)

WORK_MODES = {1: "auto_mowing", 2: "spot_mowing"}

_ERROR_ENTRY_SIZE = 5
_WORK_ENTRY_SIZE = 9


# --- Rain delay (DP 139: byte 0 enables the delay, byte 1 holds minutes) ----


def _rain_delay(
    entity: TuyaBLEEntity, product: TuyaBLEProductInfo
) -> tuple[int, bytes] | None:
    """Return the rain delay dp_id and payload once the mower has reported it.

    The switch and the delay share one payload, so neither may be written
    before the mower has reported the byte the other one owns.
    """
    dp_id = product.dp_id_for("switch", "rain_delay") or product.dp_id_for(
        "number", "rain_delay_time"
    )
    if dp_id is None:
        return None
    datapoint = entity.device.datapoints[dp_id]
    if datapoint is None or not isinstance(datapoint.value, bytes):
        return None
    if len(datapoint.value) < 2:
        return None
    return dp_id, datapoint.value


def rain_delay_reported(entity: TuyaBLEEntity, product: TuyaBLEProductInfo) -> bool:
    """Report the rain delay entities available once the payload is known."""
    return _rain_delay(entity, product) is not None


def get_rain_delay(entity: TuyaBLEEntity, product: TuyaBLEProductInfo) -> bool | None:
    """Read the rain delay switch from the first payload byte."""
    if (state := _rain_delay(entity, product)) is None:
        return None
    return state[1][0] != 0


async def set_rain_delay(
    entity: TuyaBLEEntity, product: TuyaBLEProductInfo, value: bool
) -> None:
    """Write the rain delay switch, keeping the delay held in the second byte.

    The payload is built inside the owner's write lock, so overlapping calls
    each read the latest payload rather than a stale snapshot.
    """
    if (state := _rain_delay(entity, product)) is None:
        return
    dp_id, _ = state
    datapoint = entity.device.datapoints[dp_id]
    assert datapoint is not None

    def _build_payload() -> bytes:
        dp = entity.device.datapoints[dp_id]
        if dp is None or not isinstance(dp.value, bytes) or len(dp.value) < 2:
            return b""
        payload = dp.value
        return bytes([int(value)]) + payload[1:]

    async with datapoint._owner.apply_with_rollback([(datapoint, _build_payload())]):  # pylint: disable=protected-access
        await datapoint._owner.update_from_user(dp_id)  # pylint: disable=protected-access


def get_rain_delay_time(
    entity: TuyaBLEEntity, product: TuyaBLEProductInfo
) -> float | None:
    """Read the rain delay in minutes from the second payload byte."""
    if (state := _rain_delay(entity, product)) is None:
        return None
    return float(state[1][1])


async def set_rain_delay_time(
    entity: TuyaBLEEntity, product: TuyaBLEProductInfo, value: float
) -> None:
    """Write the rain delay, keeping the switch held in the first byte.

    The payload is built inside the owner's write lock, so overlapping calls
    each read the latest payload rather than a stale snapshot.
    """
    if (state := _rain_delay(entity, product)) is None:
        return
    dp_id, _ = state
    datapoint = entity.device.datapoints[dp_id]
    assert datapoint is not None

    def _build_payload() -> bytes:
        dp = entity.device.datapoints[dp_id]
        if dp is None or not isinstance(dp.value, bytes) or len(dp.value) < 2:
            return b""
        payload = dp.value
        minutes = max(0, min(int(value), 0xFF))
        return payload[:1] + bytes([minutes]) + payload[2:]

    async with datapoint._owner.apply_with_rollback([(datapoint, _build_payload())]):  # pylint: disable=protected-access
        await datapoint._owner.update_from_user(dp_id)  # pylint: disable=protected-access


# --- Logs (raw DPs of fixed-size entries) -----------------------------------


def _entries(sensor: TuyaBLESensor, size: int) -> list[bytes] | None:
    """Split the sensor's raw payload into fixed-size entries."""
    datapoint = sensor.device.datapoints[sensor.dp_id]
    if datapoint is None or not isinstance(datapoint.value, bytes):
        return None
    value = datapoint.value
    return [value[pos : pos + size] for pos in range(0, len(value) - size + 1, size)]


def _timestamp(entry: bytes) -> int:
    """Return the leading uint32 epoch of a log entry."""
    return int.from_bytes(entry[:4], "big")


def _report(sensor: TuyaBLESensor, key: str, entries: list[dict[str, Any]]) -> None:
    """Report the entry count and expose the entries as an attribute."""
    sensor.set_native_value(len(entries))
    sensor.set_extra_state_attributes({key: entries})


def error_log(sensor: TuyaBLESensor) -> None:
    """Decode the error log: entries of a uint32 epoch and an error index."""
    if (raw_entries := _entries(sensor, _ERROR_ENTRY_SIZE)) is None:
        return
    entries = [
        {
            "time": dt_util.utc_from_timestamp(_timestamp(entry)).isoformat(),
            "error": (
                MACHINE_ERRORS[entry[4]]
                if entry[4] < len(MACHINE_ERRORS)
                else str(entry[4])
            ),
        }
        for entry in raw_entries
        if _timestamp(entry)
    ]
    _report(sensor, "errors", entries)


def work_log(sensor: TuyaBLESensor) -> None:
    """Decode the work log: entries of a uint32 start, duration and mode."""
    if (raw_entries := _entries(sensor, _WORK_ENTRY_SIZE)) is None:
        return
    entries = [
        {
            "start": dt_util.utc_from_timestamp(_timestamp(entry)).isoformat(),
            "duration_seconds": int.from_bytes(entry[4:8], "big"),
            "mode": WORK_MODES.get(entry[8], str(entry[8])),
        }
        for entry in raw_entries
        if _timestamp(entry)
    ]
    _report(sensor, "sessions", entries)
