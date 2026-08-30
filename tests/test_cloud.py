"""Unit tests for the cloud credential manager and helpers."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from homeassistant.exceptions import ConfigEntryNotReady
import pytest

from custom_components.tuya_ble.cloud import (
    HASSTuyaBLEDeviceManager,
    TokenRefreshListener,
    _build_credentials,
    _extract_functions,
    _extract_status_range,
)


class FakeHass:
    """Minimal HomeAssistant stand-in that records executor jobs."""

    def __init__(self) -> None:
        self.executed: list[Any] = []

    async def async_add_executor_job(self, func: Callable[..., Any], *args: Any) -> Any:
        """Record the function and run it synchronously."""
        self.executed.append(func)
        return func(*args)


def make_device(**overrides: Any) -> Any:
    """Build a fake CustomerDevice as a SimpleNamespace."""
    base = {
        "id": "deviceid",
        "uuid": "uuid",
        "local_key": "lc",
        "category": "wk",
        "product_id": "pid",
        "name": "Dev",
        "product_name": "Product",
        "function": {
            "c": SimpleNamespace(code="c", desc="d", name="n", type="t", values="v")
        },
        "status_range": {"s": SimpleNamespace(code="s", type="t", values="v")},
        "local_strategy": {
            1: {
                "status_code": "c",
                "config_item": {"valueType": "Boolean", "valueDesc": "{}"},
            },
            2: {
                "status_code": "s",
                "config_item": {
                    "valueType": "Integer",
                    "valueDesc": '{"min":0,"max":100}',
                },
            },
        },
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_extract_functions() -> None:
    """Assert that function specs are extracted as dictionaries."""
    dev = make_device()
    result = _extract_functions(dev)
    assert result == [{"code": "c", "dp_id": 1, "type": "Boolean", "values": "{}"}]


def test_extract_functions_empty() -> None:
    """Assert that an empty function map yields an empty list."""
    dev = make_device(function={})
    assert not _extract_functions(dev)


def test_extract_status_range() -> None:
    """Assert that status range specs are extracted as dictionaries."""
    dev = make_device()
    result = _extract_status_range(dev)
    assert result == [
        {
            "code": "s",
            "dp_id": 2,
            "type": "Integer",
            "values": '{"min":0,"max":100}',
        }
    ]


def test_extract_status_range_empty() -> None:
    """Assert that an empty status range map yields an empty list."""
    dev = make_device(status_range={})
    assert not _extract_status_range(dev)


def test_extract_captured_irrigation_local_strategy() -> None:
    """Map the captured timer's cloud status codes to its BLE DP IDs."""
    device = make_device(
        function={"normal_timer": SimpleNamespace()},
        status_range={
            "battery_percentage": SimpleNamespace(),
            "normal_timer": SimpleNamespace(),
        },
        local_strategy={
            11: {
                "status_code": "battery_percentage",
                "config_item": {
                    "valueType": "Integer",
                    "valueDesc": '{"unit":"%","min":0,"max":100}',
                },
            },
            101: {
                "status_code": "normal_timer",
                "config_item": {
                    "valueType": "String",
                    "valueDesc": '{"maxlen":255}',
                },
            },
        },
    )
    assert _extract_functions(device) == [
        {
            "code": "normal_timer",
            "dp_id": 101,
            "type": "String",
            "values": '{"maxlen":255}',
        }
    ]
    assert _extract_status_range(device) == [
        {
            "code": "battery_percentage",
            "dp_id": 11,
            "type": "Integer",
            "values": '{"unit":"%","min":0,"max":100}',
        },
        {
            "code": "normal_timer",
            "dp_id": 101,
            "type": "String",
            "values": '{"maxlen":255}',
        },
    ]


def test_build_credentials() -> None:
    """Assert that cloud device data is mapped onto credentials."""
    dev = make_device()
    creds = _build_credentials(dev)
    assert creds.uuid == "uuid"
    assert creds.local_key == "lc"
    assert creds.device_id == "deviceid"
    assert creds.category == "wk"
    assert creds.product_id == "pid"
    assert creds.device_name == "Dev"
    assert creds.product_model is None
    assert creds.product_name == "Product"
    assert creds.functions == [
        {"code": "c", "dp_id": 1, "type": "Boolean", "values": "{}"}
    ]
    assert creds.status_range == [
        {
            "code": "s",
            "dp_id": 2,
            "type": "Integer",
            "values": '{"min":0,"max":100}',
        }
    ]


