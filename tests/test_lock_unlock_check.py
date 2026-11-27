"""The cloud ``ble_unlock_check``: format, storage, redaction and logging.

All values are synthetic. The check is a lock credential, so every test that
touches it also checks that it never reaches a log line or diagnostics.
"""

# pylint: disable=protected-access
from __future__ import annotations

import asyncio
import base64
from dataclasses import replace
import logging
from struct import pack
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.tuya_ble import _build_credentials_from_entry, async_setup_entry
from custom_components.tuya_ble import diagnostics as diagnostics_module
from custom_components.tuya_ble.cloud import _build_credentials
from custom_components.tuya_ble.const import CONF_BLE_UNLOCK_CHECK, DOMAIN
from custom_components.tuya_ble.device_registry import DeviceEntities, DeviceRegistry
from custom_components.tuya_ble.devices import TuyaBLEData, TuyaBLEProductInfo
from custom_components.tuya_ble.diagnostics import async_get_config_entry_diagnostics
from custom_components.tuya_ble.mobile_snapshot import (
    capture_mobile_snapshot,
    filter_mobile_snapshot,
)
from custom_components.tuya_ble.tuya_ble import (
    ConnectionProfile,
    TuyaBLEDataPoints,
    TuyaBLEDataPointType,
    TuyaBLEDevice,
    build_stored_check_unlock_command,
    build_timestamped_unlock_command,
    decode_ble_unlock_check,
    is_valid_ble_unlock_check,
)
from custom_components.tuya_ble.tuya_ble.const import TuyaBLECode
from tests.conftest import (
    FakeBLEManager,
    FakeDatapointsOwner,
    make_credentials,
    verified_by,
)
from tests.protocol_harness import ProtocolHarness, encrypt_payload, frame_packet0
from tests.test_cloud import make_device as make_cloud_device
from tests.test_config_flow import FakeManager, build_flow
from tests.test_config_flow_options_credentials import (
    NEW_LOCAL_KEY,
    options_flow,
    replace_keys,
)
from tests.test_diagnostics import _add_entry, _entry_data
from tests.test_init import _make_entry_data, _patch_deps
from tests.test_protocol import conclude
from tests.test_protocol import session_key as v3_session_key
from tests.test_protocol_v4 import DP_RECEIVE_V4, deliver, drain_responses, klv, report
from tests.test_security_v2 import DETECT, V2_SERVICE, _discovery

PAYLOAD = b"\x00\x01\xff\xff97531864\x01\x01\x02\x03\x04\x00\x00"
CHECK = base64.b64encode(PAYLOAD).decode()
SECRET = "97531864"


def _encoded(payload: bytes) -> str:
    return base64.b64encode(payload).decode()


# --- format -----------------------------------------------------------------


def test_decode_and_build() -> None:
    """The command keeps the member id and check code and adds the time."""
    assert decode_ble_unlock_check(CHECK) == PAYLOAD
    assert is_valid_ble_unlock_check(CHECK)
    assert build_timestamped_unlock_command(CHECK, 0x6AD8EB64) == (
        bytes.fromhex("ffff0001") + b"97531864" + bytes.fromhex("016ad8eb640001")
    )
    assert build_timestamped_unlock_command(CHECK, 0)[-6:-2] == b"\x00\x00\x00\x00"
    assert build_timestamped_unlock_command(CHECK, 0xFFFFFFFF)[-6:-2] == b"\xff" * 4


def test_build_stored_check_unlock_command() -> None:
    """The stored-check variant echoes the cloud key bytes, not the current time."""
    command = build_stored_check_unlock_command(CHECK)
    assert command == (
        bytes.fromhex("ffff0001") + b"97531864" + bytes.fromhex("01010203040001")
    )
    assert len(command) == 19


