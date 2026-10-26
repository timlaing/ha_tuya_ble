"""Tuya BLE protocol mixin with packet building, sending, and parsing."""

from __future__ import annotations

from .exceptions import BLE_CONNECTION_EXCEPTIONS, BLEAK_EXCEPTIONS
from .protocol_notify import TuyaBLEProtocolNotifyMixin

__all__ = [
    "BLEAK_EXCEPTIONS",
    "BLE_CONNECTION_EXCEPTIONS",
    "TuyaBLEProtocol",
]


class TuyaBLEProtocol(TuyaBLEProtocolNotifyMixin):
    """Mixin providing Tuya BLE protocol methods for TuyaBLEDevice."""
