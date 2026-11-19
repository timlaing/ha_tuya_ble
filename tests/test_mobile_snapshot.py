"""Mobile cloud snapshots retain useful metadata, not private values."""

import json
from typing import Any
from unittest.mock import patch

import pytest

from custom_components.tuya_ble.mobile_snapshot import (
    capture_mobile_snapshot,
    filter_mobile_snapshot,
)


def capture(record: dict[str, Any]) -> dict[str, Any]:
    """Capture a device record without involving any cloud I/O."""
    return capture_mobile_snapshot("tuya_smart", {"device": record}, {"device": True})


def test_snapshot_metadata_and_nested_redaction() -> None:
    """Secrets and identifying/status values are withheld before persistence."""
    record = {
        "productId": "jetlwvfm",
        "productVer": "1.2.3",
        "localKey": "LOCAL_KEY_SECRET",
        "secKey": "SEC_KEY_SECRET",
        "communication": {"ble": True, "protocolVersion": 4, "uuid": "PRIVATE_UUID"},
        "otaInfo": {"version": "1.2", "url": "https://private/signed-token"},
        "dataPointInfo": {
            "schema": [
                {
                    "dpId": 13,
                    "type": "Integer",
                    "unit": "%",
                    "min": 0,
                    "range": [1, 2],
                    "secret": "PRIVATE_SECRET",
                }
            ],
            "dps": {"13": 88, "101": "PRIVATE_CODE"},
        },
        "meta": {"person@example.org": {"secret": "NESTED_PRIVATE"}},
    }
    snapshot = capture(record)
    device = snapshot["records"]["device"]
    assert device["productId"] == "jetlwvfm"
    assert device["communication"]["ble"] is True
    assert device["otaInfo"]["version"] == "1.2"
    assert device["dataPointInfo"]["schema"][0]["dpId"] == 13
    assert device["dataPointInfo"]["schema"][0]["range"] == [1, 2]
    assert device["dataPointInfo"]["dps"]["13"]["redacted"] is True
    assert snapshot["key_presence"] == {"local_key": True, "sec_key": True}
    serialized = json.dumps(snapshot)
    for secret in (
        "LOCAL_KEY_SECRET",
        "SEC_KEY_SECRET",
        "PRIVATE_UUID",
        "signed-token",
        "PRIVATE_SECRET",
        "PRIVATE_CODE",
        "person@example.org",
        "NESTED_PRIVATE",
    ):
        assert secret not in serialized
    assert filter_mobile_snapshot(snapshot)["records"] == snapshot["records"]
    assert filter_mobile_snapshot(snapshot)["key_presence"] == snapshot["key_presence"]


@pytest.mark.parametrize("value", [None, True, 2, 2.5, "secret", {}, [], (), object()])
def test_sensitive_field_shapes(value: Any) -> None:
    """A secret field exposes its shape only, including unknown object types."""
    result = capture({"secKey": value})["records"]["device"]["secKey"]
    assert result["redacted"] is True
    assert "secret" not in json.dumps(result)


@pytest.mark.parametrize("value", [None, True, 4, 4.5, "1.2"])
def test_allowed_scalars(value: Any) -> None:
    """Approved paths preserve small JSON-safe scalar metadata."""
    assert capture({"productVer": value})["records"]["device"]["productVer"] == value


@pytest.mark.parametrize("value", [2**80, float("inf"), float("nan"), "x" * 513])
def test_invalid_allowed_scalar_is_redacted(value: Any) -> None:
    """Non-finite, oversized or very large values cannot enter stored JSON."""
    assert capture({"productVer": value})["records"]["device"]["productVer"]["redacted"]


@pytest.mark.parametrize(
    "marker",
    [
        {"redacted": True, "type": []},
        {"redacted": True, "type": "SECRET"},
        {"redacted": True, "type": "string", "secret": "SECRET"},
        {"redacted": False, "type": "string"},
        {"redacted": True, "type": "string", "length": -1},
        {"redacted": True, "type": "string", "length": True},
        {"redacted": True, "type": "string", "length": "SECRET"},
    ],
)
def test_saved_marker_cannot_smuggle_private_values(marker: dict[str, Any]) -> None:
    """A tampered redaction description cannot escape export filtering."""
    exported = filter_mobile_snapshot({
        "version": 1,
        "app": "tuya_smart",
        "records": {"device": {"secKey": marker}},
    })
    assert "SECRET" not in json.dumps(exported)


@pytest.mark.parametrize("snapshot", [None, [], {}, {"version": 9}])
def test_missing_or_unknown_snapshot(snapshot: Any) -> None:
    """Legacy entries with no capture export an empty mobile section."""
    assert not filter_mobile_snapshot(snapshot)


