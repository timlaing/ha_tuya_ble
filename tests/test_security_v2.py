"""FD50 Security V2 (sec_key) channel: keys, frames, setup and diagnostics.

Security V2 follows TuyaOpen's ``ble_cryption.c``: DEVICE_INFO is encrypted
with flag 14, ``MD5(local_key + sec_key)``, and the session with flag 15,
``MD5(local_key + sec_key + srand)``. Its pair request appends both 16-byte
keys to the classic frame. All vectors are synthetic.
"""

# pylint: disable=protected-access
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
import hashlib
import logging
from struct import pack
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tuya_ble import _build_credentials_from_entry
from custom_components.tuya_ble.cloud import (
    _build_credentials,
    capture_cloud_info,
    filter_cloud_info,
)
from custom_components.tuya_ble.const import (
    CONF_CLOUD_INFO,
    CONF_SEC_KEY,
    DOMAIN,
)
from custom_components.tuya_ble.devices import TuyaBLEData
from custom_components.tuya_ble.diagnostics import async_get_config_entry_diagnostics
from custom_components.tuya_ble.tuya_ble import (
    BLE_CONNECTION_EXCEPTIONS,
    SERVICE_UUID,
    ConnectionProfile,
    ProbeOutcome,
    ProbeStage,
    TuyaBLEDevice,
    async_detect_v4_profile,
    async_probe_authenticated_status,
    decode_tuya_ble_advertisement,
)
from custom_components.tuya_ble.tuya_ble.const import (
    CHARACTERISTIC_NOTIFY,
    CHARACTERISTIC_WRITE,
    PAIRING_REQUEST_LENGTH,
    TuyaBLECode,
    TuyaBLEDataPointType,
)
from custom_components.tuya_ble.tuya_ble.exceptions import TuyaBLEDeviceError
from custom_components.tuya_ble.tuya_ble.manager import TuyaBLEDeviceCredentials
from tests import test_advertisement
from tests.conftest import FakeBLEManager, make_credentials, make_device, verified_by
from tests.protocol_harness import encrypt_payload, fragment, v4_advertisement
from tests.test_cloud import make_device as make_cloud_device
from tests.test_config_flow import FakeManager, build_flow
from tests.test_config_flow_v4 import discovery
from tests.test_diagnostics import _add_entry, _entry_data
from tests.test_protocol_v4 import (
    FakeV4Firmware,
    device_info_response,
    drain_responses,
    klv,
    report,
    run_session,
    soil_sensor_datapoints,
)

LOCAL_KEY = "0123456789abcdef"
SEC_KEY = "fedcba9876543210"
# FD50 identity record with Security V2 supported and switched on (byte 1
# 0x0c, as the Parkside KC8B105), the bound bit clear.
V2_SERVICE = b"\x41\x0c\x00\x08abcdefgh"
# Security V2 supported but not switched on, as the captured Battery BLE Roller.
V2_SUPPORTED_SERVICE = b"\x41\x04\x00\x08abcdefgh"
CLASSIC_SERVICE = b"\x49\x00\x00\x08abcdefgh"
PROBE_CONNECT = "custom_components.tuya_ble.tuya_ble.probe.establish_connection"
RUNTIME_CONNECT = "custom_components.tuya_ble.tuya_ble.base.establish_connection"
DETECT = "custom_components.tuya_ble.config_flow.async_detect_v4_credentials"
SHADE_DATA = report(
    klv(1, TuyaBLEDataPointType.DT_ENUM, b"\x01")
    + klv(3, TuyaBLEDataPointType.DT_VALUE, pack(">i", 60))
)


def v2_credentials(sec_key: str | None = SEC_KEY) -> TuyaBLEDeviceCredentials:
    """Build credentials with a full 16-byte local key and a cloud sec_key."""
    return replace(make_credentials(local_key=LOCAL_KEY), sec_key=sec_key)


def _md5(data: bytes) -> bytes:
    return hashlib.md5(data, usedforsecurity=False).digest()


