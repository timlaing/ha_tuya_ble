"""Handlers for data points whose payload has no decoded representation."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...sensor import TuyaBLESensor


def raw_hex(sensor: TuyaBLESensor) -> None:
    """Report an opaque payload as hex, leaving the state unset while empty.

    Schedule payloads are device specific and undocumented, so the exact bytes
    are surfaced for diagnostics instead of being guessed at. An empty payload
    is the device reporting "nothing programmed" and must not be shown as an
    empty state.
    """
    datapoint = sensor.device.datapoints[sensor.dp_id]
    if datapoint is None:
        return
    payload = datapoint.raw_value
    sensor.set_native_value(payload.hex() if payload else None)
