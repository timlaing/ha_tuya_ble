"""Unit tests for the cloud credential manager and helpers."""

from __future__ import annotations

from collections.abc import Callable
import logging
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
import pytest

from custom_components.tuya_ble.cloud import (
    _REDACTED_UNLOCK_CHECK,
    TUYA_SHARING_LOGGER,
    HASSTuyaBLEDeviceManager,
    TokenRefreshListener,
    _build_credentials,
    _extract_functions,
    _extract_status_range,
    _redact_unlock_check,
    _UnlockCheckRedactionFilter,
    capture_cloud_fields,
    capture_cloud_info,
    capture_local_schema,
    filter_cloud_info,
    install_unlock_check_redaction,
)
from tests.test_lock_handlers import MEMBER1_CHECK


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


def test_capture_cloud_info_keeps_allowlisted_version_fields() -> None:
    """Allowlisted product/version fields, and only those, are snapshotted."""
    device = make_device(
        online=True,
        pv="1.0.5",
        mcu_sw_version="1.2.3",
        sw_version="4.5.6",
        product_model="abcd",
        sub=False,
        support_local=True,
    )

    info = capture_cloud_info(device)

    assert info["captured_at"].endswith("+00:00")
    assert info["category"] == "wk"
    assert info["product_id"] == "pid"
    assert info["product_name"] == "Product"
    assert info["online"] is True
    assert info["pv"] == "1.0.5"
    assert info["mcu_sw_version"] == "1.2.3"
    assert info["sw_version"] == "4.5.6"
    assert info["product_model"] == "abcd"
    assert info["sub"] is False
    assert info["support_local"] is True


def test_capture_cloud_info_drops_identifying_fields() -> None:
    """Network, location and owner identifiers from the raw payload are dropped."""
    device = make_device(
        ip="10.0.0.5",
        time_zone="Europe/London",
        asset_id="asset",
        lat=51.5,
        lon=-0.1,
        uid="user-id",
        owner_id="owner-id",
        active_time=123,
    )

    info = capture_cloud_info(device)

    for key in (
        "ip",
        "time_zone",
        "asset_id",
        "lat",
        "lon",
        "uid",
        "owner_id",
        "active_time",
    ):
        assert key not in info


def test_capture_cloud_info_drops_secrets_and_blobs() -> None:
    """The local key and the schema blobs captured elsewhere are excluded."""
    device = make_device()

    info = capture_cloud_info(device)

    assert "local_key" not in info
    assert "function" not in info
    assert "status_range" not in info
    assert "local_strategy" not in info


def test_capture_cloud_info_drops_credential_shaped_keys() -> None:
    """Any credential-looking key is dropped, not just the known local key."""
    device = make_device(auth_token="tok", device_secret="sec", product_key="pk")

    info = capture_cloud_info(device)

    assert "auth_token" not in info
    assert "device_secret" not in info
    assert "product_key" not in info


def test_capture_cloud_info_drops_unlisted_and_complex_fields() -> None:
    """Unknown fields and non-scalar values never reach the snapshot."""
    device = make_device(tags=["a", "b"], nested={"k": "v"}, objects=[object()])

    info = capture_cloud_info(device)

    assert "tags" not in info
    assert "nested" not in info
    assert "objects" not in info


def test_capture_cloud_info_drops_allowlisted_non_scalar() -> None:
    """An allowlisted key carrying a non-scalar value is still dropped."""
    device = make_device(online={"unexpected": "dict"})

    info = capture_cloud_info(device)

    assert "online" not in info


def test_filter_cloud_info_strips_stored_snapshots() -> None:
    """Stored snapshots are reduced to the allowlisted export fields."""
    stored = {
        "captured_at": "2026-09-30T00:00:00+00:00",
        "online": True,
        "pv": "1.0.5",
        "ip": "10.0.0.5",
        "asset_id": "asset",
        "uid": "user-id",
    }

    filtered = filter_cloud_info(stored)

    assert filtered == {
        "captured_at": "2026-09-30T00:00:00+00:00",
        "online": True,
        "pv": "1.0.5",
    }