class SecurityV2Firmware(FakeV4Firmware):
    """SDK firmware double that only understands the Security V2 channel."""

    def __init__(
        self,
        credentials: TuyaBLEDeviceCredentials,
        report_data: bytes,
        *,
        bound: bool = True,
    ) -> None:
        super().__init__(credentials, report_data)
        assert credentials.sec_key is not None
        material = credentials.local_key.encode() + credentials.sec_key.encode()
        self._keys = {14: _md5(material), 15: _md5(material + self._srand)}
        self.bound = bound
        self.pair_payload: bytes | None = None

    def _answer(self, seq_num: int, code: int, data: bytes, flag: int) -> None:
        if code == TuyaBLECode.FUN_SENDER_PAIR.value:
            self.pair_payload = data
        if code == TuyaBLECode.FUN_SENDER_DEVICE_INFO.value and not self.bound:
            info = bytearray(device_info_response(self._srand))
            info[5] = 0
            self._reply(code, seq_num, bytes(info), flag)
            return
        super()._answer(seq_num, code, data, flag)

    def _reply(self, code: int, response_to: int, data: bytes, flag: int) -> int:
        """Answer with the Security V2 counterpart of each classic flag."""
        return super()._reply(code, response_to, data, {4: 14, 5: 15}.get(flag, flag))


def _probe_establish(
    clients: list[FakeV4Firmware], build: Callable[[], FakeV4Firmware]
) -> Callable[..., Awaitable[FakeV4Firmware]]:
    """Return a probe connector that hands out a fresh firmware per attempt."""

    async def establish(*_args: Any, **kwargs: Any) -> FakeV4Firmware:
        client = build()
        kwargs["capture_client"](client)
        clients.append(client)
        return client

    return establish


# ---------------------------------------------------------------------------
# Advertisement
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("service", "security_v2"),
    [
        (V2_SERVICE, True),
        (V2_SUPPORTED_SERVICE, True),
        (b"\x41\x00\x00\x08abcdefgh", False),
        (CLASSIC_SERVICE, False),
    ],
)
def test_fd50_security_v2_flag(service: bytes, security_v2: bool) -> None:
    """Bit 2 of the frame control's low byte announces Security V2."""
    info = decode_tuya_ble_advertisement(test_advertisement.v4_advertisement(service))
    assert info is not None
    assert info.security_v2 is security_v2


def test_classic_advertisement_is_never_security_v2() -> None:
    """The A201 layout carries no Security V2 flag."""
    adv = test_advertisement.advertisement(
        b"\x00abcdefgh", bytes((0x80, 3, 0, 0, 0, 0)) + bytes(16)
    )
    info = decode_tuya_ble_advertisement(adv)
    assert info is None or not info.security_v2


# ---------------------------------------------------------------------------
# Credentials and key material
# ---------------------------------------------------------------------------


def test_credentials_hide_sec_key() -> None:
    """Neither str() nor repr() exposes the sec_key."""
    credentials = v2_credentials()
    assert SEC_KEY not in str(credentials)
    assert SEC_KEY not in repr(credentials)
    assert "sec_key: xxxxxxxxxxxxxxxx" in str(credentials)
    assert "sec_key: None" in str(v2_credentials(None))


def test_classic_credentials_keep_classic_keys() -> None:
    """Without a sec_key the login key stays MD5(local_key[:6])."""
    device = make_device()
    device.initialize_with_credentials(make_credentials(local_key=LOCAL_KEY))
    assert not device.security_v2
    assert device._login_key == _md5(LOCAL_KEY[:6].encode())


def test_security_v2_login_key() -> None:
    """Security V2 logs in with MD5(local_key + sec_key)."""
    device = make_device()
    device.initialize_with_credentials(v2_credentials())
    assert device.security_v2
    assert device._login_key == _md5((LOCAL_KEY + SEC_KEY).encode())


