"""Shared fixtures and import path setup for the test suite."""

# pylint: disable=redefined-outer-name

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from homeassistant.core import HomeAssistant
import pytest

from custom_components.tuya_ble.device_registry import DeviceEntities, EntityDescriptor
from custom_components.tuya_ble.devices import (
    TuyaBLECoordinator,
    TuyaBLEProductInfo,
)
from custom_components.tuya_ble.tuya_ble import (
    TuyaBLEDataPointType,
    TuyaBLEDevice,
)
from custom_components.tuya_ble.tuya_ble.datapoints import (
    TuyaBLEDataPoints,
)
from custom_components.tuya_ble.tuya_ble.manager import (
    AbstractTuyaBLEDeviceManager,
    TuyaBLEDeviceCredentials,
)


class FakeBLEManager(AbstractTuyaBLEDeviceManager):
    """Concrete fake of AbstractTuyaBLEDeviceManager."""

    def __init__(self, credentials: TuyaBLEDeviceCredentials | None = None) -> None:
        self.credentials = credentials
        self.address: str = ""

    async def get_device_credentials(
        self,
        address: str,
    ) -> TuyaBLEDeviceCredentials | None:
        """Get the fake device credentials for the given address."""
        self.address = address
        return self.credentials


@dataclass
class FakeBLEAddress:
    """Stands in for bleak BLEDevice."""

    address: str = "AA:BB:CC:DD:EE:FF"
    name: str = "TestDevice"


class FakeAdvertisementData:
    """Stands in for bleak AdvertisementData."""

    def __init__(self, rssi: int = -50, **kwargs: Any) -> None:
        self.rssi = rssi
        self.service_data: dict[str, bytes] = kwargs.pop("service_data", {})
        self.manufacturer_data: dict[int, bytes] = kwargs.pop("manufacturer_data", {})
        for key, value in kwargs.items():
            setattr(self, key, value)


class FakeBleakClient:
    """Fake of BleakClientWithServiceCache that records writes and handlers."""

    def __init__(self, is_connected: bool = True) -> None:
        self._is_connected = is_connected
        self.writes: list[bytes] = []
        self.notify_handler: Callable[[str, bytearray], None] | None = None
        self._notify_started = False
        self.stopped: list[str] = []

    @property
    def is_connected(self) -> bool:
        """Return whether the client is connected."""
        return self._is_connected

    async def start_notify(
        self, gatt_char: str, handler: Callable[[str, bytearray], None]
    ) -> None:
        """Start notifications on the given characteristic."""
        self.notify_handler = handler
        self._notify_started = True

    async def stop_notify(self, gatt_char: str) -> None:
        """Stop notifications on the given characteristic."""
        self._notify_started = False
        self.stopped.append(gatt_char)

    async def disconnect(self) -> None:
        """Disconnect the client."""
        self._is_connected = False

    async def write_gatt_char(
        self, gatt_char: str, data: bytes, response: bool
    ) -> None:
        """Write data to a GATT characteristic."""
        if self._is_connected:
            self.writes.append(bytes(data))
        else:
            raise OSError("not connected")


def make_device(
    manager: AbstractTuyaBLEDeviceManager | None = None,
    address: str = "AA:BB:CC:DD:EE:FF",
    name: str = "TestDevice",
    rssi: int = -50,
) -> TuyaBLEDevice:
    """Build a TuyaBLEDevice wired to fakes so it can be driven via public calls."""
    ble_device: BLEDevice = cast(BLEDevice, FakeBLEAddress(address, name))
    adv: AdvertisementData = cast(
        AdvertisementData,
        FakeAdvertisementData(rssi=rssi, service_data={}, manufacturer_data={}),
    )
    return TuyaBLEDevice(cast(AbstractTuyaBLEDeviceManager, manager), ble_device, adv)


def make_credentials(
    uuid: str = "1234567890abcdef",
    local_key: str = "abcdef",
    device_id: str = "device123",
    category: str = "wk",
    product_id: str = "drlajpqc",
    device_name: str = "Device",
) -> TuyaBLEDeviceCredentials:
    """Build a TuyaBLEDeviceCredentials instance."""

    return TuyaBLEDeviceCredentials(
        uuid=uuid,
        local_key=local_key,
        device_id=device_id,
        category=category,
        product_id=product_id,
        device_name=device_name,
        product_model="Model",
        product_name="Product",
    )


class FakeDatapointsOwner:
    """A minimal owner whose send_datapoints calls are recorded."""

    def __init__(self) -> None:
        self.sent: list[list[int]] = []

    async def send_datapoints(self, dp_ids: list[int]) -> None:
        """Record the datapoint ids that were sent."""
        self.sent.append(dp_ids)


@pytest.fixture
def datapoints_owner() -> FakeDatapointsOwner:
    """Return a fresh owner whose send_datapoints is recorded."""
    return FakeDatapointsOwner()


@pytest.fixture
def datapoints(datapoints_owner: FakeDatapointsOwner) -> TuyaBLEDataPoints:
    """Build a TuyaBLEDataPoints backed by the fake owner."""
    return TuyaBLEDataPoints(cast(TuyaBLEDevice, datapoints_owner))


