"""Unit tests for the cloud credential manager and helpers."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
import pytest

from custom_components.tuya_ble.cloud import (
    HASSTuyaBLEDeviceManager,
    TokenRefreshListener,
    _build_credentials,
    _extract_functions,
    _extract_status_range,
    capture_cloud_info,
    capture_local_schema,
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
    assert creds.cloud_info is not None
    assert creds.cloud_info["captured_at"].endswith("+00:00")
    assert creds.cloud_info["category"] == "wk"


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


def test_build_credentials_captures_local_schema() -> None:
    """Credentials carry the full unfiltered schema for diagnostics."""
    creds = _build_credentials(make_device())

    assert creds.local_schema is not None
    assert creds.local_schema["captured_at"].endswith("+00:00")
    # Sorted by dp id and flattened with the dp id promoted to a key.
    assert [item["dp_id"] for item in creds.local_schema["local_strategy"]] == [1, 2]
    assert creds.local_schema["local_strategy"][0]["config_item"] == {
        "valueType": "Boolean",
        "valueDesc": "{}",
    }
    assert creds.local_schema["status_range"] == [
        {"code": "s", "type": "t", "values": "v", "report_type": None}
    ]


def test_capture_local_schema_keeps_fields_the_runtime_lists_drop() -> None:
    """statusFormat, enumMappingMap and valueConvert survive the capture."""
    device = make_device(
        local_strategy={
            1: {
                "value_convert": {"scale": 10},
                "status_code": "c",
                "config_item": {
                    "statusFormat": "value",
                    "valueType": "Integer",
                    "valueDesc": '{"scale":10}',
                    "enumMappingMap": {"0": "off"},
                    "pid": "product",
                },
            }
        },
        status_range={
            "c": SimpleNamespace(
                code="c", type="Integer", values="{}", report_type="sum"
            )
        },
    )

    schema = capture_local_schema(device)

    entry = schema["local_strategy"][0]
    assert entry["dp_id"] == 1
    assert entry["value_convert"] == {"scale": 10}
    assert entry["config_item"]["statusFormat"] == "value"
    assert entry["config_item"]["enumMappingMap"] == {"0": "off"}
    assert entry["config_item"]["pid"] == "product"
    assert schema["status_range"][0]["report_type"] == "sum"


def test_capture_local_schema_without_strategy_or_status_range() -> None:
    """A device reporting no schema captures an empty, well-formed blob."""
    device = make_device(local_strategy={}, status_range={})

    schema = capture_local_schema(device)

    assert schema["local_strategy"] == []
    assert schema["status_range"] == []


def test_capture_local_schema_tolerates_missing_attributes() -> None:
    """A device object missing the strategy attributes still yields a blob."""
    schema = capture_local_schema(SimpleNamespace())

    assert schema["local_strategy"] == []
    assert schema["status_range"] == []


def test_capture_cloud_info_keeps_scalars_and_version_fields() -> None:
    """Scalar cloud fields, including version metadata, are snapshotted."""
    device = make_device(
        online=True,
        ip="10.0.0.5",
        time_zone="Europe/London",
        pv="1.0.5",
        active_time=123,
    )

    info = capture_cloud_info(device)

    assert info["captured_at"].endswith("+00:00")
    assert info["online"] is True
    assert info["ip"] == "10.0.0.5"
    assert info["pv"] == "1.0.5"
    assert info["active_time"] == 123


def test_capture_cloud_info_drops_secrets_and_blobs() -> None:
    """The local key and the schema blobs captured elsewhere are excluded."""
    device = make_device()

    info = capture_cloud_info(device)

    assert "local_key" not in info
    assert "function" not in info
    assert "status_range" not in info
    assert "local_strategy" not in info


def test_capture_cloud_info_keeps_scalar_lists_only() -> None:
    """Simple scalar lists survive, while nested or complex values are dropped."""
    device = make_device(tags=["a", "b"], nested={"k": "v"}, objects=[object()])

    info = capture_cloud_info(device)

    assert info["tags"] == ["a", "b"]
    assert "nested" not in info
    assert "objects" not in info


async def test_get_cloud_device_by_uuid_returns_raw_device() -> None:
    """The raw cloud device is returned without building credentials."""
    device = make_device()
    mgr = _manager(devices=[device, make_device(id="other-id", uuid="other")])

    assert await mgr.get_cloud_device_by_uuid("uuid") is device
    assert await mgr.get_cloud_device_by_uuid("nope") is None


async def test_get_cloud_device_by_uuid_force_update_refreshes_cache() -> None:
    """force_update asks the SDK to refresh its device cache first."""
    mgr = _manager()
    refreshes: list[int] = []
    mgr._manager.update_device_cache = (  # pylint: disable=protected-access
        lambda: refreshes.append(1)
    )

    assert await mgr.get_cloud_device_by_uuid("uuid", force_update=True) is None

    assert refreshes == [1]
    # Without force_update the cached device map is left alone.
    assert await mgr.get_cloud_device_by_uuid("uuid") is None
    assert refreshes == [1]


async def test_get_cloud_device_by_uuid_initializes_manager() -> None:
    """A manager that was never initialized builds itself on first use."""
    device = make_device()
    mgr = HASSTuyaBLEDeviceManager(cast(HomeAssistant, FakeHass()), {})
    mgr._manager = None  # pylint: disable=protected-access

    async def fake_initialize() -> None:
        mgr._manager = SimpleNamespace(device_map={"a": device})  # pylint: disable=protected-access

    with patch.object(mgr, "initialize", new=fake_initialize):
        assert await mgr.get_cloud_device_by_uuid("uuid") is device
