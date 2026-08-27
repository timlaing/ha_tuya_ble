"""Tuya BLE data point classes."""

from __future__ import annotations

from struct import pack
import time
from typing import TYPE_CHECKING

from .const import TuyaBLEDataPointType
from .exceptions import TuyaBLEEnumValueError

if TYPE_CHECKING:
    from .base import TuyaBLEDevice


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
    ) -> None:
        self._owner = owner
        self._id = dp_id
        self._value = value
        self._changed_by_device = False
        self.update_from_device(timestamp, flags, dp_type, value)

    def update_from_device(
        self,
        timestamp: float,
        flags: int,
        dp_type: TuyaBLEDataPointType,
        value: bytes | bool | int | str,
    ) -> None:
        """Update the data point value from a device update."""
        self._timestamp = timestamp
        self._flags = flags
        self._type = dp_type
        self._changed_by_device = self._value != value
        self._value = value

    def get_value(self) -> bytes:
        """Return the serialized value as bytes."""
        result = b""
        match self._type:
            case TuyaBLEDataPointType.DT_RAW | TuyaBLEDataPointType.DT_BITMAP:
                assert isinstance(self._value, bytes)
                result = self._value
            case TuyaBLEDataPointType.DT_BOOL:
                result = pack(">B", 1 if self._value else 0)
            case TuyaBLEDataPointType.DT_VALUE:
                assert isinstance(self._value, int)
                result = pack(">i", self._value)
            case TuyaBLEDataPointType.DT_ENUM:
                assert isinstance(self._value, int)
                if self._value > 0xFFFF:
                    result = pack(">I", self._value)
                elif self._value > 0xFF:
                    result = pack(">H", self._value)
                else:
                    result = pack(">B", self._value)
            case TuyaBLEDataPointType.DT_STRING:
                assert isinstance(self._value, str)
                result = self._value.encode()
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
    def changed_by_device(self) -> bool:
        """Return whether the value was changed by the device."""
        return self._changed_by_device

    async def set_value(self, value: bytes | bool | int | str) -> None:
        """Set the data point value and send the update to the device."""
        match self._type:
            case TuyaBLEDataPointType.DT_RAW | TuyaBLEDataPointType.DT_BITMAP:
                self._value = (
                    value if isinstance(value, bytes) else bytes(str(value), "utf-8")
                )
            case TuyaBLEDataPointType.DT_BOOL:
                self._value = bool(value)
            case TuyaBLEDataPointType.DT_VALUE:
                self._value = int(value)
            case TuyaBLEDataPointType.DT_ENUM:
                self._set_enum_value(value)
            case TuyaBLEDataPointType.DT_STRING:
                self._value = str(value)

        self._changed_by_device = False
        await self._owner.update_from_user(self._id)

    def _set_enum_value(self, value: bytes | bool | int | str) -> None:
        """Set an enum value, accepting both integer indices and string values."""
        if isinstance(value, int):
            if value >= 0:
                self._value = value
            else:
                raise TuyaBLEEnumValueError()
        elif isinstance(value, str):
            try:
                int_val = int(value)
                if int_val >= 0:
                    self._value = int_val
                else:
                    raise TuyaBLEEnumValueError()
            except ValueError:
                self._value = value
        else:
            raise TuyaBLEEnumValueError()


class TuyaBLEDataPoints:
    """Collection of data points for a Tuya BLE device."""

    def __init__(self, owner: TuyaBLEDevice) -> None:
        self._owner = owner
        self._datapoints: dict[int, TuyaBLEDataPoint] = {}
        self._update_started: int = 0
        self._updated_datapoints: list[int] = []

    def __len__(self) -> int:
        return len(self._datapoints)

    def __getitem__(self, key: int) -> TuyaBLEDataPoint | None:
        return self._datapoints.get(key)

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
        datapoint = TuyaBLEDataPoint(self, dp_id, time.time(), 0, dp_type, value or b"")
        self._datapoints[dp_id] = datapoint
        return datapoint

    def begin_update(self) -> None:
        """Begin a batch update, deferring outgoing data point writes."""
        self._update_started += 1

    async def end_update(self) -> None:
        """End a batch update, sending any deferred data point writes."""
        if self._update_started > 0:
            self._update_started -= 1
            if self._update_started == 0 and len(self._updated_datapoints) > 0:
                await self._owner.send_datapoints(self._updated_datapoints)
                self._updated_datapoints = []

    def update_from_device(
        self,
        dp_id: int,
        timestamp: float,
        flags: int,
        dp_type: TuyaBLEDataPointType,
        value: bytes | bool | int | str,
    ) -> None:
        """Update or create a data point from a device update."""
        dp = self._datapoints.get(dp_id)
        if dp:
            dp.update_from_device(timestamp, flags, dp_type, value)
        else:
            self._datapoints[dp_id] = TuyaBLEDataPoint(
                self, dp_id, timestamp, flags, dp_type, value
            )

    async def update_from_user(self, dp_id: int) -> None:
        """Handle a user-initiated data point update."""
        if self._update_started > 0:
            if dp_id in self._updated_datapoints:
                self._updated_datapoints.remove(dp_id)
            self._updated_datapoints.append(dp_id)
        else:
            await self._owner.send_datapoints([dp_id])
