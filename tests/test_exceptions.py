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
    TuyaBLEOutgoingDataLengthError,
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


@pytest.mark.parametrize("protocol", [3, 4])
def test_outgoing_length_error_preserves_legacy_message(protocol: int) -> None:
    """Existing exception callers retain their exact original diagnostics."""
    limit = 65535 if protocol == 4 else 255
    error = TuyaBLEOutgoingDataLengthError(7, protocol)
    assert str(error) == (
        "Outgoing datapoint 7 exceeds the maximum id or value length "
        f"of {limit} bytes for the v{protocol} protocol envelope"
    )


@pytest.mark.parametrize("dp_id", [-1, 256])
def test_outgoing_id_error_reports_range_without_byte_units(dp_id: int) -> None:
    """An ID is an integer range, not a payload length measured in bytes."""
    error = TuyaBLEOutgoingDataLengthError(dp_id, protocol=4, kind="id")
    assert f"datapoint ID {dp_id}" in str(error)
    assert "0-255" in str(error)
    assert "bytes" not in str(error)
    assert "65535" not in str(error)


def test_outgoing_payload_error_reports_aggregate_byte_limit() -> None:
    """Overflow of a combined frame is not blamed on one individual value."""
    error = TuyaBLEOutgoingDataLengthError(2, protocol=4, kind="payload")
    assert "aggregate payload length" in str(error)
    assert "65535 bytes" in str(error)
    assert "id or value length" not in str(error)