def test_build_stored_check_unlock_command_rejects_malformed() -> None:
    """A malformed check never produces a command."""
    invalid = _encoded(b"invalid")
    with pytest.raises(ValueError, match="invalid format"):
        build_stored_check_unlock_command(invalid)


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("@@@@", id="not-base64"),
        pytest.param(_encoded(PAYLOAD[:-1]), id="short"),
        pytest.param(_encoded(b"\x00\x00" + PAYLOAD[2:]), id="member-0000"),
        pytest.param(_encoded(b"\xff\xff" + PAYLOAD[2:]), id="member-ffff"),
        pytest.param(_encoded(PAYLOAD[:2] + b"\x00\x00" + PAYLOAD[4:]), id="sep"),
        pytest.param(_encoded(PAYLOAD[:4] + b"1234567x" + PAYLOAD[12:]), id="code"),
        pytest.param(_encoded(PAYLOAD[:12] + b"\x02" + PAYLOAD[13:]), id="flag"),
        pytest.param(_encoded(PAYLOAD[:17] + b"\x00\x01"), id="trailer"),
    ],
)
def test_malformed_checks_are_rejected(value: str) -> None:
    """Every field is checked; the error never echoes the value."""
    with pytest.raises(ValueError) as err:
        decode_ble_unlock_check(value)
    assert value not in str(err.value)
    assert not is_valid_ble_unlock_check(value)


@pytest.mark.parametrize("value", [None, 123, b"x"])
def test_non_string_checks_are_rejected(value: Any) -> None:
    """Only a string can be a check."""
    assert not is_valid_ble_unlock_check(value)
    with pytest.raises(ValueError, match="must be a string"):
        decode_ble_unlock_check(value)


@pytest.mark.parametrize("timestamp", [-1, 0x100000000])
def test_out_of_range_timestamp_is_rejected(timestamp: int) -> None:
    """The command carries a 32-bit unsigned time."""
    with pytest.raises(ValueError, match="timestamp"):
        build_timestamped_unlock_command(CHECK, timestamp)


def test_credentials_hide_the_check() -> None:
    """Neither str() nor repr() exposes the check."""
    credentials = replace(make_credentials(), ble_unlock_check=CHECK)
    assert CHECK not in repr(credentials)
    assert CHECK not in str(credentials)
    assert "ble_unlock_check: xxxxxxxxxxxxxxxx" in str(credentials)
    assert "ble_unlock_check: None" in str(make_credentials())


# --- cloud, config flow and entry -------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected", "warned"),
    [
        ({CONF_BLE_UNLOCK_CHECK: CHECK}, CHECK, False),
        ({CONF_BLE_UNLOCK_CHECK: "bm90LWEtY2hlY2s="}, None, True),
        ({CONF_BLE_UNLOCK_CHECK: ""}, None, False),
        ({"switch_1": True}, None, False),
        (None, None, False),
    ],
)
def test_cloud_reads_the_check_from_status(
    caplog: pytest.LogCaptureFixture,
    status: dict[str, Any] | None,
    expected: str | None,
    warned: bool,
) -> None:
    """Only a valid check is kept; a malformed one is dropped without logging it."""
    device = make_cloud_device(**({"status": status} if status is not None else {}))
    with caplog.at_level(logging.DEBUG):
        credentials = _build_credentials(device)
    assert credentials.ble_unlock_check == expected
    assert ("malformed ble_unlock_check" in caplog.text) is warned
    assert "bm90LWEtY2hlY2s=" not in caplog.text


@pytest.mark.parametrize("check", [CHECK, None])
async def test_entry_stores_the_check(hass: HomeAssistant, check: str | None) -> None:
    """A device whose cloud record carries a check persists it."""
    flow = build_flow(hass)
    info = _discovery(V2_SERVICE)
    flow.discovery_info = cast(Any, info)
    flow._manager = cast(
        Any, FakeManager(replace(make_credentials(), ble_unlock_check=check))
    )
    with (
        patch.object(flow, "_async_scan_device", return_value=info),
        patch(DETECT, new=AsyncMock(side_effect=verified_by(ConnectionProfile.V4_20))),
    ):
        await flow.async_step_discovered_device({})
        assert flow._probe_task is not None
        await flow._probe_task
        result = await flow.async_step_probe_finish()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    data = cast(dict[str, Any], result["data"])
    assert data.get(CONF_BLE_UNLOCK_CHECK) == check
    assert (CONF_BLE_UNLOCK_CHECK in data) is (check is not None)


async def test_key_pair_replacement_keeps_the_check(hass: HomeAssistant) -> None:
    """Replacing the device keys leaves the lock's unlock check in place."""
    flow, entry = options_flow(hass, sec_key=None)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_BLE_UNLOCK_CHECK: CHECK}
    )
    result, _reload = await replace_keys(hass, flow, {"local_key": NEW_LOCAL_KEY})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_BLE_UNLOCK_CHECK] == CHECK
    assert entry.data["local_key"] == NEW_LOCAL_KEY


