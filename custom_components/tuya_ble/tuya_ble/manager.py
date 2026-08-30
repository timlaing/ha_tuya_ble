"""Abstract device manager for Tuya BLE credentials."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


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
        """Checks and creates credentials of the Tuya BLE device."""
        if uuid and local_key and device_id and category and product_id:
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
        return None