@pytest.mark.parametrize("local_key", ["abcdef", LOCAL_KEY, LOCAL_KEY + "x"])
def test_security_v2_uses_the_full_local_key(local_key: str) -> None:
    """Any local key of six or more bytes is used in full with the sec_key."""
    credentials = replace(make_credentials(local_key=local_key), sec_key=SEC_KEY)
    device = make_device()
    device.initialize_with_credentials(credentials)
    assert device._login_key == _md5((local_key + SEC_KEY).encode())


@pytest.mark.parametrize(
    ("local_key", "sec_key"),
    [("abcde", SEC_KEY), (LOCAL_KEY, "short"), (LOCAL_KEY, SEC_KEY + "x")],
)
def test_security_v2_rejects_malformed_key_material(
    local_key: str, sec_key: str
) -> None:
    """Keys of the wrong width are rejected, never truncated."""
    credentials = replace(make_credentials(local_key=local_key), sec_key=sec_key)
    device = make_device()
    with pytest.raises(TuyaBLEDeviceError):
        device.initialize_with_credentials(credentials)


@pytest.mark.parametrize(
    ("sec_key", "flag", "accepted"),
    [(SEC_KEY, 15, True), (SEC_KEY, 5, False), (None, 5, True), (None, 15, False)],
)
async def test_session_frames_require_the_channel_flag(
    caplog: pytest.LogCaptureFixture, sec_key: str | None, flag: int, accepted: bool
) -> None:
    """Each channel only decrypts its own session flag."""
    credentials = v2_credentials(sec_key)
    firmware = (
        SecurityV2Firmware(credentials, SHADE_DATA)
        if sec_key is not None
        else FakeV4Firmware(credentials, SHADE_DATA)
    )
    device = make_device()
    device.set_ble_device_and_advertisement_data(
        device._ble_device,
        v4_advertisement(
            uuid=credentials.uuid.encode(),
            service=V2_SERVICE if sec_key is not None else CLASSIC_SERVICE,
        ),
    )
    device.initialize_with_credentials(credentials)
    with patch(RUNTIME_CONNECT, return_value=firmware):
        await device.update()
        await asyncio.wait_for(firmware.acknowledged.wait(), 1)
        assert device._session_key is not None
        data = report(klv(30, TuyaBLEDataPointType.DT_VALUE, pack(">i", 7)), sn=9)
        frame = encrypt_payload(
            device._session_key, flag, 99, 0, TuyaBLECode(0x8006), data
        )
        assert firmware.notify_handler is not None
        assert firmware.notify_char is not None
        with caplog.at_level(logging.DEBUG):
            # Delivered through the callback the runtime subscribed with.
            for packet in fragment(frame, protocol_version=4):
                firmware.notify_handler(firmware.notify_char, bytearray(packet))
            await drain_responses(device)
        await device.stop()

    datapoint = device.datapoints[30]
    if accepted:
        assert datapoint is not None
        assert datapoint.value == 7
    else:
        assert datapoint is None
        assert "Ignoring malformed BLE notification" in caplog.text
        assert f"Received security flag {flag}, expected" in caplog.text


# ---------------------------------------------------------------------------
# Runtime session
# ---------------------------------------------------------------------------