def test_entry_credentials_restore_the_check() -> None:
    """Runtime credentials carry the stored check, or none."""
    data = _make_entry_data()
    entry = MockConfigEntry(domain=DOMAIN, data={**data, CONF_BLE_UNLOCK_CHECK: CHECK})
    assert _build_credentials_from_entry(entry).ble_unlock_check == CHECK
    assert (
        _build_credentials_from_entry(
            MockConfigEntry(domain=DOMAIN, data=data)
        ).ble_unlock_check
        is None
    )


async def test_setup_marks_sensitive_dp_ids(hass: HomeAssistant) -> None:
    """Setup applies the descriptor's sensitive ids before the first connection."""
    entry = MockConfigEntry(domain=DOMAIN, data=_make_entry_data(), entry_id="e1")
    entry.add_to_hass(hass)
    product = TuyaBLEProductInfo(
        name="Lock",
        entities=DeviceEntities(
            category="jtmspro", product_id="x", sensitive_dp_ids=frozenset({71})
        ),
    )
    deps = _patch_deps(hass, product_info=product)
    for patcher in deps["patches"]:
        patcher.start()
    try:
        assert await async_setup_entry(hass, entry)
    finally:
        patch.stopall()
        for task in deps["background_tasks"]:
            task["target"].close()
    assert deps["device"].datapoints.sensitive_ids == frozenset({71})


# --- diagnostics ------------------------------------------------------------


