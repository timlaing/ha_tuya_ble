"""Unit tests for the Tuya BLE config flow and options flow."""

# cspell:ignore dbca

# pylint: disable=protected-access
from __future__ import annotations

import hashlib
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from Crypto.Cipher import AES
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tuya_ble.config_flow import (
    TuyaBLEConfigFlow,
    TuyaBLEOptionsFlow,
)
from custom_components.tuya_ble.const import (
    CONF_CATEGORY,
    CONF_DEVICE_NAME,
    CONF_PRODUCT_ID,
    CONF_PRODUCT_NAME,
    CONF_USER_CODE,
    CONF_UUID,
    DOMAIN,
)
from custom_components.tuya_ble.tuya_ble import SERVICE_UUID, TuyaBLEDeviceCredentials
from custom_components.tuya_ble.tuya_ble.const import MANUFACTURER_DATA_ID
from tests.conftest import FakeAdvertisementData


class FakeLogin:
    """Fake login control used to stub out cloud login calls."""

    def __init__(self, qr_success: bool = True, login_success: bool = True) -> None:
        self.qr_success = qr_success
        self.login_success = login_success
        self.qr_calls: list[tuple[Any, Any]] = []
        self.login_calls: list[tuple[Any, Any]] = []

    def qr_code(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Return a fake QR code response."""
        self.qr_calls.append((args, kwargs))
        if self.qr_success:
            return {"success": True, "result": {"qrcode": "token123"}}
        return {"success": False, "msg": "bad", "code": 1}

    def login_result(self, *args: Any, **kwargs: Any) -> tuple[bool, dict[str, Any]]:
        """Return a fake login result."""
        self.login_calls.append((args, kwargs))
        if self.login_success:
            return (
                True,
                {
                    "t": "t",
                    "uid": "uid",
                    "expire_time": 123,
                    "access_token": "at",
                    "refresh_token": "rt",
                    "terminal_id": "tid",
                    "endpoint": "https://x",
                },
            )
        return (False, {"msg": "failed", "code": 2})


class FakeManager:
    """Fake cloud device manager used to stub out cloud lookups."""

    def __init__(self, credentials: TuyaBLEDeviceCredentials | None = None) -> None:
        self.credentials = credentials
        self.initialized = False
        self.calls: list[tuple[str, bool]] = []

    async def initialize(self) -> None:
        """Mark the fake manager as initialized."""
        self.initialized = True

    async def get_device_credentials_by_uuid(
        self,
        uuid: str,
        *,
        force_update: bool = False,
    ) -> TuyaBLEDeviceCredentials | None:
        """Return the fake credentials and record the call."""
        self.calls.append((uuid, force_update))
        return self.credentials


class FakeDiscovery:
    """Fake Bluetooth service info standing in for a real device."""

    def __init__(
        self,
        address: str = "AA:BB:CC:DD:EE:FF",
        name: str = "FakeDevice",
        with_service: bool = True,
    ) -> None:
        self.address = address
        self.name = name
        self.service_data: dict[str, bytes] | None = (
            {SERVICE_UUID: b"\x00prod"} if with_service else None
        )
        key = hashlib.md5(b"prod").digest()  # noqa: S324
        encrypted_uuid = AES.new(key, AES.MODE_CBC, key).encrypt(b"1234567890abcdef")
        self.manufacturer_data: dict[int, bytes] | None = (
            {MANUFACTURER_DATA_ID: b"\x80\x02" + b"\x00" * 4 + encrypted_uuid}
            if with_service
            else None
        )
        self.advertisement = FakeAdvertisementData(
            service_data=self.service_data,
            manufacturer_data=self.manufacturer_data,
        )


def captured_irrigation_discovery() -> FakeDiscovery:
    """Build the exact advertisement captured from the Diivoo timer."""
    discovery = FakeDiscovery(
        address="DC:23:4D:CD:E0:34",
        name="DC:23:4D:CD:E0:34",
    )
    discovery.service_data = {SERVICE_UUID: bytes.fromhex("006242189ef70302b3")}
    discovery.manufacturer_data = {
        MANUFACTURER_DATA_ID: bytes.fromhex(
            "80030000010061ec486560c8075c37cf692d43faefff"
        )
    }
    discovery.advertisement = FakeAdvertisementData(
        rssi=-76,
        service_data=discovery.service_data,
        manufacturer_data=discovery.manufacturer_data,
        service_uuids=[SERVICE_UUID],
        tx_power=-127,
    )
    return discovery


def build_flow(
    hass: HomeAssistant,
    qr_success: bool = True,
    login_success: bool = True,
) -> TuyaBLEConfigFlow:
    """Build a configured TuyaBLEConfigFlow with fake login control."""
    flow = TuyaBLEConfigFlow()
    flow.hass = hass
    flow._data = {}
    flow.context = {}
    flow._qr_login_control = FakeLogin(qr_success, login_success)
    return flow


def build_options_flow(
    hass: HomeAssistant,
    qr_success: bool = True,
    login_success: bool = True,
) -> tuple[TuyaBLEOptionsFlow, MockConfigEntry]:
    """Build a configured TuyaBLEOptionsFlow with fake login control."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="x",
        data={},
        options={CONF_USER_CODE: "user1"},
        source="bluetooth",
        unique_id=None,
        entry_id="entry1",
    )
    entry.add_to_hass(hass)
    flow = TuyaBLEOptionsFlow(entry)
    flow.hass = hass
    flow.context = {}
    flow.handler = entry.entry_id
    flow._qr_login_control = FakeLogin(qr_success, login_success)
    return flow, entry