async def test_security_v2_session_end_to_end(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Connect, pair and read reports over the Security V2 channel."""
    credentials = v2_credentials()
    data = report(soil_sensor_datapoints(), sn=3)
    firmware = SecurityV2Firmware(credentials, data)
    device = make_device()
    device.set_ble_device_and_advertisement_data(
        device._ble_device,
        v4_advertisement(uuid=credentials.uuid.encode(), service=V2_SERVICE),
    )
    device.initialize_with_credentials(credentials)

    with caplog.at_level(logging.DEBUG):
        await run_session(device, firmware)

    assert not firmware.violations
    flags = [frame[3] for frame in firmware.received]
    assert flags[0] == 14
    assert set(flags[1:]) == {15}
    assert firmware.received[0][2] == b"\x00\x14"
    # Firmware matches only the UUID, so Security V2 keeps the classic frame.
    assert firmware.pair_payload == (
        credentials.uuid.encode()
        + LOCAL_KEY[:6].encode()
        + credentials.device_id.encode().ljust(22, b"\x00")
    )
    assert len(firmware.pair_payload) == PAIRING_REQUEST_LENGTH
    assert firmware.notify_kwargs == {"bluez": {"use_start_notify": True}}
    datapoint = device.datapoints[15]
    assert datapoint is not None
    assert datapoint.value == 87
    assert "security v2" in caplog.text
    assert SEC_KEY not in caplog.text
    assert LOCAL_KEY not in caplog.text


# ---------------------------------------------------------------------------
# Setup verification
# ---------------------------------------------------------------------------


async def test_v2_verification_with_unbound_advertisement() -> None:
    """An FD50 advert without the bound bit still verifies over Security V2."""
    credentials = v2_credentials()
    clients: list[FakeV4Firmware] = []
    establish = _probe_establish(
        clients, lambda: SecurityV2Firmware(credentials, SHADE_DATA)
    )
    stages: list[ProbeStage] = []
    with patch(PROBE_CONNECT, side_effect=establish):
        result = await async_detect_v4_profile(
            make_device()._ble_device,
            v4_advertisement(uuid=credentials.uuid.encode(), service=V2_SERVICE),
            credentials,
            frozenset({1, 3}),
            stages=stages,
        )
    assert result == ConnectionProfile.V4_20
    assert not stages
    assert len(clients) == 1
    assert [frame[3] for frame in clients[0].received] == [14, 15, 15]


async def test_classic_advertisement_ignores_cloud_sec_key() -> None:
    """A device not advertising Security V2 keeps the classic channel."""
    credentials = v2_credentials()
    clients: list[FakeV4Firmware] = []
    establish = _probe_establish(
        clients, lambda: FakeV4Firmware(credentials, SHADE_DATA)
    )
    with patch(PROBE_CONNECT, side_effect=establish):
        result = await async_detect_v4_profile(
            make_device()._ble_device,
            v4_advertisement(uuid=credentials.uuid.encode(), service=CLASSIC_SERVICE),
            credentials,
            frozenset({1, 3}),
        )
    assert result == ConnectionProfile.V4_20
    assert [frame[3] for frame in clients[0].received] == [4, 5, 5]
    assert credentials.sec_key == SEC_KEY


async def test_unbound_device_info_is_never_paired(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The DEVICE_INFO bound flag, not the advert, gates the pair request."""
    credentials = v2_credentials()
    clients: list[FakeV4Firmware] = []
    stages: list[ProbeStage] = []
    establish = _probe_establish(
        clients, lambda: SecurityV2Firmware(credentials, SHADE_DATA, bound=False)
    )
    with (
        caplog.at_level(logging.DEBUG),
        patch(PROBE_CONNECT, side_effect=establish),
        patch(
            "custom_components.tuya_ble.tuya_ble.connection_profile."
            "MTU_NEGOTIATION_TIMEOUT",
            0.01,
        ),
    ):
        result = await async_detect_v4_profile(
            make_device()._ble_device,
            v4_advertisement(uuid=credentials.uuid.encode(), service=V2_SERVICE),
            credentials,
            frozenset({1, 3}),
            stages=stages,
        )
    assert result is None
    assert stages[0] == ProbeStage.UNBOUND
    assert [frame[0] for frame in clients[0].received] == [0]
    assert (
        "Authenticated probe inconclusive at stage unbound "
        "(security v2, protocol 4.2)" in caplog.text
    )
    assert "Authenticated BLE probe failed: TuyaBLEDeviceError" in caplog.text
    for client in clients:
        assert TuyaBLECode.FUN_SENDER_PAIR.value not in [
            frame[0] for frame in client.received
        ]


async def test_malformed_sec_key_is_inconclusive() -> None:
    """A sec_key of the wrong width never opens a connection."""
    credentials = v2_credentials("short")
    stages: list[ProbeStage] = []
    with patch(PROBE_CONNECT, new=AsyncMock()) as connect:
        result = await async_detect_v4_profile(
            make_device()._ble_device,
            v4_advertisement(uuid=credentials.uuid.encode(), service=V2_SERVICE),
            credentials,
            frozenset({1, 3}),
            stages=stages,
        )
    assert result is None
    assert set(stages) == {ProbeStage.CREDENTIALS}
    connect.assert_not_called()


@pytest.mark.parametrize(
    ("advertisement", "expected"),
    [
        (CLASSIC_SERVICE, frozenset()),
        (b"\x41\x00\x00\x08abcdefgh", frozenset()),
    ],
)
async def test_ineligible_detection_reports_its_stage(
    advertisement: bytes, expected: frozenset[int]
) -> None:
    """A device that cannot be checked says so instead of a generic failure."""
    stages: list[ProbeStage] = []
    result = await async_detect_v4_profile(
        make_device()._ble_device,
        v4_advertisement(service=advertisement),
        v2_credentials(),
        expected,
        stages=stages,
    )
    assert result is None
    assert stages == [ProbeStage.INELIGIBLE]


async def test_unbound_classic_probe_reports_ineligible() -> None:
    """A classic advertisement without the bound flag is never probed."""
    stages: list[ProbeStage] = []
    adv = test_advertisement.advertisement(
        b"\x00abcdefgh", bytes((0x00, 3, 0, 0, 0, 0)) + bytes(16), SERVICE_UUID
    )
    outcome = await async_probe_authenticated_status(
        make_device()._ble_device, adv, v2_credentials(), "uuid", frozenset({1}), stages
    )
    assert outcome == ProbeOutcome.INCONCLUSIVE
    assert stages == [ProbeStage.INELIGIBLE]


class UnsupportedVersionFirmware(SecurityV2Firmware):
    """Reply to device info with a protocol version that is not supported."""

    def _reply(self, code: int, response_to: int, data: bytes, flag: int) -> int:
        if code == TuyaBLECode.FUN_SENDER_DEVICE_INFO.value:
            data = data[:2] + b"\x05\x00" + data[4:]
        return super()._reply(code, response_to, data, flag)


async def test_unsupported_device_version_reports_its_stage() -> None:
    """A device reporting an unsupported version is never paired."""
    credentials = v2_credentials()
    clients: list[FakeV4Firmware] = []
    stages: list[ProbeStage] = []
    establish = _probe_establish(
        clients, lambda: UnsupportedVersionFirmware(credentials, SHADE_DATA)
    )
    with patch(PROBE_CONNECT, side_effect=establish):
        outcome = await async_probe_authenticated_status(
            make_device()._ble_device,
            v4_advertisement(uuid=credentials.uuid.encode(), service=V2_SERVICE),
            credentials,
            credentials.uuid,
            frozenset({1, 3}),
            stages,
        )
    assert outcome == ProbeOutcome.INCONCLUSIVE
    assert stages == [ProbeStage.UNSUPPORTED]
    assert [frame[0] for frame in clients[0].received] == [0]


# ---------------------------------------------------------------------------
# Cloud
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"sec_key": SEC_KEY}, SEC_KEY),
        ({"secKey": SEC_KEY}, SEC_KEY),
        ({"sec_key": "", "secKey": SEC_KEY}, SEC_KEY),
        ({"sec_key": ""}, None),
        ({"sec_key": 123}, None),
        ({}, None),
    ],
)
def test_cloud_sec_key(fields: dict[str, Any], expected: str | None) -> None:
    """The sec_key is read from the raw record, and only its presence exported."""
    device = make_cloud_device(**fields)
    credentials = _build_credentials(device)
    assert credentials.sec_key == expected
    info = capture_cloud_info(device)
    assert info["sec_key_present"] is (expected is not None)
    assert SEC_KEY not in repr(info)
    assert filter_cloud_info(info)["sec_key_present"] is (expected is not None)


