"""Tests for custom_components/tuya_ble/diagnostics.py."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tuya_ble.const import (
    CONF_CATEGORY,
    CONF_CLOUD_INFO,
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_ENDPOINT,
    CONF_FUNCTIONS,
    CONF_LOCAL_KEY,
    CONF_LOCAL_SCHEMA,
    CONF_PRODUCT_ID,
    CONF_STATUS_RANGE,
    CONF_TERMINAL_ID,
    CONF_TOKEN_INFO,
    CONF_USER_CODE,
    CONF_UUID,
    DOMAIN,
)
from custom_components.tuya_ble.devices import TuyaBLEData
from custom_components.tuya_ble.diagnostics import (
    async_get_config_entry_diagnostics,
    async_get_device_diagnostics,
)
from custom_components.tuya_ble.tuya_ble import TuyaBLEDataPointType, TuyaBLEDevice

from .conftest import FakeBLEManager, make_credentials

# A local strategy entry carrying the fields the filtered runtime lists throw
# away: statusFormat, enumMappingMap and valueConvert.
_STRATEGY = {
    1: {
        "value_convert": {"scale": 10, "ratio": 1},
        "status_code": "switch_1",
        "config_item": {
            "statusFormat": "bool",
            "valueDesc": "{}",
            "valueType": "Boolean",
            "enumMappingMap": {},
            "pid": "product",
        },
    },
    2: {
        "value_convert": {"scale": 0},
        "status_code": "count",
        "config_item": {
            "statusFormat": "value",
            "valueDesc": '{"min":0,"max":100,"scale":10,"step":1}',
            "valueType": "Integer",
            "enumMappingMap": {"0": "off", "1": "on"},
            "pid": "product",
        },
    },
}

_STATUS_RANGE = {
    "switch_1": SimpleNamespace(
        code="switch_1", type="Boolean", values="{}", report_type="minux"
    ),
    "count": SimpleNamespace(
        code="count", type="Integer", values="{}", report_type="sum"
    ),
}

_CLOUD_DEVICE = SimpleNamespace(
    uuid="1234567890abcdef",
    local_strategy=_STRATEGY,
    status_range=_STATUS_RANGE,
)


def _schema(captured_at: str = "2026-09-30T00:00:00+00:00") -> dict[str, Any]:
    """Return a stored schema blob as the config flow would have persisted it."""
    return {
        "captured_at": captured_at,
        "local_strategy": [
            {"dp_id": dp_id, **details} for dp_id, details in sorted(_STRATEGY.items())
        ],
        "status_range": [
            {
                "code": spec.code,
                "type": spec.type,
                "values": spec.values,
                "report_type": spec.report_type,
            }
            for spec in _STATUS_RANGE.values()
        ],
    }


def _entry_data() -> dict[str, Any]:
    """Return config entry data holding a captured schema."""
    return {
        "address": "AA:BB:CC:DD:EE:FF",
        CONF_UUID: "1234567890abcdef",
        CONF_LOCAL_KEY: "not-a-real-key",
        CONF_DEVICE_ID: "device123",
        CONF_CATEGORY: "wk",
        CONF_PRODUCT_ID: "drlajpqc",
        CONF_DEVICE_NAME: "Device",
        CONF_FUNCTIONS: [{"code": "switch_1", "dp_id": 1}],
        CONF_STATUS_RANGE: [{"code": "switch_1", "dp_id": 1}],
        CONF_LOCAL_SCHEMA: _schema(),
    }


def _add_entry(
    hass: HomeAssistant,
    data: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
) -> MockConfigEntry:
    """Register a config entry, optionally with options set."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Device",
        data=data if data is not None else _entry_data(),
        options=options or {},
        entry_id="entry1",
    )
    entry.add_to_hass(hass)
    return entry