async def test_async_step_user_no_input(hass: HomeAssistant) -> None:
    """Test the user step with no user input."""
    flow = build_flow(hass)
    result = await flow.async_step_user(user_input=None)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_async_step_user_success(hass: HomeAssistant) -> None:
    """Test the user step with a valid user input."""
    flow = build_flow(hass)
    result = await flow.async_step_user(user_input={CONF_USER_CODE: "code1"})
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "qr"
    assert flow._data[CONF_USER_CODE] == "code1"


async def test_async_step_user_login_error(hass: HomeAssistant) -> None:
    """Test the user step handling a login error."""
    flow = build_flow(hass, qr_success=False)
    result = await flow.async_step_user(user_input={CONF_USER_CODE: "code1"})
    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "login_error"  # type: ignore[index]


async def test_async_step_user_login_error_placeholders(hass: HomeAssistant) -> None:
    """Test that login error placeholders are populated."""
    flow = build_flow(hass, qr_success=False)
    result = await flow.async_step_user(user_input={CONF_USER_CODE: "code1"})
    assert result["errors"]["base"] == "login_error"  # type: ignore[index]
    assert (
        result["description_placeholders"]["msg"] == "bad"  # type: ignore[index]
    )


async def test_async_step_qr_form(hass: HomeAssistant) -> None:
    """Test the QR step with no user input."""
    flow = build_flow(hass)
    result = await flow.async_step_qr(user_input=None)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "qr"


async def test_async_step_qr_submit(hass: HomeAssistant) -> None:
    """Test the QR step submitting credentials."""
    flow = build_flow(hass)
    flow.context = {}
    manager = FakeManager(
        credentials=TuyaBLEDeviceCredentials(
            uuid="1234567890abcdef",
            local_key="k",
            device_id="dev",
            category="wk",
            product_id="prod",
            device_name="Device",
            product_model=None,
            product_name="Product",
        )
    )
    with (
        patch(
            "custom_components.tuya_ble.config_flow.HASSTuyaBLEDeviceManager",
            return_value=manager,
        ),
        patch.object(flow, "_async_current_ids", return_value=set()),
        patch(
            "custom_components.tuya_ble.config_flow.async_discovered_service_info",
            return_value=[],
        ),
    ):
        result = await flow.async_step_qr(user_input={})
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "no_unconfigured_devices"
    assert manager.initialized is True


async def test_async_step_scan_login_error(hass: HomeAssistant) -> None:
    """Test the scan step handling a login error."""
    flow = build_flow(hass, login_success=False)
    result = await flow.async_step_scan(user_input={})
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "qr"
    assert result["errors"]["base"] == "login_error"  # type: ignore[index]


