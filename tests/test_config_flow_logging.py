"""Unit tests for the debug logging emitted by the Tuya BLE config flow."""
# pylint: disable=protected-access
# The tests drive individual logging paths through flow internals, which the
# project's test conventions sanction as setup state.

# cspell:ignore dbca

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import patch

from homeassistant.core import HomeAssistant
import pytest

from custom_components.tuya_ble.const import CONF_USER_CODE
from custom_components.tuya_ble.tuya_ble import SERVICE_UUID, TuyaBLEDeviceCredentials
from custom_components.tuya_ble.tuya_ble.const import MANUFACTURER_DATA_ID
from tests.conftest import FakeAdvertisementData
from tests.test_config_flow import (
    _SCAN_PATCH,
    FakeDiscovery,
    FakeManager,
    build_flow,
    build_options_flow,
    captured_irrigation_discovery,
)


async def test_bluetooth_discovery_is_logged(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Trace the address, name and RSSI that started the flow."""
    flow = build_flow(hass)
    flow.context = {}
    flow._unique_id_added = False  # type: ignore[attr-defined]
    discovery = FakeDiscovery("AA:BB:CC:DD:EE:FF", "MyDevice", rssi=-77)
    with (
        patch.object(flow, "_async_current_ids", return_value=set()),
        caplog.at_level(logging.DEBUG),
    ):
        await flow.async_step_bluetooth(discovery)  # type: ignore[arg-type]
    assert "Bluetooth discovery started: AA:BB:CC:DD:EE:FF" in caplog.text
    assert "name: MyDevice, RSSI: -77" in caplog.text


async def test_qr_code_issued_is_logged_without_token(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A granted QR request is traced, but the token itself is never logged."""
    flow = build_flow(hass)
    with caplog.at_level(logging.DEBUG):
        result = await flow.async_step_user(user_input={CONF_USER_CODE: "code1"})
    assert result["step_id"] == "qr"
    assert "QR code issued" in caplog.text
    assert "token123" not in caplog.text
    assert "code1" not in caplog.text


async def test_qr_code_rejection_is_logged(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A rejected QR request logs the message and code, but never the user code."""
    flow = build_flow(hass, qr_success=False)
    with caplog.at_level(logging.DEBUG):
        await flow.async_step_user(user_input={CONF_USER_CODE: "code1"})
    assert "QR code request rejected: bad (code 1)" in caplog.text
    assert "code1" not in caplog.text


async def test_user_step_login_error_is_logged(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """The surfaced login_error carries the message and code into the log."""
    flow = build_flow(hass, qr_success=False)
    with caplog.at_level(logging.DEBUG):
        await flow.async_step_user(user_input={CONF_USER_CODE: "code1"})
    assert "Login failed: bad (code 1)" in caplog.text
    assert "code1" not in caplog.text


async def test_scan_reissues_qr_code_on_failure(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A pending-but-unfinished login re-issues the QR code and is traced."""
    flow = build_flow(hass, login_success=False)
    flow._qr_user_code = "user1"
    with caplog.at_level(logging.DEBUG):
        result = await flow.async_step_scan({})
    assert result["errors"]["base"] == "login_error"  # type: ignore[index]
    assert "QR code login not completed, re-issuing QR code" in caplog.text
    assert "failed (code 2)" in caplog.text


async def test_scan_device_start_is_logged(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Starting the active scan records the address and timeout."""
    flow = build_flow(hass)
    discovery = FakeDiscovery()

    async def process_advertisements(*_: Any, **__: Any) -> FakeDiscovery:
        return discovery

    with (
        patch(_SCAN_PATCH, side_effect=process_advertisements),
        caplog.at_level(logging.DEBUG),
    ):
        await flow._async_scan_device(discovery.address)
    assert f"{discovery.address}: active scan started, timeout 60s" in caplog.text


async def test_scan_device_timeout_is_logged(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """An active-scan timeout is traced and yields no discovery info."""
    flow = build_flow(hass)
    with (
        patch(_SCAN_PATCH, side_effect=TimeoutError),
        caplog.at_level(logging.DEBUG),
    ):
        assert await flow._async_scan_device("AA:BB:CC:DD:EE:FF") is None
    assert "AA:BB:CC:DD:EE:FF: active scan timed out" in caplog.text


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Return every captured warning message, independent of capture order."""
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING
    ]


# Distinctive values, so a "must never be logged" assertion cannot pass or fail
# by accidentally matching an unrelated substring such as "at" or "tid".
LOGIN_SECRETS = {
    "t": "secret-session-token",
    "uid": "secret-uid",
    "access_token": "secret-access-token",
    "refresh_token": "secret-refresh-token",
    "terminal_id": "secret-terminal-id",
    "endpoint": "https://secret-endpoint.invalid",
}


def _login_info() -> dict[str, Any]:
    """Return a login payload whose every sensitive value is unmistakable."""
    return {**LOGIN_SECRETS, "expire_time": 1}


def _assert_no_secrets_logged(caplog: pytest.LogCaptureFixture) -> None:
    """Fail if any credential from the login payload reached the log."""
    for secret in LOGIN_SECRETS.values():
        assert secret not in caplog.text


async def test_setup_address_without_manager_is_warned(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A missing cloud manager is a warning, not a silent abort."""
    flow = build_flow(hass)
    flow._manager = None
    with caplog.at_level(logging.DEBUG):
        result, error = await flow._async_setup_address("AA:BB:CC:DD:EE:FF")
    assert error is None
    assert result is not None
    assert result["reason"] == "unknown"
    assert any(
        "AA:BB:CC:DD:EE:FF: cannot set up device" in message
        and "cloud manager is not initialised" in message
        for message in _warnings(caplog)
    )


async def test_setup_address_scan_failure_is_logged(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A failed active scan is traced with its form error key."""
    flow = build_flow(hass)
    flow._manager = FakeManager()  # type: ignore[assignment]
    with (
        patch.object(flow, "_async_scan_device", return_value=None),
        caplog.at_level(logging.DEBUG),
    ):
        result, error = await flow._async_setup_address("AA:BB:CC:DD:EE:FF")
    assert result is None
    assert error == "bluetooth_scan_failed"
    assert "AA:BB:CC:DD:EE:FF: bluetooth_scan_failed" in caplog.text


async def test_setup_address_undecodable_identity_is_logged(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """An undecodable advertisement is traced with its raw payload hex."""
    flow = build_flow(hass)
    flow._manager = FakeManager()  # type: ignore[assignment]
    discovery = FakeDiscovery()
    discovery.advertisement = FakeAdvertisementData(
        service_data={SERVICE_UUID: b"\x00prod"},
        manufacturer_data={MANUFACTURER_DATA_ID: b"\x80\x02\x00"},
    )
    with (
        patch.object(flow, "_async_scan_device", return_value=discovery),
        caplog.at_level(logging.DEBUG),
    ):
        result, error = await flow._async_setup_address(discovery.address)
    assert result is None
    assert error == "identity_not_decoded"
    assert "AA:BB:CC:DD:EE:FF: identity_not_decoded" in caplog.text
    assert "manufacturer_data" in caplog.text


async def test_setup_address_unregistered_device_is_warned(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A device missing from the cloud account is a warning, with the force flag."""
    flow = build_flow(hass)
    flow._manager = FakeManager(credentials=None)  # type: ignore[assignment]
    discovery = captured_irrigation_discovery()
    with (
        patch.object(flow, "_async_scan_device", return_value=discovery),
        caplog.at_level(logging.DEBUG),
    ):
        _result, error = await flow._async_setup_address(discovery.address)
    assert error == "device_not_registered"
    assert any(
        "DC:23:4D:CD:E0:34: device_not_registered" in message
        and "0237f144b99142e6" in message
        and "force_update: False" in message
        for message in _warnings(caplog)
    )


async def test_setup_address_entry_created_is_logged(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Creating the entry traces the resolved identity without the local key."""
    flow = build_flow(hass)
    flow._manager = FakeManager(  # type: ignore[assignment]
        credentials=TuyaBLEDeviceCredentials(
            uuid="0237f144b99142e6",
            local_key="super-secret-key",
            device_id="bfd2847bb05f5dbca9t4uj",
            category="ggq",
            product_id="fdrbxxbg",
            device_name="Irrigation",
            product_model=None,
            product_name="Diivoo",
        )
    )
    discovery = captured_irrigation_discovery()
    with (
        patch.object(flow, "_async_scan_device", return_value=discovery),
        caplog.at_level(logging.DEBUG),
    ):
        await flow._async_setup_address(discovery.address)
    assert "DC:23:4D:CD:E0:34: config entry created" in caplog.text
    assert "category: ggq, product_id: fdrbxxbg" in caplog.text
    assert "device_name: Irrigation" in caplog.text
    assert "super-secret-key" not in caplog.text


async def test_device_step_no_unconfigured_devices_is_logged(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Aborting with no devices available is traced."""
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
        caplog.at_level(logging.DEBUG),
    ):
        await flow.async_step_device(user_input=None)
    assert "No unconfigured Tuya BLE devices discovered" in caplog.text


async def test_discovered_device_added_is_logged(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A newly discovered device is traced with the running total."""
    flow = build_flow(hass)
    d1 = FakeDiscovery("AA:BB:CC:DD:EE:FF", "Device1")
    with (
        patch(
            "custom_components.tuya_ble.config_flow.async_discovered_service_info",
            return_value=[d1],
        ),
        patch.object(flow, "_async_current_ids", return_value=set()),
        caplog.at_level(logging.DEBUG),
    ):
        flow._refresh_discovered_devices()
    assert "Discovered Tuya BLE device AA:BB:CC:DD:EE:FF" in caplog.text
    assert "name: Device1, total: 1" in caplog.text


async def test_login_complete_direct_discovery_is_logged(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A Bluetooth-triggered flow sets up its own device after login."""
    flow = build_flow(hass)
    flow.context = {}
    discovery = captured_irrigation_discovery()
    flow.discovery_info = discovery  # type: ignore[assignment]
    manager = FakeManager(credentials=None)
    with (
        patch(
            "custom_components.tuya_ble.config_flow.HASSTuyaBLEDeviceManager",
            return_value=manager,
        ),
        patch.object(flow, "_async_scan_device", return_value=None),
        caplog.at_level(logging.DEBUG),
    ):
        await flow._async_qr_login_store_and_advance(_login_info())
    assert "Login complete, setting up discovered device" in caplog.text
    assert discovery.address in caplog.text
    _assert_no_secrets_logged(caplog)


async def test_login_complete_device_selection_is_logged(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A manually started flow offers the device list after login."""
    flow = build_flow(hass)
    flow.context = {}
    flow.discovery_info = None
    manager = FakeManager(credentials=None)
    with (
        patch(
            "custom_components.tuya_ble.config_flow.HASSTuyaBLEDeviceManager",
            return_value=manager,
        ),
        patch(
            "custom_components.tuya_ble.config_flow.async_discovered_service_info",
            return_value=[],
        ),
        patch.object(flow, "_async_current_ids", return_value=set()),
        caplog.at_level(logging.DEBUG),
    ):
        await flow._async_qr_login_store_and_advance(_login_info())
    assert "Login complete, selecting a device to set up" in caplog.text
    _assert_no_secrets_logged(caplog)


async def test_options_reauth_rejection_is_logged(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A rejected re-authentication logs the code and message, not the token."""
    flow, _ = build_options_flow(hass, qr_success=False)
    with caplog.at_level(logging.DEBUG):
        result = await flow.async_step_user(user_input={CONF_USER_CODE: "c"})
    assert result["errors"]["base"] == "login_error"  # type: ignore[index]
    assert "Re-authentication rejected (code 1): bad" in caplog.text
