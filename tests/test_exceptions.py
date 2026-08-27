"""Unit tests for the Tuya BLE protocol exceptions."""

from __future__ import annotations

import pytest

from custom_components.tuya_ble.tuya_ble.exceptions import (
    TuyaBLEDataCRCError,
    TuyaBLEDataFormatError,
    TuyaBLEDataLengthError,
    TuyaBLEDeviceError,
    TuyaBLEEnumValueError,
    TuyaBLEError,
)


def test_all_exceptions_derive_from_base() -> None:
    """Assert every protocol exception inherits from the base TuyaBLEError."""
    assert issubclass(TuyaBLEEnumValueError, TuyaBLEError)
    assert issubclass(TuyaBLEDataFormatError, TuyaBLEError)
    assert issubclass(TuyaBLEDataCRCError, TuyaBLEError)
    assert issubclass(TuyaBLEDataLengthError, TuyaBLEError)
    assert issubclass(TuyaBLEDeviceError, TuyaBLEError)


def test_enum_value_error_message() -> None:
    """Assert the enum value error carries its descriptive message."""
    with pytest.raises(
        TuyaBLEEnumValueError,
        match="Value of DP_ENUM datapoint must be unsigned integer",
    ):
        raise TuyaBLEEnumValueError()


def test_data_format_error_message() -> None:
    """Assert the data format error carries its descriptive message."""
    with pytest.raises(
        TuyaBLEDataFormatError, match="Incoming packet is formatted in wrong way"
    ):
        raise TuyaBLEDataFormatError()


def test_data_crc_error_message() -> None:
    """Assert the data CRC error carries its descriptive message."""
    with pytest.raises(TuyaBLEDataCRCError, match="Incoming packet has invalid CRC"):
        raise TuyaBLEDataCRCError()


def test_data_length_error_message() -> None:
    """Assert the data length error carries its descriptive message."""
    with pytest.raises(
        TuyaBLEDataLengthError, match="Incoming packet has invalid length"
    ):
        raise TuyaBLEDataLengthError()


def test_device_error_message_contains_code() -> None:
    """Assert the device error message embeds the error code."""
    with pytest.raises(TuyaBLEDeviceError, match="error code 7"):
        raise TuyaBLEDeviceError(7)
