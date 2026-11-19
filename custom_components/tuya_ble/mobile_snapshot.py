"""Bounded mobile cloud metadata capture, sanitized before storage and export."""

# cspell:ignore ecode topo

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import math
import re
from typing import Any

from homeassistant.util import dt as dt_util

MAX_DEPTH = 8
MAX_ITEMS = 256
MAX_NODES = 4096
MAX_BYTES = 256 * 1024
_DP_ID = re.compile(r"\d{1,3}", re.ASCII)
_MASKED_FIELD = re.compile(r"redacted_field_\d{1,3}", re.ASCII)
_SENSITIVE = re.compile(
    r"key|token|secret|password|passwd|signature|certificate|checkcode|"
    r"endpoint|url|address|^ip$|^lat$|^lon$|owner|user|^uid$|^sid$|ecode|^mac$|^uuid$|"
    r"^devId$|^deviceId$|^id$|^name$",
    re.IGNORECASE,
)
# Only documented product/version/capability and DP-schema paths expose values.
_ROOT = frozenset({
    "productId",
    "product_id",
    "productName",
    "productVer",
    "category",
    "accessType",
    "baseAttribute",
    "devAttribute",
    "cloudOnline",
    "isOnline",
    "virtual",
    "virtualExperience",
    "firmware_version",
    "support_local",
})
_CHILDREN = {
    "communication": frozenset({
        "type",
        "communicationType",
        "ble",
        "wifi",
        "zigbee",
        "mesh",
        "protocolVersion",
        "bleVersion",
        "supportLocal",
    }),
    "otaInfo": frozenset({
        "version",
        "currentVersion",
        "firmwareVersion",
        "hardwareVersion",
        "module",
        "moduleType",
        "upgradeStatus",
    }),
    "schema": frozenset({
        "dpId",
        "dp_id",
        "code",
        "type",
        "valueType",
        "unit",
        "min",
        "max",
        "step",
        "scale",
        "range",
    }),
}
_TYPES = frozenset({"null", "boolean", "number", "string", "object", "array", "other"})
_STRUCTURAL = {
    (): _ROOT
    | frozenset({
        "activeTime",
        "active_time",
        "biz_type",
        "create_time",
        "createTime",
        "communication",
        "dataPointInfo",
        "devId",
        "deviceId",
        "id",
        "devKey",
        "deviceTopo",
        "gatewayVerCAD",
        "homeDisplayOrder",
        "iconUrl",
        "icon",
        "ip",
        "lat",
        "localKey",
        "local_key",
        "secKey",
        "sec_key",
        "lon",
        "mac",
        "meta",
        "name",
        "otaInfo",
        "ownerId",
        "owner_id",
        "runtimeEnv",
        "shareInfo",
        "timezoneId",
        "time_zone",
        "uuid",
        "uid",
        "update_time",
        "dps",
        "status",
        "status_range",
        "local_strategy",
        "result",
        "success",
    }),
    ("communication",): _CHILDREN["communication"]
    | frozenset({
        "uuid",
        "mac",
        "id",
        "deviceId",
        "address",
        "key",
        "secret",
    }),
    ("otaInfo",): _CHILDREN["otaInfo"]
    | frozenset({
        "url",
        "upgradeUrl",
        "modules",
        "otaModules",
    }),
    ("dataPointInfo",): frozenset({
        "schema",
        "dps",
        "dpMaxId",
        "dpName",
        "dpStatus",
        "dpsTime",
    }),
}
_SCHEMA_FIELDS = _CHILDREN["schema"] | frozenset({
    "name",
    "desc",
    "values",
    "valueDesc",
    "properties",
    "config_item",
    "status_code",
    "value_convert",
    "enumMappingMap",
    "ratio",
    "secret",
})


def _numeric_dp(name: str) -> bool:
    """DP identifiers are structural; opaque device/account IDs are not."""
    return _DP_ID.fullmatch(name) is not None and int(name) <= 255


def _recognized_field(path: tuple[str, ...], name: str) -> bool:
    """Retain only known names in every expanded container, including on export."""
    if _MASKED_FIELD.fullmatch(name):
        return True
    if path == ("dataPointInfo", "dps"):
        return _numeric_dp(name)
    if path[:2] == ("dataPointInfo", "schema"):
        return name in _SCHEMA_FIELDS or _numeric_dp(name)
    return name in _STRUCTURAL.get(path, ())