def _live_device(hass: HomeAssistant, entry: MockConfigEntry) -> Any:
    """Install a device carrying two data points into hass.data for the entry."""
    device = TuyaBLEDevice(
        FakeBLEManager(make_credentials()),
        cast(Any, SimpleNamespace(address="AA:BB:CC:DD:EE:FF", name="TestDevice")),
        cast(Any, SimpleNamespace(rssi=-60, service_data={}, manufacturer_data={})),
    )
    # Typed values are what the protocol layer produces once decoded.
    device.datapoints.update_from_device(1, 0.0, 0, TuyaBLEDataPointType.DT_BOOL, True)
    device.datapoints.update_from_device(2, 0.0, 0, TuyaBLEDataPointType.DT_VALUE, 10)
    device.datapoints.update_from_device(
        3,
        0.0,
        0,
        TuyaBLEDataPointType.DT_RAW,
        b"\xab\xcd",
        raw_value=b"\xab\xcd\x00",
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = TuyaBLEData(
        title="Device",
        device=device,
        product=MagicMock(),
        manager=MagicMock(),
        coordinator=MagicMock(),
    )
    return device


async def test_config_entry_diagnostics_shape(hass: HomeAssistant) -> None:
    """Both diagnostics endpoints return the documented sections."""
    entry = _add_entry(hass)

    payload = await async_get_config_entry_diagnostics(hass, entry)

    assert set(payload) == {
        "schema_captured_at",
        "schema_source",
        "device",
        "live",
        "cloud_info",
        "cloud_schema",
        "descriptor",
        "identity",
        "home_assistant",
    }
    assert payload["schema_source"] == "stored"
    assert payload["cloud_info"] == {}
    assert payload["schema_captured_at"] == "2026-09-30T00:00:00+00:00"
    assert payload["identity"]["uuid"] == "1234567890abcdef"
    assert payload["descriptor"]["resolved"] is True
    assert payload["home_assistant"]["devices"] == []


async def test_diagnostics_export_full_unfiltered_strategy(hass: HomeAssistant) -> None:
    """The full local strategy survives, including the fields the runtime drops."""
    entry = _add_entry(hass)

    payload = await async_get_config_entry_diagnostics(hass, entry)

    strategy = payload["cloud_schema"]["local_strategy"]
    assert [item["dp_id"] for item in strategy] == [1, 2]
    assert strategy[1]["config_item"]["valueDesc"] == (
        '{"min":0,"max":100,"scale":10,"step":1}'
    )
    assert strategy[1]["config_item"]["enumMappingMap"] == {"0": "off", "1": "on"}
    assert strategy[1]["value_convert"] == {"scale": 0}
    # report_type only ever exists on the captured schema.
    report_types = {
        item["code"]: item["report_type"]
        for item in payload["cloud_schema"]["status_range"]
    }
    assert report_types == {"switch_1": "minux", "count": "sum"}
    assert payload["cloud_schema"]["function"] == [{"code": "switch_1", "dp_id": 1}]


async def test_diagnostics_reports_descriptor_entities(hass: HomeAssistant) -> None:
    """The descriptor section lists the platform/dp mappings this product uses."""
    entry = _add_entry(hass)

    payload = await async_get_config_entry_diagnostics(hass, entry)

    entities = payload["descriptor"]["entities"]
    assert "switch" in entities
    switch = next(item for item in entities["switch"] if item["dp_id"] == 8)
    assert switch["translation_key"] == "window_check"
    assert switch["dp_type"] is None
    assert payload["descriptor"]["model_name"]


async def test_diagnostics_for_unresolvable_product(hass: HomeAssistant) -> None:
    """A product with no descriptor is reported instead of raising."""
    data = _entry_data() | {CONF_PRODUCT_ID: "not-a-product"}
    entry = _add_entry(hass, data=data)

    payload = await async_get_config_entry_diagnostics(hass, entry)

    assert payload["descriptor"] == {"resolved": False}


async def test_diagnostics_report_live_device_and_datapoints(
    hass: HomeAssistant,
) -> None:
    """Live data points are exported with their type, value and raw bytes."""
    entry = _add_entry(hass)
    device = _live_device(hass, entry)

    payload = await async_get_config_entry_diagnostics(hass, entry)

    assert payload["device"]["loaded"] is True
    assert payload["device"]["address"] == "AA:BB:CC:DD:EE:FF"
    assert payload["device"]["advertised_name"] == "TestDevice"
    assert payload["device"]["rssi"] == -60
    assert set(payload["live"]) == {"1", "2", "3"}
    assert payload["live"]["1"]["type"] == "DT_BOOL"
    assert payload["live"]["1"]["value"] is True
    assert payload["live"]["1"]["raw"] is None
    assert payload["live"]["2"]["value"] == 10
    # Byte values stay JSON-safe, and the verbatim payload is kept.
    assert payload["live"]["3"]["value"] == "abcd"
    assert payload["live"]["3"]["raw"] == "abcd00"
    assert payload["live"]["1"]["changed_by_device"] is False
    assert isinstance(device.datapoints.values(), list)


async def test_diagnostics_without_loaded_device(hass: HomeAssistant) -> None:
    """An unloaded entry still produces a payload with no live data."""
    entry = _add_entry(hass)

    payload = await async_get_config_entry_diagnostics(hass, entry)

    assert payload["device"] == {"loaded": False}
    assert payload["live"] == {}


async def test_device_diagnostics_scopes_to_that_device(hass: HomeAssistant) -> None:
    """Device diagnostics report only the requested registry device."""
    entry = _add_entry(hass)
    registry = dr.async_get(hass)
    device = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "1234567890abcdef")},
        name="Device",
    )

    payload = await async_get_device_diagnostics(hass, entry, device)

    assert payload["home_assistant"]["device"]["name"] == "Device"
    assert "devices" not in payload["home_assistant"]
    assert payload["schema_source"] == "stored"