def test_filter_cloud_info_tolerates_empty() -> None:
    """An empty stored snapshot stays empty."""
    assert filter_cloud_info({}) == {}


def test_filter_cloud_info_drops_non_scalar_values() -> None:
    """Allowlisted keys holding non-scalars are dropped from stored snapshots.

    The pre-allowlist capture could store scalar-only lists, so a stored
    snapshot must be reduced to scalars on export too.
    """
    stored = {
        "captured_at": "2026-09-30T00:00:00+00:00",
        "online": True,
        "pv": ["1.0.5", "1.0.6"],
        "category": {"nested": "dict"},
    }

    filtered = filter_cloud_info(stored)

    assert filtered == {
        "captured_at": "2026-09-30T00:00:00+00:00",
        "online": True,
    }


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


class _Opaque:
    """A non-JSON value of a type the SDK might attach to a device."""


def test_capture_cloud_fields_names_every_field_but_withholds_values() -> None:
    """Every raw field is listed; only allowlisted scalars keep their value."""
    device = make_device(
        sec_key="fedcba9876543210",
        online=True,
        pv="2.1",
        product_model=["not", "scalar"],
        lat=12.5,
        sub=None,
        is_shared=False,
        opaque=_Opaque(),
        local_key="0123456789abcdef",
    )
    fields = capture_cloud_fields(device)

    assert list(fields) == sorted(vars(device))
    assert fields["online"] is True
    assert fields["pv"] == "2.1"
    assert fields["sub"] is None
    assert fields["product_model"] == "<redacted list, 2 items>"
    assert fields["sec_key"] == "<redacted str, 16 chars>"
    assert fields["local_key"] == "<redacted str, 16 chars>"
    assert fields["lat"] == "<redacted number>"
    assert fields["is_shared"] == "<redacted bool>"
    assert fields["opaque"] == "<redacted _Opaque>"
    assert fields["function"] == "<redacted dict, 1 items>"
    assert "fedcba9876543210" not in repr(fields)
    assert "0123456789abcdef" not in repr(fields)


def test_capture_cloud_fields_describes_unlisted_none() -> None:
    """A field the cloud sends as null is named without exposing a value."""
    assert capture_cloud_fields(make_device(owner_id=None))["owner_id"] == (
        "<redacted none>"
    )


def test_capture_cloud_info_includes_field_map() -> None:
    """The stored snapshot carries the redacted field map."""
    info = capture_cloud_info(make_device(sec_key="fedcba9876543210"))
    assert info["fields"]["sec_key"] == "<redacted str, 16 chars>"
    assert filter_cloud_info(info)["fields"] == info["fields"]


def test_filter_cloud_info_rechecks_stored_field_map() -> None:
    """Stored field maps cannot smuggle values past the allowlist on export."""
    snapshot = {
        "fields": {
            "pv": "2.1",
            "local_key": "0123456789abcdef",
            "sec_key": "<redacted str, 16 chars>",
            "status": "<redacted dict, 9 items>",
            "uid": "<redacted str, 16 chars> plus a value",
            "online": {"nested": True},
        }
    }
    assert filter_cloud_info(snapshot)["fields"] == {
        "pv": "2.1",
        "local_key": "<redacted>",
        "sec_key": "<redacted str, 16 chars>",
        "status": "<redacted dict, 9 items>",
        "uid": "<redacted>",
        "online": "<redacted>",
    }


def test_filter_cloud_info_drops_malformed_field_map() -> None:
    """A field map that is not a mapping is not exported."""
    assert "fields" not in filter_cloud_info({"fields": ["sec_key"]})


# --- tuya_sharing log redaction (ble_unlock_check) -------------------------


def _check_record(message: str, *args: Any) -> logging.LogRecord:
    """Build a LogRecord the way the SDK's own logger would."""
    return logging.LogRecord(
        "tuya_sharing", logging.DEBUG, "customerapi.py", 1, message, args, None
    )