async def test_async_step_scan_success(hass: HomeAssistant) -> None:
    """Test the scan step submitting credentials."""
    flow = build_flow(hass)
    flow.context = {}
    manager = FakeManager(
        credentials=TuyaBLEDeviceCredentials(
            uuid="1234567890abcdef",
            local_key="k",
            device_id="dev",
            category="wk",
            product_id="prod",
            device_name="Device",
            product_model=None,
            product_name="Product",
        )
    )
    with (
        patch(
            "custom_components.tuya_ble.config_flow.HASSTuyaBLEDeviceManager",
            return_value=manager,
        ),
        patch.object(flow, "_async_current_ids", return_value=set()),
        patch(
            "custom_components.tuya_ble.config_flow.async_discovered_service_info",
            return_value=[],
        ),
    ):
        result = await flow.async_step_scan(user_input={})
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "no_unconfigured_devices"
    assert manager.initialized is True


async def test_bluetooth_flow_sets_up_discovery_directly_after_login(
    hass: HomeAssistant,
) -> None:
    """Set up the captured Diivoo timer without offering a device list."""
    flow = build_flow(hass)
    flow.context = {}
    discovery = captured_irrigation_discovery()
    flow.discovery_info = discovery  # type: ignore[assignment]
    manager = FakeManager(
        credentials=TuyaBLEDeviceCredentials(
            uuid="0237f144b99142e6",
            local_key="test-local-key",
            device_id="bfd2847bb05f5dbca9t4uj",
            category="ggq",
            product_id="fdrbxxbg",
            device_name="Irrigation (Irrigation - Main)",
            product_model=None,
            product_name="Diivoo smart dual water timer",
        )
    )
    with (
        patch(
            "custom_components.tuya_ble.config_flow.HASSTuyaBLEDeviceManager",
            return_value=manager,
        ),
        patch.object(flow, "_async_scan_device", return_value=discovery),
    ):
        result = await flow.async_step_scan(user_input={})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Irrigation (Irrigation - Main)"
    assert manager.calls == [("0237f144b99142e6", False)]
    entry_data = cast(dict[str, Any], result["data"])
    assert entry_data[CONF_UUID] == "0237f144b99142e6"
    assert entry_data["device_id"] == "bfd2847bb05f5dbca9t4uj"
    assert entry_data[CONF_CATEGORY] == "ggq"
    assert entry_data[CONF_PRODUCT_ID] == "fdrbxxbg"
    assert entry_data[CONF_DEVICE_NAME] == "Irrigation (Irrigation - Main)"
    assert entry_data[CONF_PRODUCT_NAME] == "Diivoo smart dual water timer"


async def test_is_matching(hass: HomeAssistant) -> None:
    """Test that flows with identical discovery info match."""
    flow1 = build_flow(hass)
    flow2 = build_flow(hass)
    disc = FakeDiscovery("AA:BB:CC:DD:EE:FF")
    flow1.discovery_info = disc  # type: ignore[assignment]
    flow2.discovery_info = disc  # type: ignore[assignment]
    assert flow1.is_matching(flow2) is True
    other = FakeDiscovery("11:22:33:44:55:66")
    flow2.discovery_info = other  # type: ignore[assignment]
    assert flow1.is_matching(flow2) is False
    flow1.discovery_info = None
    assert flow1.is_matching(flow2) is False


async def test_refresh_discovered_devices(hass: HomeAssistant) -> None:
    """Test discovering devices from service info."""
    flow = build_flow(hass)
    d1 = FakeDiscovery("AA:BB:CC:DD:EE:FF")
    d_known = FakeDiscovery("22:33:44:55:66:77")
    d_nosvc = FakeDiscovery("33:44:55:66:77:88", "NoSvc", with_service=False)
    flow._discovered_devices[d_known.address] = d_known  # type: ignore[assignment]
    flow._unique_id_added = False  # type: ignore[attr-defined]
    with (
        patch(
            "custom_components.tuya_ble.config_flow.async_discovered_service_info",
            return_value=[d1, d_nosvc],
        ),
        patch.object(flow, "_async_current_ids", return_value=set()),
    ):
        flow._refresh_discovered_devices()
    assert (
        flow._discovered_devices[d1.address]  # type: ignore[comparison-overlap]
        is d1
    )
    assert "33:44:55:66:77:88" not in flow._discovered_devices


async def test_refresh_discovered_devices_already_configured(
    hass: HomeAssistant,
) -> None:
    """Test that already-configured devices are skipped."""
    flow = build_flow(hass)
    d1 = FakeDiscovery("AA:BB:CC:DD:EE:FF")
    with (
        patch(
            "custom_components.tuya_ble.config_flow.async_discovered_service_info",
            return_value=[d1],
        ),
        patch.object(flow, "_async_current_ids", return_value={d1.address}),
    ):
        flow._refresh_discovered_devices()
    assert d1.address not in flow._discovered_devices