# ---------------------------------------------------------------------------
# Config flow and entry data
# ---------------------------------------------------------------------------


def _discovery(service: bytes) -> Any:
    info = discovery()
    adv = test_advertisement.v4_advertisement(service)
    info.service_data = adv.service_data
    info.manufacturer_data = adv.manufacturer_data
    info.advertisement = cast(Any, adv)
    info.device = make_device()._ble_device
    return info


@pytest.mark.parametrize(
    ("service", "stored"), [(V2_SERVICE, True), (CLASSIC_SERVICE, False)]
)
async def test_entry_stores_sec_key_only_for_security_v2(
    hass: HomeAssistant, service: bytes, stored: bool
) -> None:
    """Only a Security V2 device persists the cloud sec_key."""
    flow = build_flow(hass)
    info = _discovery(service)
    flow.discovery_info = cast(Any, info)
    flow._manager = cast(Any, FakeManager(v2_credentials()))
    with (
        patch.object(flow, "_async_scan_device", return_value=info),
        patch(DETECT, new=AsyncMock(side_effect=verified_by(ConnectionProfile.V4_20))),
    ):
        result = await flow.async_step_discovered_device({})
        assert result["type"] == FlowResultType.SHOW_PROGRESS
        assert flow._probe_task is not None
        await flow._probe_task
        result = await flow.async_step_probe_finish()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    data = cast(dict[str, Any], result["data"])
    assert (data.get(CONF_SEC_KEY) == SEC_KEY) is stored
    assert (CONF_SEC_KEY in data) is stored