async def test_diagnostics_redact_secrets(hass: HomeAssistant) -> None:
    """No stored credential may appear in a diagnostics download."""
    data = _entry_data()
    data[CONF_ENDPOINT] = "https://secret.example.com"
    data[CONF_TERMINAL_ID] = "terminal-1234"
    data[CONF_USER_CODE] = "user-code-xyz"
    entry = _add_entry(hass, data=data)

    payload = await async_get_config_entry_diagnostics(hass, entry)

    serialized = repr(payload)
    assert "not-a-real-key" not in serialized
    assert "secret.example.com" not in serialized
    assert "terminal-1234" not in serialized
    assert "user-code-xyz" not in serialized


async def test_diagnostics_redact_token_blob(hass: HomeAssistant) -> None:
    """Re-auth tokens are redacted even when nested inside options-derived data."""
    entry = _add_entry(
        hass,
        options={
            CONF_TOKEN_INFO: {
                "t": "tok-t",
                "uid": "uid-1",
                "access_token": "access-abc",
                "refresh_token": "refresh-abc",
            },
            CONF_ENDPOINT: "https://secret.example.com",
            CONF_TERMINAL_ID: "terminal-1234",
            CONF_USER_CODE: "user-code-xyz",
        },
    )

    payload = await async_get_config_entry_diagnostics(hass, entry)

    serialized = repr(payload)
    assert "access-abc" not in serialized
    assert "refresh-abc" not in serialized
    assert "tok-t" not in serialized
    assert "secret.example.com" not in serialized


async def test_diagnostics_refresh_from_reauth_tokens(hass: HomeAssistant) -> None:
    """With re-auth tokens on hand the schema is re-fetched from the cloud."""
    entry = _add_entry(
        hass,
        options={
            CONF_TOKEN_INFO: {"access_token": "access-abc"},
            CONF_ENDPOINT: "https://endpoint.example.com",
        },
    )
    captured = _schema("2026-09-30T12:00:00+00:00")

    with (
        patch(
            "custom_components.tuya_ble.diagnostics.capture_local_schema",
            return_value=captured,
        ) as capture,
        patch(
            "custom_components.tuya_ble.diagnostics.HASSTuyaBLEDeviceManager"
        ) as manager_cls,
    ):
        manager = manager_cls.return_value
        manager.get_cloud_device_by_uuid = AsyncMock(return_value=_CLOUD_DEVICE)
        payload = await async_get_config_entry_diagnostics(hass, entry)

    manager.get_cloud_device_by_uuid.assert_awaited_once_with(
        "1234567890abcdef", force_update=True
    )
    capture.assert_called_once_with(_CLOUD_DEVICE)
    assert payload["schema_source"] == "cloud_refresh"
    assert payload["schema_captured_at"] == "2026-09-30T12:00:00+00:00"
    assert len(payload["cloud_schema"]["local_strategy"]) == 2


async def test_diagnostics_do_not_persist_refreshed_tokens(hass: HomeAssistant) -> None:
    """The refresh path must not write the tokens it just used."""
    entry = _add_entry(
        hass,
        options={
            CONF_TOKEN_INFO: {"access_token": "access-abc"},
        },
    )
    before = dict(entry.options)

    with (
        patch(
            "custom_components.tuya_ble.diagnostics.capture_local_schema",
            return_value=_schema(),
        ),
        patch(
            "custom_components.tuya_ble.diagnostics.HASSTuyaBLEDeviceManager"
        ) as manager_cls,
    ):
        manager_cls.return_value.get_cloud_device_by_uuid = AsyncMock(
            return_value=_CLOUD_DEVICE
        )
        with patch.object(hass.config_entries, "async_update_entry") as update:
            await async_get_config_entry_diagnostics(hass, entry)

    update.assert_not_called()
    assert entry.options == before