# --- Entity test helpers (used by test_entity_*.py files) ---


_EntityManager = FakeBLEManager(
    TuyaBLEDeviceCredentials(
        uuid="u",
        local_key="k",
        device_id="dev",
        category="",
        product_id="",
        device_name="n",
        product_model="m",
        product_name="pm",
    )
)


class StatusRecordingDevice(TuyaBLEDevice):
    """Device double that logs the status requests instead of sending them."""

    def __init__(
        self,
        manager: AbstractTuyaBLEDeviceManager | None,
        ble_device: BLEDevice,
        advertisement_data: AdvertisementData,
    ) -> None:
        """Start with an empty request log."""
        super().__init__(
            cast(AbstractTuyaBLEDeviceManager, manager), ble_device, advertisement_data
        )
        self.status_requests: list[int] = []

    async def update(self) -> None:
        """Record the status request the coordinator issues on connect."""
        self.status_requests.append(1)


class EntityDevice(StatusRecordingDevice):
    """Device double for entity tests, recording sends and status requests."""

    def __init__(
        self,
        manager: AbstractTuyaBLEDeviceManager | None,
        ble_device: BLEDevice,
        advertisement_data: AdvertisementData,
    ) -> None:
        """Start with empty send and request logs."""
        super().__init__(manager, ble_device, advertisement_data)
        self.sent: list[list[int]] = []

    async def send_datapoints(self, datapoint_ids: list[int]) -> None:
        """Record the data point ids an entity asked the device to send."""
        self.sent.append(datapoint_ids)


def make_status_device[T: StatusRecordingDevice](
    manager: AbstractTuyaBLEDeviceManager | None = None,
    address: str = "AA:BB:CC:DD:EE:FF",
    device_cls: type[T] = StatusRecordingDevice,  # type: ignore[assignment]
) -> T:
    """Build a device double whose status requests are recorded."""
    ble_device: BLEDevice = cast(BLEDevice, FakeBLEAddress(address))
    advertisement_data: AdvertisementData = cast(
        AdvertisementData,
        FakeAdvertisementData(rssi=-50, service_data={}, manufacturer_data={}),
    )
    return device_cls(manager, ble_device, advertisement_data)


def make_product_info(
    *,
    name: str = "Test Product",
    manufacturer: str = "TestMfg",
    mode: int | None = None,
    switch: int | None = None,
    program: int | None = None,
    manual_control: int | None = None,
    water_valve: int | None = None,
) -> TuyaBLEProductInfo:
    """Build a descriptor-backed TuyaBLEProductInfo for tests.

    Mirrors how the integration derives data point ids: a Fingerbot is a
    product with a ``fingerbot_mode`` select, and a water valve is a product
    with a ``valve`` entity. Pass None for an entity the product does not have.
    """
    entities: dict[str, list[EntityDescriptor]] = {}
    if mode is not None:
        entities.setdefault("select", []).append(
            EntityDescriptor(
                platform="select", dp_id=mode, translation_key="fingerbot_mode"
            )
        )
    if switch is not None:
        entities.setdefault("switch", []).append(
            EntityDescriptor(platform="switch", dp_id=switch, translation_key="switch")
        )
    if manual_control is not None:
        entities.setdefault("switch", []).append(
            EntityDescriptor(
                platform="switch",
                dp_id=manual_control,
                translation_key="manual_control",
            )
        )
    if program is not None:
        entities.setdefault("number", []).append(
            EntityDescriptor(
                platform="number",
                dp_id=program,
                translation_key="program_idle_position",
            )
        )
    if water_valve is not None:
        entities.setdefault("valve", []).append(
            EntityDescriptor(
                platform="valve", dp_id=water_valve, translation_key="valve"
            )
        )
    return TuyaBLEProductInfo(
        name=name,
        manufacturer=manufacturer,
        entities=DeviceEntities(
            category="test",
            product_id="testproduct",
            device_name=name,
            manufacturer=manufacturer,
            entities=entities,
        ),
    )


def build_context(
    hass: HomeAssistant,
) -> tuple[TuyaBLEDevice, TuyaBLECoordinator, TuyaBLEProductInfo]:
    """Build a device, coordinator, and product info triple for entity tests."""
    device = make_status_device(_EntityManager, device_cls=EntityDevice)
    coordinator = TuyaBLECoordinator(hass, device)
    return device, coordinator, make_product_info()


def add_dp(
    device: TuyaBLEDevice,
    dp_id: int,
    dp_type: TuyaBLEDataPointType,
    value: bytes | bool | int | str,
    raw: bytes | None = None,
) -> None:
    """Add or update a data point as if pushed from the device."""
    device.datapoints.update_from_device(dp_id, 1000.0, 0, dp_type, value, raw)


async def connect(coordinator: TuyaBLECoordinator) -> None:
    """Simulate the coordinator being connected."""
    coordinator._async_handle_connect()  # pylint: disable=protected-access