def _shape(value: Any) -> dict[str, Any]:
    """Withhold content completely while retaining its type and size."""
    result: dict[str, Any] = {"redacted": True, "type": "other"}
    if value is None:
        result["type"] = "null"
    else:
        for kind, name in (
            (bool, "boolean"),
            (int | float, "number"),
            (str, "string"),
            (Mapping, "object"),
            (list | tuple, "array"),
        ):
            if isinstance(value, kind):
                result["type"] = name
                break
    if isinstance(value, str):
        result["length"] = len(value)
    elif isinstance(value, Mapping | list | tuple):
        result["items"] = len(value)
    return result


def _stored_shape(value: Any) -> dict[str, Any] | None:
    """Recheck a saved redaction marker without trusting arbitrary contents."""
    if (
        not isinstance(value, dict)
        or value.get("redacted") is not True
        or not isinstance(value.get("type"), str)
        or value.get("type") not in _TYPES
        or set(value) - {"redacted", "type", "length", "items"}
    ):
        return None
    result = {"redacted": True, "type": value["type"]}
    for key in ("length", "items"):
        count = value.get(key)
        if (
            isinstance(count, int)
            and not isinstance(count, bool)
            and 0 <= count < 2**63
        ):
            result[key] = count
    return result


def _allowed(path: tuple[str, ...]) -> bool:
    """Use exact contextual paths; never allow a whole cloud subtree."""
    if len(path) == 1:
        return path[0] in _ROOT
    if len(path) == 2:
        return path[1] in _CHILDREN.get(path[0], ())
    # Structured DP schema only, not live/raw DPS or the entire dataPointInfo.
    return (
        len(path) == 4
        and path[:2] == ("dataPointInfo", "schema")
        and path[2] == "[]"
        and path[3] in _CHILDREN["schema"]
    ) or (
        len(path) == 5
        and path[:2] == ("dataPointInfo", "schema")
        and path[2] == "[]"
        and path[3:] == ("range", "[]")
    )


def _scalar(value: Any) -> bool:
    """Only retain small finite scalar values at approved paths."""
    if value is None or isinstance(value, bool):
        return True
    if isinstance(value, int):
        return value.bit_length() <= 64
    if isinstance(value, float):
        return math.isfinite(value)
    return isinstance(value, str) and len(value) <= 512


def _may_expand(path: tuple[str, ...]) -> bool:
    """Unknown containers may contain private identities in their dictionary keys."""
    return (
        not path
        or path
        in {
            ("communication",),
            ("otaInfo",),
            ("dataPointInfo",),
            ("dataPointInfo", "dps"),
        }
        or path[:2] == ("dataPointInfo", "schema")
    )


def _private_path(path: tuple[str, ...]) -> bool:
    """Credential-like fields must be masked before inspecting their contents."""
    return bool(path and _SENSITIVE.search(path[-1]))


