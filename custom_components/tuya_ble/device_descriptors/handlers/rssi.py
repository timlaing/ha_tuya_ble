"""RSSI signal-strength sensor handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...sensor import TuyaBLESensor


def rssi(sensor: TuyaBLESensor) -> None:
    """Read the RSSI signal strength from the device."""
    sensor.set_native_value(sensor.device.rssi)
