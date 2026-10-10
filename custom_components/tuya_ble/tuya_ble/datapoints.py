"""Tuya BLE data point classes."""

from __future__ import annotations

from collections.abc import Iterable
import logging
from struct import pack
import time
from typing import TYPE_CHECKING

from .const import TuyaBLEDataPointType
from .exceptions import TuyaBLEDataFormatError, TuyaBLEEnumValueError

if TYPE_CHECKING:
    from .base import TuyaBLEDevice

_LOGGER = logging.getLogger(__name__)

TuyaBLEDataPointValue = bytes | bytearray | memoryview | bool | int | str


class TuyaBLEDataPoint:
    """Represents a single data point from a Tuya BLE device."""

    def __init__(
        self,
        owner: TuyaBLEDataPoints,
        dp_id: int,
        timestamp: float,
        flags: int,
        dp_type: TuyaBLEDataPointType,
        value: bytes | bool | int | str,
        raw_value: bytes | None = None,
    ) -> None:
        self._owner = owner
        self._id = dp_id
        self._value = value
        self._changed_by_device = False
        self._raw_value = raw_value
        self.update_from_device(timestamp, flags, dp_type, value, raw_value)

    def update_from_device(
        self,
        timestamp: float,
        flags: int,
        dp_type: TuyaBLEDataPointType,
        value: bytes | bool | int | str,
        raw_value: bytes | None = None,
    ) -> None:
        """Update the data point value from a device update.

        `raw_value` is the exact payload the device sent, kept verbatim so the
        original width and leading zero bytes survive decoding. It is only
        meaningful for values received from the device; a local write leaves
        the previous snapshot untouched and `get_value()` must not be used to
        reconstruct it, since it re-serializes with a different width.
        """
        self._timestamp = timestamp
        self._flags = flags
        self._type = dp_type
        self._changed_by_device = self._value != value
        if self._changed_by_device:
            _LOGGER.debug(
                "Data point %s changed by device: %s -> %s (%s)",
                self._id,
                self._value,
                value,
                dp_type,
            )
        self._value = value
        self._raw_value = raw_value

    @staticmethod
    def _pack_enum(value: int) -> bytes:
        """Pack an enum value using the narrowest integer width that fits."""
        if value > 0xFFFF:
            return pack(">I", value)
        if value > 0xFF:
            return pack(">H", value)
        return pack(">B", value)

    def get_value(self) -> bytes:
        """Return the serialized value as bytes."""
        result = b""
        match self._type:
            case TuyaBLEDataPointType.DT_RAW | TuyaBLEDataPointType.DT_BITMAP:
                if not isinstance(self._value, bytes):
                    raise TuyaBLEDataFormatError()
                result = self._value
            case TuyaBLEDataPointType.DT_BOOL:
                result = pack(">B", 1 if self._value else 0)
            case TuyaBLEDataPointType.DT_VALUE:
                if not isinstance(self._value, int):
                    raise TuyaBLEDataFormatError()
                result = pack(">i", self._value)
            case TuyaBLEDataPointType.DT_ENUM:
                if not isinstance(self._value, int):
                    raise TuyaBLEDataFormatError()
                result = self._pack_enum(self._value)
            case TuyaBLEDataPointType.DT_STRING:
                if not isinstance(self._value, str):
                    raise TuyaBLEDataFormatError()
                result = self._value.encode()
            case _:
                _LOGGER.warning(
                    "Unhandled data point type %s for data point %s, "
                    "serializing to empty value",
                    self._type,
                    self._id,
                )
        return result

    @property
    def dp_id(self) -> int:
        """Return the data point identifier."""
        return self._id

    @property
    def timestamp(self) -> float:
        """Return the timestamp of the last update."""
        return self._timestamp

    @property
    def flags(self) -> int:
        """Return the data point flags."""
        return self._flags

    @property
    def dp_type(self) -> TuyaBLEDataPointType:
        """Return the data point type."""
        return self._type

    @property
    def value(self) -> bytes | bool | int | str:
        """Return the current value."""
        return self._value

    @property
    def raw_value(self) -> bytes | None:
        """Return the exact bytes last received from the device, if any.

        The same `TuyaBLEDataPoint` object is reused across updates, so this is
        always the most recently *received* payload and is not affected by
        local writes. It is `None` when the data point has not been updated
        from a device report.
        """
        return self._raw_value

    @property
    def changed_by_device(self) -> bool:
        """Return whether the value was changed by the device."""
        return self._changed_by_device

    def set_value_no_notify(self, value: TuyaBLEDataPointValue) -> None:
        """Set the value without sending an update to the device."""
        match self._type:
            case TuyaBLEDataPointType.DT_RAW | TuyaBLEDataPointType.DT_BITMAP:
                if not isinstance(value, bytes | bytearray | memoryview):
                    raise TuyaBLEDataFormatError()
                self._value = bytes(value)
            case TuyaBLEDataPointType.DT_BOOL:
                if not isinstance(value, int):
                    raise TuyaBLEDataFormatError()
                self._value = bool(value)
            case TuyaBLEDataPointType.DT_VALUE:
                self._value = int(value)
            case TuyaBLEDataPointType.DT_ENUM:
                self._set_enum_value(value)
            case TuyaBLEDataPointType.DT_STRING:
                self._value = str(value)
        self._changed_by_device = False
        _LOGGER.debug(
            "Data point %s set locally without notifying the device: %s",
            self._id,
            self._value,
        )

    async def set_value(self, value: TuyaBLEDataPointValue) -> None:
        """Set the data point value and send the update to the device."""
        self.set_value_no_notify(value)
        await self._owner.update_from_user(self._id)

    def _set_enum_value(self, value: TuyaBLEDataPointValue) -> None:
        """Set an enum value, accepting both integer indices and string values."""
        if isinstance(value, int):
            if value >= 0:
                self._value = value
                _LOGGER.debug("Data point %s enum set from int: %s", self._id, value)
            else:
                raise TuyaBLEEnumValueError()
        elif isinstance(value, str):
            try:
                int_val = int(value)
                if int_val >= 0:
                    self._value = int_val
                    _LOGGER.debug(
                        "Data point %s enum set from numeric string: %s",
                        self._id,
                        value,
                    )
                else:
                    raise TuyaBLEEnumValueError()
            except ValueError:
                self._value = value
                _LOGGER.debug(
                    "Data point %s enum set from name string: %s", self._id, value
                )
        else:
            raise TuyaBLEEnumValueError()