async def test_diagnostics_redact_the_check_and_sensitive_dps(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sensitive DPs keep their type only; the stored check is a flag."""
    registry = DeviceRegistry()
    registry._products[("wk", "drlajpqc")] = DeviceEntities(
        category="wk", product_id="drlajpqc", sensitive_dp_ids=frozenset({71, 72})
    )
    monkeypatch.setattr(diagnostics_module, "get_registry", lambda: registry)
    entry = _add_entry(hass, data={**_entry_data(), CONF_BLE_UNLOCK_CHECK: CHECK})
    device = TuyaBLEDevice(
        FakeBLEManager(make_credentials()),
        cast(Any, SimpleNamespace(address="AA:BB:CC:DD:EE:FF", name="Lock")),
        cast(Any, SimpleNamespace(rssi=-60, service_data={}, manufacturer_data={})),
    )
    command = build_timestamped_unlock_command(CHECK, 1)
    device.datapoints.update_from_device(
        71, 0.0, 0, TuyaBLEDataPointType.DT_RAW, command, command
    )
    device.datapoints.get_or_create(72, TuyaBLEDataPointType.DT_RAW, command)
    device.datapoints.update_from_device(1, 0.0, 0, TuyaBLEDataPointType.DT_BOOL, True)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = TuyaBLEData(
        title="Lock",
        device=device,
        product=MagicMock(),
        manager=MagicMock(),
        coordinator=MagicMock(),
    )

    payload = await async_get_config_entry_diagnostics(hass, entry)

    assert payload["live"]["71"] == {
        "type": "DT_RAW",
        "value": "**REDACTED**",
        "raw": "**REDACTED**",
        "changed_by_device": False,
    }
    assert payload["live"]["72"]["value"] == "**REDACTED**"
    assert payload["live"]["72"]["raw"] is None
    assert payload["live"]["1"]["value"] is True
    assert payload["identity"]["ble_unlock_check_stored"] is True
    assert CHECK not in repr(payload)
    assert command.hex() not in repr(payload)
    assert SECRET not in repr(payload)


async def test_diagnostics_without_check(hass: HomeAssistant) -> None:
    """An entry with no check reports it as not stored."""
    entry = _add_entry(hass)
    payload = await async_get_config_entry_diagnostics(hass, entry)
    assert payload["identity"]["ble_unlock_check_stored"] is False


def test_mobile_snapshot_never_keeps_the_check() -> None:
    """App-login metadata (#209) withholds the check and DP 71 payloads."""
    command = build_timestamped_unlock_command(CHECK, 1).hex()
    records = {
        "device": {
            "devId": "device123",
            "dps": {"71": command},
            "status": [{"code": CONF_BLE_UNLOCK_CHECK, "value": CHECK}],
            "dataPointInfo": {"dps": {"71": command}},
        },
        "status": {"result": [{"code": CONF_BLE_UNLOCK_CHECK, "value": CHECK}]},
    }
    snapshot = capture_mobile_snapshot("smart_life", records, {"device": True})
    exported = filter_mobile_snapshot(snapshot)
    for payload in (snapshot, exported):
        assert CHECK not in repr(payload)
        assert command not in repr(payload)
        assert SECRET not in repr(payload)


# --- logging ----------------------------------------------------------------


def test_datapoint_logs_redact_sensitive_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Device reports and local writes of a sensitive DP never log its value."""
    datapoints = TuyaBLEDataPoints(cast(TuyaBLEDevice, FakeDatapointsOwner()))
    datapoints.sensitive_ids = [71]
    assert datapoints.sensitive_ids == frozenset({71})
    command = build_timestamped_unlock_command(CHECK, 1)
    with caplog.at_level(logging.DEBUG):
        datapoint = datapoints.update_from_device(
            71, 0.0, 0, TuyaBLEDataPointType.DT_RAW, b"\x00"
        )
        datapoint.set_value_no_notify(command)
        datapoints.update_from_device(71, 1.0, 0, TuyaBLEDataPointType.DT_RAW, b"\x01")
        datapoints.update_from_device(1, 0.0, 0, TuyaBLEDataPointType.DT_BOOL, True)
        datapoints.update_from_device(1, 1.0, 0, TuyaBLEDataPointType.DT_BOOL, False)
    assert repr(command) not in caplog.text
    assert "Data point 71 changed by device: <redacted> -> <redacted>" in caplog.text
    assert "Data point 71 set locally without notifying the device: <redacted>" in (
        caplog.text
    )
    assert "Data point 1 changed by device: True -> False" in caplog.text


async def test_v3_send_and_receive_redact_sensitive_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The v3 send trace and per-DP receive trace hide a sensitive DP."""
    harness = ProtocolHarness()
    await harness.register_notify()
    harness.device._protocol_version = 3
    harness.device.datapoints.sensitive_ids = {71}
    command = build_timestamped_unlock_command(CHECK, 1)
    datapoint = harness.device.get_or_create_datapoint(
        71, TuyaBLEDataPointType.DT_RAW, b""
    )
    ack = frame_packet0(
        encrypt_payload(
            v3_session_key(harness), 5, 1, 1, TuyaBLECode.FUN_SENDER_DPS, b"\x00"
        )
    )
    report_v3 = pack(">BBB", 71, TuyaBLEDataPointType.DT_RAW.value, len(command))
    with caplog.at_level(logging.DEBUG):
        await conclude(datapoint.set_value(command), lambda: harness.notify(ack))
        harness.notify(
            frame_packet0(
                encrypt_payload(
                    v3_session_key(harness),
                    5,
                    2,
                    0,
                    TuyaBLECode.FUN_RECEIVE_DP,
                    report_v3 + command,
                )
            )
        )
        await asyncio.sleep(0)
    assert "Sending datapoint update, id: 71, type: DT_RAW: value: <redacted>" in (
        caplog.text
    )
    assert "Received DP id=71 type=DT_RAW flags=0x00 raw=<redacted>" in caplog.text
    assert command.hex() not in caplog.text
    assert repr(command) not in caplog.text


async def test_v4_report_payload_is_not_logged_with_sensitive_dps(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A v4 report is logged by size only once any DP is sensitive."""
    harness = ProtocolHarness(protocol_version=4)
    await harness.client.start_notify(
        "00000002-0000-1001-8001-00805f9b07d0",
        harness.device._safe_notification_handler,
    )
    harness.device.datapoints.sensitive_ids = {71}
    command = build_timestamped_unlock_command(CHECK, 1)
    data = report(klv(71, TuyaBLEDataPointType.DT_RAW, command))
    with caplog.at_level(logging.DEBUG):
        deliver(harness, DP_RECEIVE_V4, data)
        await drain_responses(harness.device)
    assert f"Received FUN_RECEIVE_DP_V4 payload: {len(data)} byte(s)" in caplog.text
    assert command.hex() not in caplog.text
    datapoint = harness.device.datapoints[71]
    assert datapoint is not None
    assert datapoint.value == command