def test_entry_credentials_restore_sec_key() -> None:
    """Runtime credentials carry the stored sec_key, or none."""
    base = {
        "uuid": "1234567890abcdef",
        "local_key": LOCAL_KEY,
        "device_id": "device123",
        "category": "cl",
        "product_id": "jetlwvfm",
    }
    entry = MockConfigEntry(domain=DOMAIN, data={**base, CONF_SEC_KEY: SEC_KEY})
    assert _build_credentials_from_entry(entry).sec_key == SEC_KEY
    entry = MockConfigEntry(domain=DOMAIN, data=base)
    assert _build_credentials_from_entry(entry).sec_key is None


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("security_v2", [True, False])
async def test_diagnostics_report_sec_key_presence_only(
    hass: HomeAssistant, security_v2: bool
) -> None:
    """Diagnostics show whether a sec_key exists and is used, never its value."""
    data = _entry_data()
    data[CONF_CLOUD_INFO] = {"category": "wk", "sec_key_present": True}
    if security_v2:
        data[CONF_SEC_KEY] = SEC_KEY
    entry = _add_entry(hass, data=data)
    device = TuyaBLEDevice(
        FakeBLEManager(make_credentials()),
        cast(Any, SimpleNamespace(address="AA:BB:CC:DD:EE:FF", name="TestDevice")),
        cast(Any, SimpleNamespace(rssi=-60, service_data={}, manufacturer_data={})),
    )
    device.initialize_with_credentials(v2_credentials(SEC_KEY if security_v2 else None))
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = TuyaBLEData(
        title="Device",
        device=device,
        product=MagicMock(),
        manager=MagicMock(),
        coordinator=MagicMock(),
    )

    payload = await async_get_config_entry_diagnostics(hass, entry)

    assert payload["cloud_info"]["sec_key_present"] is True
    assert payload["identity"]["sec_key_stored"] is security_v2
    assert payload["device"]["security_v2"] is security_v2
    assert SEC_KEY not in repr(payload)


class ActivationOnlyFirmware(SecurityV2Firmware):
    """Answer device info with an activation-only flag the integration lacks."""

    def __init__(
        self, credentials: TuyaBLEDeviceCredentials, report_data: bytes
    ) -> None:
        super().__init__(credentials, report_data)
        self._keys[11] = b"K" * 16

    def _reply(self, code: int, response_to: int, data: bytes, flag: int) -> int:
        if code == TuyaBLECode.FUN_SENDER_DEVICE_INFO.value:
            flag = 11
        return super()._reply(code, response_to, data, flag)