@dataclass
class _Capture:
    """Bound recursive processing even for unusually large cloud records."""

    nodes: int = 0
    bytes_used: int = 0
    truncated: bool = False

    def _over_budget(self, path: tuple[str, ...]) -> bool:
        """All recursion must stay inside processing and depth limits."""
        return (
            self.nodes > MAX_NODES
            or self.bytes_used > MAX_BYTES
            or len(path) > MAX_DEPTH
        )

    def _schema(self, value: str) -> Any:
        """Decode a schema JSON string only within the snapshot size limit."""
        if len(value) > MAX_BYTES:
            self.truncated = True
            return None
        try:
            decoded = json.loads(value)
        except RecursionError:
            self.truncated = True
            return None
        except ValueError:
            return None
        return decoded if isinstance(decoded, dict | list) else None

    def _mapping(
        self, value: Mapping[str, Any], path: tuple[str, ...]
    ) -> dict[str, Any]:
        """Preserve structural field names, never opaque dynamic identities."""
        result = {}
        for index, (key, item) in enumerate(value.items()):
            if (
                index >= MAX_ITEMS
                or self.nodes >= MAX_NODES
                or self.bytes_used > MAX_BYTES
            ):
                self.truncated = True
                break
            name = str(key)
            if not _recognized_field(path, name):
                name = f"redacted_field_{index}"
                item = _shape(item)
            self.bytes_used += len(name) + 80
            result[name] = self.sanitize(item, (*path, name))
        return result

    def _schema_snapshot(self, value: str, path: tuple[str, ...]) -> Any:
        """Apply the same capture policy after decoding schema JSON text."""
        decoded = self._schema(value)
        if decoded is None:
            return _shape(value)
        return self.sanitize(decoded, path)

    def _array(
        self, value: list[Any] | tuple[Any, ...], path: tuple[str, ...]
    ) -> list[Any]:
        """Bound collection sizes and cumulative processing work."""
        if len(value) > MAX_ITEMS:
            self.truncated = True
        items = [
            self.sanitize(item, (*path, "[]"))
            for item in value[:MAX_ITEMS]
            if self.nodes < MAX_NODES and self.bytes_used <= MAX_BYTES
        ]
        if len(items) < min(len(value), MAX_ITEMS):
            self.truncated = True
        return items

    def sanitize(self, value: Any, path: tuple[str, ...] = ()) -> Any:
        """Keep approved metadata, replacing everything else with safe shapes."""
        self.nodes += 1
        self.bytes_used += 64
        if self._over_budget(path):
            self.truncated = True
            return _shape(value)
        stored = _stored_shape(value)
        if stored is not None:
            return stored
        if _private_path(path):
            return _shape(value)
        if isinstance(value, str) and path == ("dataPointInfo", "schema"):
            return self._schema_snapshot(value, path)
        if isinstance(value, Mapping):
            # Unknown containers and live DPS can use private values as keys.
            # Expand only the documented structural metadata containers.
            result: Any = (
                self._mapping(value, path) if _may_expand(path) else _shape(value)
            )
        elif isinstance(value, list | tuple):
            result = self._array(value, path)
        elif _allowed(path) and _scalar(value):
            self.bytes_used += len(value) if isinstance(value, str) else 32
            result = value
        else:
            result = _shape(value)
        return result


def capture_mobile_snapshot(
    app: str, records: Mapping[str, Any], operations: Mapping[str, bool]
) -> dict[str, Any]:
    """Capture target-device metadata only, never login/session information."""
    capture = _Capture()
    record = records.get("device")
    record = record if isinstance(record, Mapping) else {}
    snapshot = {
        "version": 1,
        "captured_at": dt_util.utcnow().isoformat(),
        "app": app if app in ("tuya_smart", "smart_life") else "",
        "records": {
            name: capture.sanitize(records[name])
            for name in ("device", "product", "status")
            if name in records
        },
        "operations": {
            name: operations.get(name) is True
            for name in ("device", "product", "status")
        },
        "truncated": capture.truncated,
        "key_presence": {
            "local_key": any(
                isinstance(record.get(key), str) and bool(record[key])
                for key in ("localKey", "local_key")
            ),
            "sec_key": any(
                isinstance(record.get(key), str) and bool(record[key])
                for key in ("secKey", "sec_key")
            ),
        },
    }
    if len(json.dumps(snapshot).encode()) > MAX_BYTES:
        snapshot["records"] = {}
        snapshot["truncated"] = True
    return snapshot


def filter_mobile_snapshot(snapshot: Any) -> dict[str, Any]:
    """Revalidate persisted snapshots before a diagnostics download."""
    if not isinstance(snapshot, dict) or snapshot.get("version") != 1:
        return {}
    records = snapshot.get("records")
    operations = snapshot.get("operations")
    filtered = capture_mobile_snapshot(
        str(snapshot.get("app"))
        if snapshot.get("app") in ("tuya_smart", "smart_life")
        else "",
        records if isinstance(records, Mapping) else {},
        operations if isinstance(operations, Mapping) else {},
    )
    captured = snapshot.get("captured_at")
    if (
        isinstance(captured, str)
        and len(captured) <= 64
        and dt_util.parse_datetime(captured) is not None
    ):
        filtered["captured_at"] = captured
    presence = snapshot.get("key_presence")
    if isinstance(presence, Mapping):
        filtered["key_presence"] = {
            name: presence.get(name) is True for name in ("local_key", "sec_key")
        }
    filtered["truncated"] |= snapshot.get("truncated") is True
    return filtered
