"""Backward-compatible re-exports from split modules.

All symbols that were previously in this file now live in:
  - entity.py      — TuyaBLEEntity, product metadata, device info helpers
  - coordinator.py — TuyaBLECoordinator
  - device_descriptors/handlers/ — per-device handlers (e.g. Fingerbot helpers)

This module re-exports everything so that existing imports continue to work.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .coordinator import TuyaBLECoordinator
from .entity import (
    TuyaBLEEntity,
    TuyaBLEProductInfo,
    TuyaBLERestoreEntity,
    get_device_info,
    get_device_product_info,
    get_device_readable_name,
    get_product_info_by_ids,
    get_short_address,
)

__all__ = [
    "TuyaBLECoordinator",
    "TuyaBLEData",
    "TuyaBLEEntity",
    "TuyaBLEProductInfo",
    "TuyaBLERestoreEntity",
    "get_device_info",
    "get_device_product_info",
    "get_device_readable_name",
    "get_product_info_by_ids",
    "get_short_address",
]


@dataclass
class TuyaBLEData:
    """Data for the Tuya BLE integration."""

    title: str
    device: Any  # TuyaBLEDevice — avoid circular import
    product: TuyaBLEProductInfo
    manager: Any  # AbstractTuyaBLEDeviceManager
    coordinator: TuyaBLECoordinator
