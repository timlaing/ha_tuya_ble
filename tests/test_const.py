"""Unit tests for the Tuya BLE protocol constants."""

from __future__ import annotations

import pytest

from custom_components.tuya_ble.tuya_ble.const import (
    CHARACTERISTIC_NOTIFY,
    CHARACTERISTIC_WRITE,
    DEFAULT_ATTEMPTS,
    GATT_MTU,
    MANUFACTURER_DATA_ID,
    RESPONSE_WAIT_TIMEOUT,
    SERVICE_UUID,
    TuyaBLECode,
    TuyaBLEDataPointType,
)


def test_protocol_constants() -> None:
    """Assert the fixed protocol constants keep their documented values."""
    assert GATT_MTU == 20
    assert DEFAULT_ATTEMPTS == 0xFFFF
    assert SERVICE_UUID == "0000a201-0000-1000-8000-00805f9b34fb"
    assert MANUFACTURER_DATA_ID == 0x07D0
    assert RESPONSE_WAIT_TIMEOUT == 60
    assert CHARACTERISTIC_NOTIFY == "00002b10-0000-1000-8000-00805f9b34fb"
    assert CHARACTERISTIC_WRITE == "00002b11-0000-1000-8000-00805f9b34fb"


@pytest.mark.parametrize(
    ("member", "value"),
    [
        (TuyaBLECode.FUN_SENDER_DEVICE_INFO, 0x0000),
        (TuyaBLECode.FUN_SENDER_PAIR, 0x0001),
        (TuyaBLECode.FUN_SENDER_DPS, 0x0002),
        (TuyaBLECode.FUN_SENDER_DEVICE_STATUS, 0x0003),
        (TuyaBLECode.FUN_SENDER_UNBIND, 0x0005),
        (TuyaBLECode.FUN_SENDER_DEVICE_RESET, 0x0006),
        (TuyaBLECode.FUN_SENDER_OTA_START, 0x000C),
        (TuyaBLECode.FUN_SENDER_OTA_FILE, 0x000D),
        (TuyaBLECode.FUN_SENDER_OTA_OFFSET, 0x000E),
        (TuyaBLECode.FUN_SENDER_OTA_UPGRADE, 0x000F),
        (TuyaBLECode.FUN_SENDER_OTA_OVER, 0x0010),
        (TuyaBLECode.FUN_SENDER_DPS_V4, 0x0027),
        (TuyaBLECode.FUN_RECEIVE_DP, 0x8001),
        (TuyaBLECode.FUN_RECEIVE_TIME_DP, 0x8003),
        (TuyaBLECode.FUN_RECEIVE_SIGN_DP, 0x8004),
        (TuyaBLECode.FUN_RECEIVE_SIGN_TIME_DP, 0x8005),
        (TuyaBLECode.FUN_RECEIVE_DP_V4, 0x8006),
        (TuyaBLECode.FUN_RECEIVE_TIME_DP_V4, 0x8007),
        (TuyaBLECode.FUN_RECEIVE_TIME1_REQ, 0x8011),
        (TuyaBLECode.FUN_RECEIVE_TIME2_REQ, 0x8012),
    ],
)
def test_code_values(member: TuyaBLECode, value: int) -> None:
    """Assert each TuyaBLECode member holds the expected integer value."""
    assert member.value == value


@pytest.mark.parametrize(
    ("member", "value"),
    [
        (TuyaBLEDataPointType.DT_RAW, 0),
        (TuyaBLEDataPointType.DT_BOOL, 1),
        (TuyaBLEDataPointType.DT_VALUE, 2),
        (TuyaBLEDataPointType.DT_STRING, 3),
        (TuyaBLEDataPointType.DT_ENUM, 4),
        (TuyaBLEDataPointType.DT_BITMAP, 5),
    ],
)
def test_type_values(member: TuyaBLEDataPointType, value: int) -> None:
    """Assert each TuyaBLEDataPointType member holds its expected value."""
    assert member.value == value