async def test_async_step_device_setup(hass: HomeAssistant) -> None:
    """Test the device step creating an entry."""
    flow = build_flow(hass)
    flow.context = {}
    manager = FakeManager(
        credentials=TuyaBLEDeviceCredentials(
            uuid="1234567890abcdef",
            local_key="abc",
            device_id="dev",
            category="wk",
            product_id="prod",
            device_name="Device",
            product_model=None,
            product_name="Product",
        )
    )
    flow._manager = manager  # type: ignore[assignment]
    d = FakeDiscovery("AA:BB:CC:DD:EE:FF")
    flow._discovered_devices[d.address] = d  # type: ignore[assignment]
    flow._data = {}
    with patch.object(flow, "_async_scan_device", return_value=d):
        result = await flow.async_step_device(user_input={CONF_ADDRESS: d.address})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Device"
    assert manager.calls == [("1234567890abcdef", False)]


async def test_async_step_device_no_credentials(hass: HomeAssistant) -> None:
    """Test the device step with no credentials returned."""
    flow = build_flow(hass)
    flow.context = {}
    flow._manager = FakeManager(credentials=None)  # type: ignore[assignment]
    d = FakeDiscovery("AA:BB:CC:DD:EE:FF")
    flow._discovered_devices = {d.address: d}  # type: ignore[dict-item]
    flow._unique_id_added = False  # type: ignore[attr-defined]
    with (
        patch(
            "custom_components.tuya_ble.config_flow.async_discovered_service_info",
            return_value=[],
        ),
        patch.object(flow, "_async_current_ids", return_value=set()),
        patch.object(flow, "_async_scan_device", return_value=d),
    ):
        result = await flow.async_step_device(user_input={CONF_ADDRESS: d.address})
    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "device_not_registered"  # type: ignore[index]
    assert flow._get_device_info_error is True


async def test_async_step_device_no_devices(hass: HomeAssistant) -> None:
    """Test the device step with no discovered devices."""
    flow = build_flow(hass)
    flow.context = {}
    flow._manager = FakeManager()  # type: ignore[assignment]
    flow._discovered_devices = {}
    flow._unique_id_added = False  # type: ignore[attr-defined]
    with (
        patch(
            "custom_components.tuya_ble.config_flow.async_discovered_service_info",
            return_value=[],
        ),
        patch.object(flow, "_async_current_ids", return_value=set()),
    ):
        result = await flow.async_step_device(user_input=None)
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "no_unconfigured_devices"


async def test_async_step_device_form(hass: HomeAssistant) -> None:
    """Test the device step rendering a form."""
    flow = build_flow(hass)
    flow.context = {}
    flow._manager = FakeManager(credentials=None)  # type: ignore[assignment]
    d = FakeDiscovery("AA:BB:CC:DD:EE:FF")
    flow._discovered_devices = {d.address: d}  # type: ignore[dict-item]
    flow._unique_id_added = False  # type: ignore[attr-defined]
    with (
        patch(
            "custom_components.tuya_ble.config_flow.async_discovered_service_info",
            return_value=[],
        ),
        patch.object(flow, "_async_current_ids", return_value=set()),
    ):
        result = await flow.async_step_device(user_input=None)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "device"
    assert d.address in cast(Any, result["data_schema"]).schema[CONF_ADDRESS].container


async def test_discovered_device_step_has_no_device_selector(
    hass: HomeAssistant,
) -> None:
    """Keep a Bluetooth-triggered flow locked to its discovered address."""
    flow = build_flow(hass)
    flow.context = {}
    flow._manager = FakeManager(credentials=None)  # type: ignore[assignment]
    d = FakeDiscovery("AA:BB:CC:DD:EE:FF")
    flow.discovery_info = d  # type: ignore[assignment]
    result = await flow.async_step_discovered_device(user_input=None)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "discovered_device"
    assert not cast(Any, result["data_schema"]).schema


