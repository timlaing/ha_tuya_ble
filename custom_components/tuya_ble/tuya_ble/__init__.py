"""Tuya BLE protocol library for Home Assistant."""

from __future__ import annotations

__version__ = "0.1.0"


from .advertisement import (
    TuyaBLEAdvertisementInfo,
    TuyaBLEClassicRecord,
    decode_classic_record,
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
    TuyaBLEDataPointType,
)
from .datapoints import TuyaBLEDataPoint, TuyaBLEDataPoints
from .manager import (
    AbstractTuyaBLEDeviceManager,
    TuyaBLEDeviceCredentials,
)
from .probe import (
    ProbeOutcome,
    ProbeStage,
    async_detect_v4_profile,
    async_probe_authenticated_status,
)
from .protocol_mixin import BLE_CONNECTION_EXCEPTIONS, BLEAK_EXCEPTIONS
from .security import is_valid_sec_key

__all__ = [
    "AbstractTuyaBLEDeviceManager",
    "BLEAK_EXCEPTIONS",
    "BLE_CONNECTION_EXCEPTIONS",
    "DPType",
    "ConnectionProfile",
    "ProbeOutcome",
    "ProbeStage",
    "MANUFACTURER_DATA_ID",
    "MAX_DEVICE_ID_LENGTH",
    "MAX_UUID_LENGTH",
    "SERVICE_UUID",
    "SERVICE_UUID_V4",
    "SERVICE_UUIDS",
    "TuyaBLEAdvertisementInfo",
    "TuyaBLEClassicRecord",
    "TuyaBLEDataPoint",
    "TuyaBLEDataPoints",
    "TuyaBLEDataPointType",
    "TuyaBLEDevice",
    "TuyaBLEDeviceCredentials",
    "TuyaBLEDeviceFunction",
    "decode_classic_record",
    "decode_tuya_ble_advertisement",
    "is_valid_sec_key",
    "async_probe_authenticated_status",
    "async_detect_v4_profile",
]