class TuyaBLEDataPoints:
    """Collection of data points for a Tuya BLE device."""

    def __init__(self, owner: TuyaBLEDevice) -> None:
        self._owner = owner
        self._datapoints: dict[int, TuyaBLEDataPoint] = {}

    def __len__(self) -> int:
        return len(self._datapoints)

    def __getitem__(self, key: int) -> TuyaBLEDataPoint | None:
        return self._datapoints.get(key)

    def values(self) -> Iterable[TuyaBLEDataPoint]:
        """Iterate over every data point known so far, in dp id order."""
        return [self._datapoints[dp_id] for dp_id in sorted(self._datapoints)]

    def has_id(self, dp_id: int, dp_type: TuyaBLEDataPointType | None = None) -> bool:
        """Check if a data point with the given ID exists."""
        return (dp_id in self._datapoints) and (
            (dp_type is None) or (self._datapoints[dp_id].dp_type == dp_type)
        )

    def get_or_create(
        self,
        dp_id: int,
        dp_type: TuyaBLEDataPointType,
        value: bytes | bool | int | str | None = None,
    ) -> TuyaBLEDataPoint:
        """Return an existing data point or create a new one."""
        datapoint = self._datapoints.get(dp_id)
        if datapoint:
            return datapoint
        _LOGGER.debug("Creating new data point %s (%s)", dp_id, dp_type)
        datapoint = TuyaBLEDataPoint(
            self, dp_id, time.time(), 0, dp_type, b"" if value is None else value
        )
        self._datapoints[dp_id] = datapoint
        return datapoint

    def update_from_device(
        self,
        dp_id: int,
        timestamp: float,
        flags: int,
        dp_type: TuyaBLEDataPointType,
        value: bytes | bool | int | str,
        raw_value: bytes | None = None,
    ) -> TuyaBLEDataPoint:
        """Update or create a data point from a device update, returning it."""
        dp = self._datapoints.get(dp_id)
        if dp:
            dp.update_from_device(timestamp, flags, dp_type, value, raw_value)
            return dp
        _LOGGER.debug("Data point %s created from device update", dp_id)
        new_dp = TuyaBLEDataPoint(
            self, dp_id, timestamp, flags, dp_type, value, raw_value
        )
        self._datapoints[dp_id] = new_dp
        return new_dp

    async def update_from_user(self, dp_id: int) -> None:
        """Handle a user-initiated data point update."""
        _LOGGER.debug("Data point %s sent immediately", dp_id)
        await self._owner.send_datapoints([dp_id])