async def test_discovered_device_setup_scans_original_address(
    hass: HomeAssistant,
) -> None:
    """Set up a Bluetooth-triggered flow without offering other devices."""
    flow = build_flow(hass)
    flow.context = {}
    credentials = TuyaBLEDeviceCredentials(
        uuid="1234567890abcdef",
        local_key="key",
        device_id="dev",
        category="wk",
        product_id="prod",
        device_name="Cloud name",
        product_model=None,
        product_name="Cloud product",
    )
    flow._manager = FakeManager(credentials)  # type: ignore[assignment]
    discovery = FakeDiscovery("AA:BB:CC:DD:EE:FF")
    flow.discovery_info = discovery  # type: ignore[assignment]
    scan = AsyncMock(return_value=discovery)
    with patch.object(flow, "_async_scan_device", scan):
        result = await flow.async_step_discovered_device(user_input={})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Cloud name"
    scan.assert_awaited_once_with(discovery.address)


async def test_discovered_device_scan_failure_is_retry_form(
    hass: HomeAssistant,
) -> None:
    """Retry only the original discovery when its active scan times out."""
    flow = build_flow(hass)
    flow.context = {}
    flow._manager = FakeManager()  # type: ignore[assignment]
    flow.discovery_info = FakeDiscovery()  # type: ignore[assignment]
    with patch.object(flow, "_async_scan_device", return_value=None):
        result = await flow.async_step_discovered_device(user_input={})
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "discovered_device"
    assert result["errors"]["base"] == "bluetooth_scan_failed"  # type: ignore[index]


async def test_async_scan_device_waits_for_complete_tuya_identity(
    hass: HomeAssistant,
) -> None:
    """Request active scanning and accept the complete Tuya scan response."""
    flow = build_flow(hass)
    discovery = captured_irrigation_discovery()

    async def process_advertisements(
        hass_arg: HomeAssistant,
        predicate: Any,
        matcher: dict[str, Any],
        mode: Any,
        timeout: float,
    ) -> FakeDiscovery:
        assert hass_arg is hass
        assert predicate(discovery) is True
        assert matcher == {"address": discovery.address, "connectable": True}
        assert timeout == 60
        return discovery

    with patch(
        "custom_components.tuya_ble.config_flow.bluetooth.async_process_advertisements",
        side_effect=process_advertisements,
    ):
        result = await flow._async_scan_device(discovery.address)
    assert result is cast(Any, discovery)


async def test_async_scan_device_accepts_block_aligned_extra_manufacturer_bytes(
    hass: HomeAssistant,
) -> None:
    """Scan predicate must accept manufacturer payloads with extra aligned bytes."""
    flow = build_flow(hass)
    discovery = FakeDiscovery()
    discovery.manufacturer_data = {
        MANUFACTURER_DATA_ID: b"\x80\x02" + b"\x00" * 4 + b"\x00" * 48
    }

    async def process_advertisements(
        hass_arg: HomeAssistant,
        predicate: Any,
        matcher: dict[str, Any],
        mode: Any,
        timeout: float,
    ) -> FakeDiscovery:
        assert predicate(discovery) is True
        return discovery

    with patch(
        "custom_components.tuya_ble.config_flow.bluetooth.async_process_advertisements",
        side_effect=process_advertisements,
    ):
        result = await flow._async_scan_device(discovery.address)
    assert result is cast(Any, discovery)


async def test_async_scan_device_rejects_non_block_aligned_manufacturer_bytes(
    hass: HomeAssistant,
) -> None:
    """Scan predicate must reject manufacturer payloads that aren't block aligned."""
    flow = build_flow(hass)
    discovery = FakeDiscovery()
    discovery.manufacturer_data = {
        MANUFACTURER_DATA_ID: b"\x80\x02" + b"\x00" * 4 + b"\x00" * 17
    }

    async def process_advertisements(
        hass_arg: HomeAssistant,
        predicate: Any,
        matcher: dict[str, Any],
        mode: Any,
        timeout: float,
    ) -> FakeDiscovery:
        assert predicate(discovery) is False
        return discovery

    with patch(
        "custom_components.tuya_ble.config_flow.bluetooth.async_process_advertisements",
        side_effect=process_advertisements,
    ):
        result = await flow._async_scan_device(discovery.address)
    assert result is cast(Any, discovery)