async def test_unexpected_reply_flag_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A reply with a flag the handshake cannot use is named in the log."""
    credentials = v2_credentials()
    clients: list[FakeV4Firmware] = []
    establish = _probe_establish(
        clients, lambda: ActivationOnlyFirmware(credentials, SHADE_DATA)
    )
    stages: list[ProbeStage] = []
    with (
        caplog.at_level(logging.DEBUG),
        patch(PROBE_CONNECT, side_effect=establish),
        patch("custom_components.tuya_ble.tuya_ble.probe.PROBE_TIMEOUT", 0.2),
    ):
        outcome = await async_probe_authenticated_status(
            make_device()._ble_device,
            v4_advertisement(uuid=credentials.uuid.encode(), service=V2_SERVICE),
            credentials,
            credentials.uuid,
            frozenset({1, 3}),
            stages,
        )
    assert outcome == ProbeOutcome.RETRYABLE
    assert stages == [ProbeStage.DEVICE_INFO]
    assert "Received security flag 11, expected 14" in caplog.text


async def test_classic_characteristics_keep_default_notifications() -> None:
    """Only the FD50 characteristics request BlueZ StartNotify."""
    credentials = make_credentials()
    firmware = FakeV4Firmware(
        credentials,
        report(soil_sensor_datapoints(), sn=3),
        characteristics=(CHARACTERISTIC_NOTIFY, CHARACTERISTIC_WRITE),
    )
    device = make_device()
    device.set_ble_device_and_advertisement_data(
        device._ble_device, v4_advertisement(uuid=credentials.uuid.encode())
    )
    device.initialize_with_credentials(credentials)
    await run_session(device, firmware)
    assert firmware.notify_char == CHARACTERISTIC_NOTIFY
    assert not firmware.notify_kwargs


async def test_runtime_never_pairs_an_unbound_v4_device(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unverified entry does not pair a device that reports it is unbound."""
    credentials = v2_credentials()
    firmware = SecurityV2Firmware(credentials, SHADE_DATA, bound=False)
    device = make_device()
    device.set_ble_device_and_advertisement_data(
        device._ble_device,
        v4_advertisement(uuid=credentials.uuid.encode(), service=V2_SERVICE),
    )
    device.initialize_with_credentials(credentials)
    with (
        caplog.at_level(logging.DEBUG),
        patch(RUNTIME_CONNECT, return_value=firmware),
        pytest.raises(BLE_CONNECTION_EXCEPTIONS),
    ):
        await device.update()
    await device.stop()
    codes = {frame[0] for frame in firmware.received}
    assert TuyaBLECode.FUN_SENDER_PAIR.value not in codes
    assert "not added to an account; not pairing" in caplog.text


class RefusingPairFirmware(SecurityV2Firmware):
    """Refuse the pair request explicitly, as for a mismatched device ID."""

    def _answer(self, seq_num: int, code: int, data: bytes, flag: int) -> None:
        if code == TuyaBLECode.FUN_SENDER_PAIR.value:
            self._reply(code, seq_num, b"\x01", 5)
            return
        super()._answer(seq_num, code, data, flag)


async def test_explicit_pair_refusal_reports_its_own_stage() -> None:
    """Only a refusal, not a timeout, is reported as a rejected login."""
    credentials = v2_credentials()
    clients: list[FakeV4Firmware] = []
    stages: list[ProbeStage] = []
    establish = _probe_establish(
        clients, lambda: RefusingPairFirmware(credentials, SHADE_DATA)
    )
    with patch(PROBE_CONNECT, side_effect=establish):
        outcome = await async_probe_authenticated_status(
            make_device()._ble_device,
            v4_advertisement(uuid=credentials.uuid.encode(), service=V2_SERVICE),
            credentials,
            credentials.uuid,
            frozenset({1, 3}),
            stages,
        )
    assert outcome == ProbeOutcome.REJECTED
    assert stages == [ProbeStage.PAIR_REJECTED]


