"""Tuya BLE protocol library for Home Assistant."""

from __future__ import annotations

__version__ = "0.1.0"


from .advertisement import (
    TuyaBLEAdvertisementInfo,
    TuyaBLEClassicRecord,
    TuyaBLEIdentityRecord,
    decode_classic_record,
    decode_fd50_identity_record,
    decode_tuya_ble_advertisement,
)
from .base import TuyaBLEDevice, TuyaBLEDeviceFunction
from .const import (
    MANUFACTURER_DATA_ID,
    MAX_DEVICE_ID_LENGTH,
    MAX_UUID_LENGTH,
    SERVICE_UUID,
    SERVICE_UUID_V4,
    SERVICE_UUIDS,
    ConnectionProfile,
    DPType,
    SecurityMode,
    TuyaBLEDataPointType,
)
from .datapoints import TuyaBLEDataPoint, TuyaBLEDataPoints, TuyaBLEReport
from .lock_check import (
    build_timestamped_unlock_command,
    decode_ble_unlock_check,
    is_valid_ble_unlock_check,
)
from .manager import (
    AbstractTuyaBLEDeviceManager,
    TuyaBLEDeviceCredentials,
)
from .probe import (
    ProbeOutcome,
    ProbeStage,
    async_detect_v4_credentials,
    async_detect_v4_profile,
    async_probe_authenticated_status,
)
from .protocol_mixin import BLE_CONNECTION_EXCEPTIONS, BLEAK_EXCEPTIONS
from .security import is_valid_sec_key, security_modes

__all__ = [
    "AbstractTuyaBLEDeviceManager",
    "BLEAK_EXCEPTIONS",
    "BLE_CONNECTION_EXCEPTIONS",
    "DPType",
    "ConnectionProfile",
    "ProbeOutcome",
    "ProbeStage",
    "SecurityMode",
    "MANUFACTURER_DATA_ID",
    "MAX_DEVICE_ID_LENGTH",
    "MAX_UUID_LENGTH",
    "SERVICE_UUID",
    "SERVICE_UUID_V4",
    "SERVICE_UUIDS",
    "TuyaBLEAdvertisementInfo",
    "TuyaBLEClassicRecord",
    "TuyaBLEIdentityRecord",
    "TuyaBLEDataPoint",
    "TuyaBLEDataPoints",
    "TuyaBLEDataPointType",
    "TuyaBLEReport",
    "TuyaBLEDevice",
    "TuyaBLEDeviceCredentials",
    "TuyaBLEDeviceFunction",
    "decode_classic_record",
    "decode_fd50_identity_record",
    "decode_tuya_ble_advertisement",
    "is_valid_sec_key",
    "security_modes",
    "async_probe_authenticated_status",
    "async_detect_v4_credentials",
    "async_detect_v4_profile",
    "build_timestamped_unlock_command",
    "decode_ble_unlock_check",
    "is_valid_ble_unlock_check",
]