async def test_async_scan_device_tolerates_missing_manufacturer_data(
    hass: HomeAssistant,
) -> None:
    """Predicate must not raise when manufacturer_data is None during scanning."""
    flow = build_flow(hass)
    discovery = FakeDiscovery()
    discovery.manufacturer_data = None

    async def process_advertisements(
        hass_arg: HomeAssistant,
        predicate: Any,
        matcher: dict[str, Any],
        mode: Any,
        timeout: float,
    ) -> FakeDiscovery:
        assert predicate(discovery) is False
        return discovery

    with patch(
        "custom_components.tuya_ble.config_flow.bluetooth.async_process_advertisements",
        side_effect=process_advertisements,
    ):
        result = await flow._async_scan_device(discovery.address)
    assert result is cast(Any, discovery)


async def test_async_scan_device_tolerates_missing_service_data(
    hass: HomeAssistant,
) -> None:
    """Predicate must not raise when service_data is None during scanning."""
    flow = build_flow(hass)
    discovery = FakeDiscovery()
    discovery.service_data = None

    async def process_advertisements(
        hass_arg: HomeAssistant,
        predicate: Any,
        matcher: dict[str, Any],
        mode: Any,
        timeout: float,
    ) -> FakeDiscovery:
        assert predicate(discovery) is False
        return discovery

    with patch(
        "custom_components.tuya_ble.config_flow.bluetooth.async_process_advertisements",
        side_effect=process_advertisements,
    ):
        result = await flow._async_scan_device(discovery.address)
    assert result is cast(Any, discovery)


async def test_async_step_bluetooth_discovered(hass: HomeAssistant) -> None:
    """Test the bluetooth discovery step."""
    flow = build_flow(hass)
    flow.context = {}
    flow._unique_id_added = False  # type: ignore[attr-defined]
    with patch.object(flow, "_async_current_ids", return_value=set()):
        result = await flow.async_step_bluetooth(
            FakeDiscovery()  # type: ignore[arg-type]
        )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_async_get_options_flow(hass: HomeAssistant) -> None:
    """Test retrieving the options flow."""
    entry = MockConfigEntry(domain=DOMAIN, title="x", data={}, options={})
    opts = TuyaBLEConfigFlow.async_get_options_flow(entry)
    assert isinstance(opts, TuyaBLEOptionsFlow)


# --------------------------------------------------------------------------
# Options flow
# --------------------------------------------------------------------------


async def test_options_init(hass: HomeAssistant) -> None:
    """Test the options flow init step."""
    flow, _ = build_options_flow(hass)
    result = await flow.async_step_init(user_input=None)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_options_user_no_input(hass: HomeAssistant) -> None:
    """Test the options user step with no input."""
    flow, _ = build_options_flow(hass)
    result = await flow.async_step_user(user_input=None)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_options_user_success(hass: HomeAssistant) -> None:
    """Test the options user step with valid input."""
    flow, _ = build_options_flow(hass)
    result = await flow.async_step_user(user_input={CONF_USER_CODE: "c"})
    assert result["step_id"] == "qr"


async def test_options_user_error(hass: HomeAssistant) -> None:
    """Test the options user step handling a login error."""
    flow, _ = build_options_flow(hass, qr_success=False)
    result = await flow.async_step_user(user_input={CONF_USER_CODE: "c"})
    assert result["errors"]["base"] == "login_error"  # type: ignore[index]


async def test_options_qr_form(hass: HomeAssistant) -> None:
    """Test the options QR step with no input."""
    flow, _ = build_options_flow(hass)
    result = await flow.async_step_qr(user_input=None)
    assert result["step_id"] == "qr"


async def test_options_qr_submit(hass: HomeAssistant) -> None:
    """Test the options QR step submitting credentials."""
    flow, _ = build_options_flow(hass)
    flow._qr_user_code = "user1"
    result = await flow.async_step_qr(user_input={})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_USER_CODE] == "user1"


async def test_options_scan_success(hass: HomeAssistant) -> None:
    """Test the options scan step submitting credentials."""
    flow, _ = build_options_flow(hass)
    flow._qr_user_code = "user1"
    result = await flow.async_step_scan(user_input={})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_USER_CODE] == "user1"


async def test_options_scan_error(hass: HomeAssistant) -> None:
    """Test the options scan step handling a login error."""
    flow, _ = build_options_flow(hass, login_success=False)
    flow._qr_user_code = "user1"
    result = await flow.async_step_scan(user_input={})
    assert result["step_id"] == "qr"
    assert result["errors"]["base"] == "login_error"  # type: ignore[index]
