"""Utility methods for the Tuya BLE integration."""

from __future__ import annotations


def to_bool(value: bytes | bool | float | int | str) -> bool:
    """Interpret a data point value as a boolean.

    A raw or bitmap payload is not a boolean and `bool(bytes)` is always true,
    so `b"\\x00"` would read as "on". Treat any set bit in the payload as a set
    flag instead, which matches how a fault bitmap reports a raised flag.
    """
    if isinstance(value, bytes):
        return any(value)
    return bool(value)


def remap_value(
    value: float | int,
    from_min: float | int = 0,
    from_max: float | int = 255,
    to_min: float | int = 0,
    to_max: float | int = 255,
    reverse: bool = False,
) -> float:
    """Remap a value from its current range to a new range."""
    if reverse:
        value = from_max - value + from_min
    return ((value - from_min) / (from_max - from_min)) * (to_max - to_min) + to_min
