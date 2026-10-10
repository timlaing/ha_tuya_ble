"""Abstract device manager for Tuya BLE credentials."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
import logging
from typing import Any

from .const import MAX_DEVICE_ID_LENGTH, MAX_UUID_LENGTH

_LOGGER = logging.getLogger(__name__)


@dataclass
class TuyaBLEDeviceCredentials:
    """Credentials needed to connect to a Tuya BLE device."""

    uuid: str
    local_key: str
    device_id: str
    category: str
    product_id: str
    device_name: str | None
    product_model: str | None
    product_name: str | None
    functions: list[Any] | None = None
    status_range: list[Any] | None = None
    local_schema: dict[str, Any] | None = None
    cloud_info: dict[str, Any] | None = None

    def __str__(self) -> str:
        return (
            "uuid: xxxxxxxxxxxxxxxx, "
            "local_key: xxxxxxxxxxxxxxxx, "
            "device_id: xxxxxxxxxxxxxxxx, "
            f"category: {self.category}, "
            f"product_id: {self.product_id}, "
            f"device_name: {self.device_name}, "
            f"product_model: {self.product_model}, "
            f"product_name: {self.product_name}"
        )


class AbstractTuyaBLEDeviceManager(ABC):
    """Abstract manager of the Tuya BLE devices credentials."""

    @abstractmethod
    async def get_device_credentials(
        self,
        address: str,
    ) -> TuyaBLEDeviceCredentials | None:
        """Get stored runtime credentials for a BLE address."""

    @staticmethod
    def check_and_create_device_credentials(
        uuid: str | None,
        local_key: str | None,
        device_id: str | None,
        category: str | None,
        product_id: str | None,
        device_name: str | None,
        product_model: str | None,
        product_name: str | None,
    ) -> TuyaBLEDeviceCredentials | None:
        """Checks and creates credentials of the Tuya BLE device.

        The pairing request packs uuid + local_key + device_id into a fixed
        44-byte frame, so over-long values are rejected here rather than
        producing a frame the device silently drops.
        """
        if not (uuid and local_key and device_id and category and product_id):
            return None
        if len(uuid) > MAX_UUID_LENGTH or len(device_id) > MAX_DEVICE_ID_LENGTH:
            _LOGGER.warning(
                "Rejecting device credentials with out-of-range uuid (%d > %d) or "
                "device_id (%d > %d) length",
                len(uuid),
                MAX_UUID_LENGTH,
                len(device_id),
                MAX_DEVICE_ID_LENGTH,
            )
            return None
        return TuyaBLEDeviceCredentials(
            uuid,
            local_key,
            device_id,
            category,
            product_id,
            device_name,
            product_model,
            product_name,
        )
