"""Tuya BLE protocol mixin with packet building, sending, and parsing."""

from __future__ import annotations

from .protocol_notify import TuyaBLEProtocolNotifyMixin
from .protocol_send import (
    BLE_CONNECTION_EXCEPTIONS,
    BLEAK_EXCEPTIONS,
)

__all__ = [
    "BLEAK_EXCEPTIONS",
    "BLE_CONNECTION_EXCEPTIONS",
    "TuyaBLEProtocol",
]


class TuyaBLEProtocol(TuyaBLEProtocolNotifyMixin):
    """Mixin providing Tuya BLE protocol methods for TuyaBLEDevice."""
