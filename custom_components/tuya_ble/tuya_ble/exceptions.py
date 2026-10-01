"""Exceptions raised by the Tuya BLE protocol library."""

from __future__ import annotations


class TuyaBLEError(Exception):
    """Base class for Tuya BLE errors."""


class TuyaBLEEnumValueError(TuyaBLEError):
    """Raised when value assigned to DP_ENUM datapoint has unexpected type."""

    def __init__(self) -> None:
        super().__init__("Value of DP_ENUM datapoint must be unsigned integer")


class TuyaBLEDataFormatError(TuyaBLEError):
    """Raised when data in Tuya BLE structures formatted in wrong way."""

    def __init__(self) -> None:
        super().__init__("Incoming packet is formatted in wrong way")


class TuyaBLEDataCRCError(TuyaBLEError):
    """Raised when data packet has invalid CRC."""

    def __init__(self) -> None:
        super().__init__("Incoming packet has invalid CRC")


class TuyaBLEDataLengthError(TuyaBLEError):
    """Raised when data packet has invalid length."""

    def __init__(self) -> None:
        super().__init__("Incoming packet has invalid length")


class TuyaBLEOutgoingDataLengthError(TuyaBLEError):
    """Raised when an outgoing datapoint does not fit the v3 DP envelope.

    The v3 envelope encodes each datapoint as ``(dp_id, dp_type, length)`` in
    three single-byte fields, so both the id and the serialized value are capped
    at 255. Validating up front keeps ``struct.error`` out of the entity layer.
    """

    def __init__(self, dp_id: int) -> None:
        super().__init__(
            f"Outgoing datapoint {dp_id} exceeds the maximum id or value length "
            "of 255 bytes for the v3 protocol envelope"
        )


class TuyaBLEDeviceError(TuyaBLEError):
    """Raised when Tuya BLE device returned error in response to command."""

    def __init__(self, code: int) -> None:
        super().__init__(f"BLE device returned error code {code}")
