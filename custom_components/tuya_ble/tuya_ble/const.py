"""Constants for the Tuya BLE protocol."""

from __future__ import annotations

from enum import Enum, StrEnum

GATT_MTU = 20

DEFAULT_ATTEMPTS = 0xFFFF

# A fragmented message is at most 255 fragments of (GATT_MTU - 3) bytes, since
# each fragment carries a 1-byte packet number. Anything larger than this is a
# misbehaving peer rather than a legitimate payload.
MAX_INPUT_LENGTH = (GATT_MTU - 3) * 255

# Drop a partially reassembled message if the remaining fragments never arrive,
# otherwise the stale buffer desynchronises every subsequent message.
INPUT_REASSEMBLY_TIMEOUT = 5.0

CHARACTERISTIC_NOTIFY = "00002b10-0000-1000-8000-00805f9b34fb"
CHARACTERISTIC_WRITE = "00002b11-0000-1000-8000-00805f9b34fb"

SERVICE_UUID = "0000a201-0000-1000-8000-00805f9b34fb"

MANUFACTURER_DATA_ID = 0x07D0

RESPONSE_WAIT_TIMEOUT = 60

# The pairing request is a fixed-size frame holding uuid + local_key + device_id,
# zero-padded to the end. The field widths below are the protocol's own limits and
# are enforced when the credentials are created, so a corrupt cloud response cannot
# produce an over-long frame the device silently drops.
MAX_UUID_LENGTH = 16
MAX_DEVICE_ID_LENGTH = 20
# Observed wire length: uuid (16) + local_key (6) + device_id (20) = 42, plus the
# two trailing pad bytes the device expects.
PAIRING_REQUEST_LENGTH = 44


class DPType(StrEnum):
    """Data point types (cloud spec)."""

    BOOLEAN = "Boolean"
    ENUM = "Enum"
    INTEGER = "Integer"
    JSON = "Json"
    RAW = "Raw"
    STRING = "String"


class TuyaBLECode(Enum):
    """Function codes used in the Tuya BLE protocol."""

    FUN_SENDER_DEVICE_INFO = 0x0000
    FUN_SENDER_PAIR = 0x0001
    FUN_SENDER_DPS = 0x0002
    FUN_SENDER_DEVICE_STATUS = 0x0003

    FUN_SENDER_UNBIND = 0x0005
    FUN_SENDER_DEVICE_RESET = 0x0006

    FUN_SENDER_OTA_START = 0x000C
    FUN_SENDER_OTA_FILE = 0x000D
    FUN_SENDER_OTA_OFFSET = 0x000E
    FUN_SENDER_OTA_UPGRADE = 0x000F
    FUN_SENDER_OTA_OVER = 0x0010

    FUN_SENDER_DPS_V4 = 0x0027

    FUN_RECEIVE_DP = 0x8001
    FUN_RECEIVE_TIME_DP = 0x8003
    FUN_RECEIVE_SIGN_DP = 0x8004
    FUN_RECEIVE_SIGN_TIME_DP = 0x8005

    FUN_RECEIVE_DP_V4 = 0x8006
    FUN_RECEIVE_TIME_DP_V4 = 0x8007

    FUN_RECEIVE_TIME1_REQ = 0x8011
    FUN_RECEIVE_TIME2_REQ = 0x8012


class TuyaBLEDataPointType(Enum):
    """Data types supported by Tuya BLE data points."""

    DT_RAW = 0
    DT_BOOL = 1
    DT_VALUE = 2
    DT_STRING = 3
    DT_ENUM = 4
    DT_BITMAP = 5