@pytest.mark.parametrize(
    ("failure", "expected_debug"),
    [
        (RuntimeError("boom"), "Diagnostics schema refresh failed"),
        (None, "found no matching cloud device"),
    ],
)
async def test_diagnostics_fall_back_when_refresh_fails(
    hass: HomeAssistant, failure: Exception | None, expected_debug: str, caplog: Any
) -> None:
    """A failed refresh silently falls back to the stored schema."""
    entry = _add_entry(
        hass,
        options={CONF_TOKEN_INFO: {"access_token": "access-abc"}},
    )

    with (
        patch("custom_components.tuya_ble.diagnostics.capture_local_schema") as capture,
        patch(
            "custom_components.tuya_ble.diagnostics.HASSTuyaBLEDeviceManager"
        ) as manager_cls,
    ):
        manager = manager_cls.return_value
        manager.get_cloud_device_by_uuid = AsyncMock(
            side_effect=failure, return_value=None
        )
        with caplog.at_level(logging.DEBUG):
            payload = await async_get_config_entry_diagnostics(hass, entry)

    capture.assert_not_called()
    assert payload["schema_source"] == "stored"
    assert len(payload["cloud_schema"]["local_strategy"]) == 2
    assert expected_debug in caplog.text


async def test_diagnostics_without_tokens_never_call_the_cloud(
    hass: HomeAssistant,
) -> None:
    """Without re-auth tokens no cloud call is attempted at all."""
    entry = _add_entry(hass)

    with patch(
        "custom_components.tuya_ble.diagnostics.HASSTuyaBLEDeviceManager"
    ) as manager_cls:
        payload = await async_get_config_entry_diagnostics(hass, entry)

    manager_cls.assert_not_called()
    assert payload["schema_source"] == "stored"


@pytest.mark.parametrize("schema", ["absent", "empty"])
async def test_diagnostics_legacy_entry_without_local_schema(
    hass: HomeAssistant, schema: str
) -> None:
    """A legacy entry with cloud schema but no provenance is reported as such.

    Regression test for #85: the runtime function/status-range sections are a
    usable schema, so their presence must not be reported as "missing".
    """
    entry_data = _entry_data()
    if schema == "absent":
        del entry_data[CONF_LOCAL_SCHEMA]
    else:
        entry_data[CONF_LOCAL_SCHEMA] = {}
    entry = _add_entry(hass, data=entry_data)

    payload = await async_get_config_entry_diagnostics(hass, entry)

    assert payload["schema_source"] == "legacy"
    assert payload["schema_captured_at"] is None
    assert payload["cloud_schema"]["local_strategy"] == []
    # The runtime status range is used when the schema carries none.
    assert payload["cloud_schema"]["status_range"] == [{"code": "switch_1", "dp_id": 1}]


async def test_diagnostics_entry_without_functions_and_status_range(
    hass: HomeAssistant,
) -> None:
    """Empty runtime lists are empty; a truly absent schema is missing."""
    entry = _add_entry(
        hass,
        data={
            "address": "AA:BB:CC:DD:EE:FF",
            CONF_UUID: "u",
            CONF_CATEGORY: "wk",
            CONF_PRODUCT_ID: "drlajpqc",
        },
    )

    payload = await async_get_config_entry_diagnostics(hass, entry)

    assert payload["schema_source"] == "missing"
    assert payload["cloud_schema"]["function"] == []
    assert payload["cloud_schema"]["status_range"] == []
    assert payload["identity"]["device_name"] is None
    assert payload["identity"]["address"] == "AA:BB:CC:DD:EE:FF"


