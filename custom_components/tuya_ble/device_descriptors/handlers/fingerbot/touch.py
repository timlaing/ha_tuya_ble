"""Mode availability and press duration for Fingerbot Touch channels."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ....entity import TuyaBLEEntity, TuyaBLEProductInfo


def _channel_mode(entity: TuyaBLEEntity, channel: int) -> int | None:
    datapoint = entity.device.datapoints[100 + channel]
    if datapoint is not None and isinstance(datapoint.value, int):
        return datapoint.value
    return None


def channel_1_can_switch(entity: TuyaBLEEntity, _product: TuyaBLEProductInfo) -> bool:
    """Return whether the first channel is in Push or Switch mode."""
    return _channel_mode(entity, 1) in (0, 1)


def channel_2_can_switch(entity: TuyaBLEEntity, _product: TuyaBLEProductInfo) -> bool:
    """Return whether the second channel is in Push or Switch mode."""
    return _channel_mode(entity, 2) in (0, 1)


def channel_1_in_switch_mode(
    entity: TuyaBLEEntity, _product: TuyaBLEProductInfo
) -> bool:
    """Return whether the first channel is in Switch mode."""
    return _channel_mode(entity, 1) == 1


def channel_2_in_switch_mode(
    entity: TuyaBLEEntity, _product: TuyaBLEProductInfo
) -> bool:
    """Return whether the second channel is in Switch mode."""
    return _channel_mode(entity, 2) == 1


def push_duration(entity: TuyaBLEEntity, channel: int) -> float | None:
    """Read the channel's press duration in seconds when it is in Push mode."""
    if _channel_mode(entity, channel) != 0:
        return None
    datapoint = entity.device.datapoints[102 + channel]
    if datapoint is not None and isinstance(datapoint.value, int):
        return min(max(datapoint.value, 100), 10000) / 1000
    return 0.1