def test_update_token() -> None:
    """Assert that updated tokens are written back to the data dict."""
    data: dict[str, Any] = {}
    listener = TokenRefreshListener(hass=None, data=data)  # type: ignore[arg-type]
    listener.update_token({"access_token": "abc"})
    assert data["access_token"] == "abc"


def _manager(response: Any = None, devices: Any = None) -> Any:
    """Build a manager wired to a fake cloud API."""
    hass = FakeHass()
    mgr = HASSTuyaBLEDeviceManager(hass, {})  # type: ignore[arg-type]
    fake = SimpleNamespace(
        device_map={d.id: d for d in devices} if devices else {},
        update_device_cache=lambda: None,
        customer_api=SimpleNamespace(get=lambda url: response),
    )
    mgr._manager = fake  # pylint: disable=protected-access
    mgr._hass = hass  # type: ignore[assignment]  # pylint: disable=protected-access
    return mgr


async def test_no_match_returns_none() -> None:
    """Assert that a non-matching advertised UUID yields no credentials."""
    device = make_device()
    mgr = _manager(devices=[device])
    with patch("custom_components.tuya_ble.cloud._LOGGER.warning") as warning:
        result = await mgr.get_device_credentials_by_uuid("other")
    assert result is None
    warning.assert_called_once_with(
        "No Tuya credentials found for UUID %s",
        "other",
    )


async def test_match_returns_credentials() -> None:
    """Assert that a matching decoded UUID yields cloud credentials."""
    device = make_device()
    mgr = _manager(devices=[device])
    result = await mgr.get_device_credentials_by_uuid("uuid")
    assert result is not None
    assert result.product_id == "pid"
    assert mgr.data == {}


async def test_force_update_calls_update_cache() -> None:
    """Assert that force_update refreshes the device cache."""
    device = make_device()
    mgr = _manager(devices=[device])
    result = await mgr.get_device_credentials_by_uuid(
        "uuid",
        force_update=True,
    )
    assert result is not None


def test_none_hass_raises() -> None:
    """Assert that a None hass is rejected at construction time."""
    with pytest.raises(ValueError):
        HASSTuyaBLEDeviceManager(hass=None, data={})  # type: ignore[arg-type]


async def test_initialize_builds_manager() -> None:
    """Assert that initialize() constructs a Manager and refreshes the cache."""
    hass = FakeHass()
    data: dict[str, Any] = {
        "token_info": {"t": 1},
        "user_code": "uc",
        "terminal_id": "tid",
        "endpoint": "ep",
    }
    mgr = HASSTuyaBLEDeviceManager(hass, data)  # type: ignore[arg-type]
    fake_manager = MagicMock()
    fake_manager.update_device_cache = MagicMock()
    fake_manager.device_map = {}
    with patch(
        "custom_components.tuya_ble.cloud.Manager", return_value=fake_manager
    ) as m:
        await mgr.initialize()
    m.assert_called_once()
    assert mgr._manager is fake_manager  # pylint: disable=protected-access
    fake_manager.update_device_cache.assert_called_once()


async def test_get_device_credentials_by_uuid_lazy_init() -> None:
    """Assert that UUID credential lookup initializes when needed."""
    hass = FakeHass()
    mgr = HASSTuyaBLEDeviceManager(hass, {})  # type: ignore[arg-type]
    fake_manager = MagicMock()
    fake_manager.update_device_cache = MagicMock()
    fake_manager.device_map = {}
    with (
        patch("custom_components.tuya_ble.cloud.Manager", return_value=fake_manager),
        patch("custom_components.tuya_ble.cloud._LOGGER.warning") as warning,
    ):
        result = await mgr.get_device_credentials_by_uuid("missing")
    assert result is None
    assert mgr._manager is fake_manager  # pylint: disable=protected-access
    fake_manager.update_device_cache.assert_called_once()
    warning.assert_called_once_with(
        "No Tuya credentials found for UUID %s",
        "missing",
    )


async def test_get_device_credentials_by_uuid_no_manager_raises() -> None:
    """Assert that a failed initialization raises ConfigEntryNotReady."""
    hass = FakeHass()
    mgr = HASSTuyaBLEDeviceManager(hass, {})  # type: ignore[arg-type]

    async def noop_initialize() -> None:
        """Leave _manager as None."""

    with (
        patch.object(mgr, "initialize", new=noop_initialize),
        pytest.raises(ConfigEntryNotReady),
    ):
        await mgr.get_device_credentials_by_uuid("uuid")
