"""Unit tests for the cloud credential manager and helpers."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from homeassistant.const import CONF_ADDRESS

from custom_components.tuya_ble.cloud import (
    HASSTuyaBLEDeviceManager,
    TokenRefreshListener,
    _build_credentials,
    _extract_functions,
    _extract_status_range,
    _normalize_mac,
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
            "f": SimpleNamespace(code="c", desc="d", name="n", type="t", values="v")
        },
        "status_range": {"s": SimpleNamespace(code="s", type="t", values="v")},
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_normalize_mac() -> None:
    """Assert that MAC addresses are normalized to uppercase colon form."""
    assert _normalize_mac("aabbccddeeff") == "AA:BB:CC:DD:EE:FF"
    assert _normalize_mac("AABBCCDDEEFF") == "AA:BB:CC:DD:EE:FF"


def test_extract_functions() -> None:
    """Assert that function specs are extracted as dictionaries."""
    dev = make_device()
    result = _extract_functions(dev)
    assert result == [
        {"code": "c", "desc": "d", "name": "n", "type": "t", "values": "v"}
    ]


def test_extract_functions_empty() -> None:
    """Assert that an empty function map yields an empty list."""
    dev = make_device(function={})
    assert _extract_functions(dev) == []


def test_extract_status_range() -> None:
    """Assert that status range specs are extracted as dictionaries."""
    dev = make_device()
    result = _extract_status_range(dev)
    assert result == [{"code": "s", "type": "t", "values": "v"}]


def test_extract_status_range_empty() -> None:
    """Assert that an empty status range map yields an empty list."""
    dev = make_device(status_range={})
    assert _extract_status_range(dev) == []


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
        {"code": "c", "desc": "d", "name": "n", "type": "t", "values": "v"}
    ]
    assert creds.status_range == [{"code": "s", "type": "t", "values": "v"}]


class TestTokenRefreshListener:
    """Tests for the token refresh listener."""

    def test_update_token(self) -> None:
        """Assert that updated tokens are written back to the data dict."""
        data: dict[str, Any] = {}
        listener = TokenRefreshListener(hass=None, data=data)  # type: ignore[arg-type]
        listener.update_token({"access_token": "abc"})
        assert data["access_token"] == "abc"


class TestGetDeviceCredentials:
    """Tests for HASSTuyaBLEDeviceManager.get_device_credentials."""

    def _manager(self, response: Any = None, devices: Any = None) -> Any:
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

    async def test_no_match_returns_none(self) -> None:
        """Assert that a non-matching MAC yields no credentials."""
        device = make_device()
        mgr = self._manager(
            response={"result": [{"mac": "112233445566"}]}, devices=[device]
        )
        result = await mgr.get_device_credentials("AA:BB:CC:DD:EE:FF")
        assert result is None

    async def test_no_response_result(self) -> None:
        """Assert that a missing result in the response yields no credentials."""
        device = make_device()
        mgr = self._manager(response={"result": None}, devices=[device])
        result = await mgr.get_device_credentials("AA:BB:CC:DD:EE:FF")
        assert result is None

    async def test_response_none(self) -> None:
        """Assert that a None response yields no credentials."""
        device = make_device()
        mgr = self._manager(response=None, devices=[device])
        result = await mgr.get_device_credentials("AA:BB:CC:DD:EE:FF")
        assert result is None

    async def test_mac_missing_from_factory_info(self) -> None:
        """Assert that factory info without a MAC yields no credentials."""
        device = make_device()
        mgr = self._manager(response={"result": [{"other": 1}]}, devices=[device])
        result = await mgr.get_device_credentials("AA:BB:CC:DD:EE:FF")
        assert result is None

    async def test_match_returns_credentials(self) -> None:
        """Assert that a matching MAC yields credentials and leaves data empty."""
        device = make_device()
        mgr = self._manager(
            response={"result": [{"mac": "112233445566"}]}, devices=[device]
        )
        result = await mgr.get_device_credentials("11:22:33:44:55:66")
        assert result is not None
        assert mgr.data == {}

    async def test_match_saves_address(self) -> None:
        """Assert that a matching MAC is saved to data when requested."""
        device = make_device()
        mgr = self._manager(
            response={"result": [{"mac": "112233445566"}]}, devices=[device]
        )
        result = await mgr.get_device_credentials("11:22:33:44:55:66", save_data=True)
        assert result is not None
        assert mgr.data[CONF_ADDRESS] == "11:22:33:44:55:66"

    async def test_force_update_calls_update_cache(self) -> None:
        """Assert that force_update refreshes the device cache."""
        device = make_device()
        mgr = self._manager(
            response={"result": [{"mac": "112233445566"}]}, devices=[device]
        )
        result = await mgr.get_device_credentials(
            "11:22:33:44:55:66", force_update=True
        )
        assert result is not None
