"""Constants for the Tuya BLE protocol."""

from __future__ import annotations

from enum import Enum, StrEnum

GATT_MTU = 20

# A fragmented message is at most 255 fragments of (GATT_MTU - 3) bytes, since
# each fragment carries a 1-byte packet number. Anything larger than this is a
# misbehaving peer rather than a legitimate payload.
MAX_INPUT_LENGTH = (GATT_MTU - 3) * 255

# Drop a partially reassembled message if the remaining fragments never arrive,
# otherwise the stale buffer desynchronises every subsequent message.
INPUT_REASSEMBLY_TIMEOUT = 5.0

CHARACTERISTIC_NOTIFY = "00002b10-0000-1000-8000-00805f9b34fb"
CHARACTERISTIC_WRITE = "00002b11-0000-1000-8000-00805f9b34fb"

# The Tuya BLE SDK's GATT table for protocol 4 (FD50 service). Firmware is not
# guaranteed to follow that table, so a protocol-4 device only uses this pair
# when its GATT services offer both characteristics, and the classic pair above
# otherwise.
CHARACTERISTIC_NOTIFY_V4 = "00000002-0000-1001-8001-00805f9b07d0"
CHARACTERISTIC_WRITE_V4 = "00000001-0000-1001-8001-00805f9b07d0"

SERVICE_UUID = "0000a201-0000-1000-8000-00805f9b34fb"
SERVICE_UUID_V4 = "0000fd50-0000-1000-8000-00805f9b34fb"
SERVICE_UUIDS = (SERVICE_UUID, SERVICE_UUID_V4)

MANUFACTURER_DATA_ID = 0x07D0

RESPONSE_WAIT_TIMEOUT = 60

PROTOCOL_VERSION_V4 = 4
# Confirmed device protocols, not the initial advertisement/handshake marker.
SUPPORTED_PROTOCOL_VERSIONS = (3, 4)

# The pairing request is a fixed-size frame holding uuid + local_key + device_id,
# zero-padded to the end. The field widths below are the protocol's own limits and
# are enforced when the credentials are created, so a corrupt cloud response cannot
# produce an over-long frame the device silently drops.
MAX_UUID_LENGTH = 16
MAX_DEVICE_ID_LENGTH = 20
# Observed wire length: uuid (16) + local_key (6) + device_id (20) = 42, plus the
# two trailing pad bytes the device expects.
PAIRING_REQUEST_LENGTH = 44

# The cloud sec_key used by Security V2 key derivation.
SECURITY_V2_KEY_LENGTH = 16

# The device-info payload the Tuya app sends to TuyaOS FD50 firmware: its
# receive capacity, 243 bytes, regardless of the negotiated ATT MTU.
APP_DEVICE_INFO_CAPACITY = 0xF3

# Security flags prefixing each encrypted frame: login and session keys for the
# classic scheme, and their Security V2 counterparts.
SECURITY_FLAG_LOGIN = 4
SECURITY_FLAG_SESSION = 5
SECURITY_FLAG_V2_LOGIN = 14
SECURITY_FLAG_V2_SESSION = 15


class ConnectionProfile(StrEnum):
    """Verified FD50 handshake variants, independent of the report version."""

    V4_20 = "v4_20"
    V2_20 = "v2_20"
    V4_MTU = "v4_mtu"
    V2_MTU = "v2_mtu"
    # The Tuya app's own handshake (YZD02B and lock app captures): marker 0x20 and
    # a 243-byte receive capacity, sent in 20-byte fragments.
    V2_APP = "v2_app"

    @property
    def info_version(self) -> int:
        """Return the initial device-info frame's protocol marker."""
        return 2 if self in (self.V2_20, self.V2_MTU, self.V2_APP) else 4

    @property
    def app_capacity(self) -> bool:
        """Return whether device info announces the app's 243-byte capacity."""
        return self is self.V2_APP

    @property
    def negotiated_mtu(self) -> bool:
        """Return whether this profile needs larger negotiated ATT writes."""
        return self in (self.V4_MTU, self.V2_MTU)


class SecurityMode(StrEnum):
    """Login channel a device verified with, independent of its handshake profile."""

    # Flags 4/5, keyed with the first six bytes of the local key.
    CLASSIC = "classic"
    # Flags 14/15, keyed with the full local key and the cloud sec_key.
    V2 = "v2"


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


V4_VALUE_WIDTHS = {
    TuyaBLEDataPointType.DT_BOOL: (1,),
    # Firmware chooses the integer width (the SRB-PM01 reports 1 and 2 bytes);
    # protocol 3 decodes the same widths signed.
    TuyaBLEDataPointType.DT_VALUE: (1, 2, 4),
    TuyaBLEDataPointType.DT_ENUM: (1,),
    TuyaBLEDataPointType.DT_BITMAP: (1, 2, 4),
}