class AdvertisementRaceFirmware(SecurityV2Firmware):
    """Let an advertisement rewrite the advertised state mid-handshake."""

    def __init__(
        self,
        credentials: TuyaBLEDeviceCredentials,
        report_data: bytes,
        device: TuyaBLEDevice,
        *,
        bound: bool,
    ) -> None:
        super().__init__(credentials, report_data, bound=bound)
        self.device = device

    def _reply(self, code: int, response_to: int, data: bytes, flag: int) -> int:
        seq = super()._reply(code, response_to, data, flag)
        if code == TuyaBLECode.FUN_SENDER_DEVICE_INFO.value:
            # Queued after the reply's fragments, so it runs once the parser has
            # stored the reply and before the handshake task resumes.
            asyncio.get_running_loop().call_soon(self._advertisement_arrives)
        return seq

    def _advertisement_arrives(self) -> None:
        # Test setup: the state an advertisement decode would leave behind,
        # including a classic record's protocol version for the unbound case.
        self.device._is_bound = not self.bound
        if not self.bound:
            self.device._protocol_version = 3


@pytest.mark.parametrize("bound", [True, False])
async def test_pairing_follows_device_info_not_advertisements(
    caplog: pytest.LogCaptureFixture, bound: bool
) -> None:
    """Only the DEVICE_INFO reply of this handshake decides whether to pair."""
    credentials = v2_credentials()
    device = make_device()
    device.set_ble_device_and_advertisement_data(
        device._ble_device,
        v4_advertisement(uuid=credentials.uuid.encode(), service=V2_SERVICE),
    )
    device.initialize_with_credentials(credentials)
    firmware = AdvertisementRaceFirmware(credentials, SHADE_DATA, device, bound=bound)
    with caplog.at_level(logging.DEBUG):
        if bound:
            await run_session(device, firmware)
        else:
            with (
                patch(RUNTIME_CONNECT, return_value=firmware),
                pytest.raises(BLE_CONNECTION_EXCEPTIONS),
            ):
                await device.update()
            await device.stop()
    paired = TuyaBLECode.FUN_SENDER_PAIR.value in {
        frame[0] for frame in firmware.received
    }
    assert paired is bound
    assert ("not added to an account; not pairing" in caplog.text) is not bound
    assert firmware.received[0][0] == TuyaBLECode.FUN_SENDER_DEVICE_INFO.value


class SilentFirmware(FakeV4Firmware):
    """Never answer the device-info request, as for a frame it cannot decrypt."""

    def _answer(self, seq_num: int, code: int, data: bytes, flag: int) -> None:
        if code != TuyaBLECode.FUN_SENDER_DEVICE_INFO.value:
            super()._answer(seq_num, code, data, flag)


@pytest.mark.parametrize(
    ("service", "expected"),
    [
        (V2_SERVICE, ProbeStage.MISSING_SEC_KEY),
        (CLASSIC_SERVICE, ProbeStage.DEVICE_INFO),
    ],
)
async def test_missing_sec_key_explains_a_silent_security_v2_device(
    service: bytes, expected: ProbeStage
) -> None:
    """A Security V2 device with no cloud sec_key reports the missing key."""
    credentials = v2_credentials(None)
    clients: list[FakeV4Firmware] = []
    stages: list[ProbeStage] = []
    establish = _probe_establish(
        clients, lambda: SilentFirmware(credentials, SHADE_DATA)
    )
    with (
        patch(PROBE_CONNECT, side_effect=establish),
        patch("custom_components.tuya_ble.tuya_ble.probe.PROBE_TIMEOUT", 0.2),
    ):
        outcome = await async_probe_authenticated_status(
            make_device()._ble_device,
            v4_advertisement(uuid=credentials.uuid.encode(), service=service),
            credentials,
            credentials.uuid,
            frozenset({1, 3}),
            stages,
        )
    assert outcome == ProbeOutcome.RETRYABLE
    assert stages == [expected]
    # The classic login is still attempted.
    assert [frame[3] for frame in clients[0].received] == [4]