def test_redact_unlock_check_replaces_field_and_keeps_rest() -> None:
    """The check under its key is masked while the rest of the record stays."""
    check = MEMBER1_CHECK
    message = (
        "response ret = {'code': 0, 'success': True, "
        f"'result': {{'ble_unlock_check': '{check}', 'id': 'device123'}}}}"
    )
    redacted = _redact_unlock_check(message)
    assert _REDACTED_UNLOCK_CHECK in redacted
    assert check not in redacted
    assert "'code': 0" in redacted
    assert "'id': 'device123'" in redacted


def test_redact_unlock_check_masks_only_valid_bare_tokens() -> None:
    """A bare token is masked only when it decodes as a real unlock check."""
    assert _redact_unlock_check(f"payload {MEMBER1_CHECK} end") == (
        f"payload {_REDACTED_UNLOCK_CHECK} end"
    )
    # Too short to be a check, and a plausible-but-invalid base64 blob.
    assert _redact_unlock_check("token abcdefghij") == "token abcdefghij"
    invalid = "A" * 24
    assert _redact_unlock_check(invalid) == invalid


def test_unlock_check_filter_redacts_record_in_place() -> None:
    """The record keeps its level and logger but carries the masked message."""
    record = _check_record(
        "response ret = %s", f"{{'ble_unlock_check': '{MEMBER1_CHECK}'}}"
    )
    filter_ = _UnlockCheckRedactionFilter()
    assert filter_.filter(record) is True
    assert record.levelno == logging.DEBUG
    assert record.name == "tuya_sharing"
    assert MEMBER1_CHECK not in record.getMessage()
    assert _REDACTED_UNLOCK_CHECK in record.getMessage()
    # Args are folded into the message so no handler can re-expand them.
    assert record.args == ()


def test_unlock_check_filter_keeps_clean_records_untouched() -> None:
    """A record without a check is returned untouched, args included."""
    record = _check_record("found %d devices", 3)
    assert _UnlockCheckRedactionFilter().filter(record) is True
    assert record.msg == "found %d devices"
    assert record.args == (3,)
    assert record.getMessage() == "found 3 devices"


def test_unlock_check_filter_never_drops_records_that_cannot_format() -> None:
    """A record no handler could format is still passed through."""
    record = _check_record("%(missing)s")
    record.args = ({"other": 1},)
    assert _UnlockCheckRedactionFilter().filter(record) is True
    assert record.msg == "%(missing)s"


def test_install_unlock_check_redaction_is_idempotent() -> None:
    """Installing twice leaves exactly one filter on the shared SDK logger."""
    sdk_logger = logging.getLogger(TUYA_SHARING_LOGGER)
    original = list(sdk_logger.filters)
    try:
        sdk_logger.filters.clear()
        install_unlock_check_redaction()
        install_unlock_check_redaction()
        assert (
            sum(
                isinstance(item, _UnlockCheckRedactionFilter)
                for item in sdk_logger.filters
            )
            == 1
        )
    finally:
        sdk_logger.filters[:] = original


def test_tuya_sharing_debug_output_is_redacted_for_every_handler() -> None:
    """The SDK's own handler sees the masked message, not the raw check."""
    sdk_logger = logging.getLogger(TUYA_SHARING_LOGGER)
    captured: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            """Record the formatted output of this handler."""
            captured.append(self.format(record))

    handler = _Capture()
    handler.setFormatter(logging.Formatter("%(message)s"))
    sdk_logger.addHandler(handler)
    sdk_logger.setLevel(logging.DEBUG)
    original_filters = list(sdk_logger.filters)
    try:
        sdk_logger.debug("response ret = %s", {"ble_unlock_check": MEMBER1_CHECK})
    finally:
        sdk_logger.removeHandler(handler)
        sdk_logger.filters[:] = original_filters
    assert len(captured) == 1
    assert MEMBER1_CHECK not in captured[0]
    assert _REDACTED_UNLOCK_CHECK in captured[0]