async def test_diagnostics_empty_refresh_falls_back_to_legacy(
    hass: HomeAssistant,
) -> None:
    """An empty cloud refresh must not be labelled as a usable schema."""
    entry = _add_entry(
        hass,
        data={
            "address": "AA:BB:CC:DD:EE:FF",
            CONF_UUID: "1234567890abcdef",
            CONF_CATEGORY: "wk",
            CONF_PRODUCT_ID: "drlajpqc",
            CONF_FUNCTIONS: [{"code": "switch_1", "dp_id": 1}],
            CONF_STATUS_RANGE: [{"code": "switch_1", "dp_id": 1}],
        },
        options={
            CONF_TOKEN_INFO: {"access_token": "access-abc"},
            CONF_ENDPOINT: "https://endpoint.example.com",
        },
    )

    with (
        patch(
            "custom_components.tuya_ble.diagnostics.capture_local_schema",
            return_value={
                "captured_at": "2026-09-30T12:00:00+00:00",
                "local_strategy": [],
                "status_range": [],
            },
        ),
        patch(
            "custom_components.tuya_ble.diagnostics.HASSTuyaBLEDeviceManager"
        ) as manager_cls,
    ):
        manager_cls.return_value.get_cloud_device_by_uuid = AsyncMock(
            return_value=_CLOUD_DEVICE
        )
        payload = await async_get_config_entry_diagnostics(hass, entry)

    assert payload["schema_source"] == "legacy"
    assert payload["schema_captured_at"] is None
    assert payload["cloud_schema"]["local_strategy"] == []
    assert payload["cloud_schema"]["status_range"] == [{"code": "switch_1", "dp_id": 1}]


async def test_diagnostics_empty_refresh_without_runtime_lists_is_missing(
    hass: HomeAssistant,
) -> None:
    """An empty refresh with no runtime fallback is reported as missing."""
    entry = _add_entry(
        hass,
        data={
            "address": "AA:BB:CC:DD:EE:FF",
            CONF_UUID: "1234567890abcdef",
            CONF_CATEGORY: "wk",
            CONF_PRODUCT_ID: "drlajpqc",
        },
        options={
            CONF_TOKEN_INFO: {"access_token": "access-abc"},
            CONF_ENDPOINT: "https://endpoint.example.com",
        },
    )

    with (
        patch(
            "custom_components.tuya_ble.diagnostics.capture_local_schema",
            return_value={"captured_at": "2026-09-30T12:00:00+00:00"},
        ),
        patch(
            "custom_components.tuya_ble.diagnostics.HASSTuyaBLEDeviceManager"
        ) as manager_cls,
    ):
        manager_cls.return_value.get_cloud_device_by_uuid = AsyncMock(
            return_value=_CLOUD_DEVICE
        )
        payload = await async_get_config_entry_diagnostics(hass, entry)

    assert payload["schema_source"] == "missing"
    assert payload["schema_captured_at"] is None
    assert payload["cloud_schema"]["local_strategy"] == []
    assert payload["cloud_schema"]["status_range"] == []


async def test_diagnostics_expose_stored_cloud_info(hass: HomeAssistant) -> None:
    """The cloud snapshot captured at setup is surfaced for diagnostics."""
    entry_data = _entry_data() | {
        CONF_CLOUD_INFO: {
            "captured_at": "2026-09-30T00:00:00+00:00",
            "online": True,
            "pv": "1.0.5",
            "ip": "10.0.0.5",
        }
    }
    entry = _add_entry(hass, data=entry_data)

    payload = await async_get_config_entry_diagnostics(hass, entry)

    assert payload["cloud_info"]["pv"] == "1.0.5"
    assert payload["cloud_info"]["online"] is True
    assert payload["cloud_info"]["captured_at"] == "2026-09-30T00:00:00+00:00"


async def test_diagnostics_refresh_captures_fresh_cloud_info(
    hass: HomeAssistant,
) -> None:
    """A cloud refresh replaces the stored snapshot with the freshly fetched one."""
    entry = _add_entry(
        hass,
        data=_entry_data() | {CONF_CLOUD_INFO: {"captured_at": "old", "online": False}},
        options={
            CONF_TOKEN_INFO: {"access_token": "access-abc"},
            CONF_ENDPOINT: "https://endpoint.example.com",
        },
    )

    with (
        patch(
            "custom_components.tuya_ble.diagnostics.capture_local_schema",
            return_value=_schema(),
        ),
        patch(
            "custom_components.tuya_ble.diagnostics.capture_cloud_info",
            return_value={"captured_at": "fresh", "online": True},
        ) as capture,
        patch(
            "custom_components.tuya_ble.diagnostics.HASSTuyaBLEDeviceManager"
        ) as manager_cls,
    ):
        manager_cls.return_value.get_cloud_device_by_uuid = AsyncMock(
            return_value=_CLOUD_DEVICE
        )
        payload = await async_get_config_entry_diagnostics(hass, entry)

    capture.assert_called_once_with(_CLOUD_DEVICE)
    assert payload["cloud_info"] == {"captured_at": "fresh", "online": True}