def test_malformed_saved_sections() -> None:
    """Unknown app, fields and capture metadata are not blindly exported."""
    exported = filter_mobile_snapshot({
        "version": 1,
        "app": "PASSWORD",
        "records": "SECRET",
        "operations": [],
        "captured_at": "SECRET",
        "password": "SECRET",
    })
    assert exported["app"] == ""
    assert not exported["records"]
    assert "SECRET" not in json.dumps(exported)


@pytest.mark.parametrize("records", [{"device": None}, {"status": {"dps": [1, True]}}])
def test_optional_record_shapes(records: dict[str, Any]) -> None:
    """Incomplete records remain JSON-safe and never invent credential presence."""
    snapshot = capture_mobile_snapshot("unknown", records, {})
    assert snapshot["app"] == ""
    assert snapshot["key_presence"] == {"local_key": False, "sec_key": False}
    json.dumps(filter_mobile_snapshot(snapshot))


def test_snapshot_limits_and_truncation() -> None:
    """Huge collections are bounded, with explicit truncation metadata."""
    snapshot = capture({
        "values": [dict.fromkeys(["x", "y"], "SECRET")] * 300,
        "extras": dict.fromkeys([f"x{i}" for i in range(300)], 3),
    })
    assert snapshot["truncated"] is True
    assert len(snapshot["records"]["device"]["values"]) <= 256
    assert len(snapshot["records"]["device"]["extras"]) <= 256
    assert filter_mobile_snapshot(snapshot)["truncated"] is True


def test_depth_limit() -> None:
    """Deep input cannot recurse without a bound."""
    schema: dict[str, Any] = {"x": 1}
    for _ in range(15):
        schema = {"child": schema}
    assert capture({"dataPointInfo": {"schema": schema}})["truncated"] is True


@pytest.mark.parametrize("limit", ["MAX_NODES", "MAX_BYTES"])
def test_node_and_byte_limits(limit: str) -> None:
    """Resource limits work for dictionaries and arrays."""
    with patch(f"custom_components.tuya_ble.mobile_snapshot.{limit}", 300):
        snapshot = capture({"values": list(range(1000)), "other": {"x": 1}})
    assert snapshot["truncated"] is True


def test_dynamic_token_key_is_withheld() -> None:
    """Opaque dynamic keys must not leak secrets through dictionary names."""
    snapshot = capture({"A" * 32: "SECRET", "person@example.org": "SECRET"})
    assert "A" * 32 not in json.dumps(snapshot)


def test_unknown_capture_time_is_replaced() -> None:
    """Invalid stored timestamps and unknown fields cannot leak into diagnostics."""
    snapshot = capture({})
    snapshot.update(captured_at="a" * 80, key_presence=["SECRET"])
    assert filter_mobile_snapshot(snapshot)["captured_at"] != "a" * 80


def test_final_byte_limit() -> None:
    """Even JSON expansion is bounded before persistence."""
    with patch("custom_components.tuya_ble.mobile_snapshot.MAX_BYTES", 100):
        assert capture({"productId": "jetlwvfm"})["records"] == {}


@pytest.mark.parametrize(
    "schema",
    [
        '[{"dpId":13,"type":"Integer","min":0,"secKey":"PRIVATE"}]',
        "invalid JSON",
        '"PRIVATE"',
        "x" * (256 * 1024 + 1),
    ],
)
def test_schema_json_strings_are_parsed_safely(schema: str) -> None:
    """A schema can be JSON text, never unbounded or blindly exported."""
    captured = capture({"dataPointInfo": {"schema": schema}})
    value = captured["records"]["device"]["dataPointInfo"]["schema"]
    if schema.startswith("["):
        assert value[0]["dpId"] == 13
    else:
        assert value["redacted"] is True
    assert "PRIVATE" not in json.dumps(captured)


def test_collection_node_budget_is_explicitly_truncated() -> None:
    """A collection shorter than MAX_ITEMS still respects cumulative node limits."""
    with patch("custom_components.tuya_ble.mobile_snapshot.MAX_NODES", 5):
        result = capture({
            "dataPointInfo": {"schema": [{"dpId": i} for i in range(20)]}
        })
    assert result["truncated"] is True


def test_root_field_count_is_bounded() -> None:
    """Even a record with hundreds of top-level fields cannot grow indefinitely."""
    result = capture(dict.fromkeys([f"field{i}" for i in range(300)], 0))
    assert result["truncated"] is True
    assert len(result["records"]["device"]) == 256


def test_snapshot_budget_is_shared_across_records() -> None:
    """Device, product and status sections share one cumulative limit."""
    with patch("custom_components.tuya_ble.mobile_snapshot.MAX_NODES", 5):
        captured = capture_mobile_snapshot(
            "tuya_smart",
            {
                "device": {"dataPointInfo": {"schema": [{"dpId": 13}]}},
                "product": {},
                "status": {},
            },
            {},
        )
    assert captured["truncated"]
