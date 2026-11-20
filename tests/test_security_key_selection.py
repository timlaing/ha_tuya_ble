"""Login-channel selection: classic flags 4/5 versus Security V2 flags 14/15.

FD50 frame control byte 1 carries two bits (TuyaOS ``tuya_ble_main.c``):
``0x04`` Security V2 supported and ``0x08`` Security V2 switched on. Switched-on
firmware rejects every classic frame. Firmware that only supports it keeps the
classic login, so verification tries it first and Security V2 only when the
device never answered. The verified channel is saved with the profile. Several
upstream products (``qcrilcpr``, ``mknd4lci``, ``gvygg3m8``, ``c2twgywo``,
``cq7xquhj``) use the classic login although the cloud returns a ``sec_key``.
All vectors are synthetic.
"""

# pylint: disable=protected-access
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
import logging
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tuya_ble import _build_credentials_from_entry
from custom_components.tuya_ble.const import (
    CONF_CONNECTION_PROFILE,
    CONF_LOCAL_KEY,
    CONF_SEC_KEY,
    CONF_SECURITY_MODE,
    DOMAIN,
)
from custom_components.tuya_ble.diagnostics import async_get_config_entry_diagnostics
from custom_components.tuya_ble.tuya_ble import (
    ConnectionProfile,
    ProbeOutcome,
    ProbeStage,
    SecurityMode,
    async_detect_v4_credentials,
    decode_fd50_identity_record,
    decode_tuya_ble_advertisement,
)
from custom_components.tuya_ble.tuya_ble.manager import TuyaBLEDeviceCredentials
from custom_components.tuya_ble.tuya_ble.security import security_modes
from tests import test_advertisement
from tests.conftest import make_device, verified_by
from tests.protocol_harness import reassemble, v4_advertisement
from tests.test_config_flow import FakeManager, build_flow
from tests.test_config_flow_options_credentials import (
    NEW_LOCAL_KEY,
    NEW_SEC_KEY,
    options_flow,
    record_only,
    replace_keys,
)
from tests.test_config_flow_profiles import configured_flow
from tests.test_config_flow_v4 import discovery
from tests.test_diagnostics import _add_entry, _entry_data
from tests.test_protocol_v4 import FakeV4Firmware, device_info_response, run_session
from tests.test_security_v2 import (
    LOCAL_KEY,
    SEC_KEY,
    SHADE_DATA,
    SecurityV2Firmware,
    v2_credentials,
)

ENABLED = b"\x41\x0c\x00\x08abcdefgh"
SUPPORTED = b"\x41\x04\x00\x08abcdefgh"
NO_V2 = b"\x41\x00\x00\x08abcdefgh"
PROBE_CONNECT = "custom_components.tuya_ble.tuya_ble.probe.establish_connection"
DETECT = "custom_components.tuya_ble.config_flow.async_detect_v4_credentials"
EXPECTED = frozenset({1, 3})


def _drop_unknown_flag(firmware: FakeV4Firmware, dropped: list[int]) -> None:
    """Record the flag of a frame the firmware has no key for."""
    dropped.append(reassemble(firmware._frame)[0])


class ClassicOnlyFirmware(FakeV4Firmware):
    """Silently drop Security V2 frames, as firmware without Security V2."""

    def __init__(
        self,
        credentials: TuyaBLEDeviceCredentials,
        report_data: bytes,
        *,
        bound: bool = True,
    ) -> None:
        super().__init__(credentials, report_data)
        self.bound = bound
        self.dropped: list[int] = []

    async def write_gatt_char(
        self, gatt_char: str, data: bytes, response: bool
    ) -> None:
        try:
            await super().write_gatt_char(gatt_char, data, response)
        except KeyError:
            _drop_unknown_flag(self, self.dropped)

    def _answer(self, seq_num: int, code: int, data: bytes, flag: int) -> None:
        if code == 0 and not self.bound:
            info = bytearray(device_info_response(self._srand))
            info[5] = 0
            self._reply(code, seq_num, bytes(info), flag)
            return
        super()._answer(seq_num, code, data, flag)


class V2OnlyFirmware(SecurityV2Firmware):
    """Silently drop classic frames, as firmware answering only Security V2."""

    def __init__(
        self, credentials: TuyaBLEDeviceCredentials, report_data: bytes
    ) -> None:
        super().__init__(credentials, report_data)
        self.dropped: list[int] = []

    async def write_gatt_char(
        self, gatt_char: str, data: bytes, response: bool
    ) -> None:
        try:
            await super().write_gatt_char(gatt_char, data, response)
        except KeyError:
            _drop_unknown_flag(self, self.dropped)


def _establish(
    clients: list[Any], build: Callable[[], Any]
) -> Callable[..., Awaitable[Any]]:
    """Return a probe connector handing out a fresh firmware per attempt."""

    async def establish(*_args: Any, **kwargs: Any) -> Any:
        client = build()
        kwargs["capture_client"](client)
        clients.append(client)
        return client

    return establish


def _fast_probe() -> Any:
    """Shorten silent-attempt and MTU-wait timeouts for multi-profile runs."""
    return (
        patch("custom_components.tuya_ble.tuya_ble.probe.PROBE_TIMEOUT", 0.05),
        patch(
            "custom_components.tuya_ble.tuya_ble.connection_profile."
            "MTU_NEGOTIATION_TIMEOUT",
            0.01,
        ),
    )


async def _detect(
    service: bytes,
    credentials: TuyaBLEDeviceCredentials,
    build: Callable[[], Any],
    clients: list[Any],
    stages: list[ProbeStage],
) -> TuyaBLEDeviceCredentials | None:
    timeout, mtu = _fast_probe()
    with timeout, mtu, patch(PROBE_CONNECT, side_effect=_establish(clients, build)):
        return await async_detect_v4_credentials(
            make_device()._ble_device,
            v4_advertisement(uuid=credentials.uuid.encode(), service=service),
            credentials,
            EXPECTED,
            stages=stages,
        )


def _sent_flags(clients: list[Any]) -> list[int]:
    """Every frame flag the integration sent, answered or dropped."""
    return [
        flag
        for client in clients
        for flag in [frame[3] for frame in client.received] + client.dropped
    ]


# ---------------------------------------------------------------------------
# Advertisement and selection order
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("service", "supported", "enabled"),
    [
        (ENABLED, True, True),
        (SUPPORTED, True, False),
        (b"\x41\x08\x00\x08abcdefgh", False, True),
        (NO_V2, False, False),
    ],
)
def test_fd50_security_v2_state_bits(
    service: bytes, supported: bool, enabled: bool
) -> None:
    """Byte 1 bit 2 announces support and bit 3 that Security V2 is on."""
    info = decode_tuya_ble_advertisement(test_advertisement.v4_advertisement(service))
    assert info is not None
    assert info.security_v2 is supported
    assert info.security_v2_enabled is enabled
    record = decode_fd50_identity_record(cast(Any, record_only(service)))
    assert record is not None
    assert record.security_v2 is supported
    assert record.security_v2_enabled is enabled


def test_classic_advertisement_never_enables_security_v2() -> None:
    """A201 records carry no Security V2 state."""
    info = decode_tuya_ble_advertisement(
        test_advertisement.classic_advertisement(b"\x00abcdefgh")
    )
    assert info is not None
    assert not info.security_v2
    assert not info.security_v2_enabled


@pytest.mark.parametrize(
    ("supported", "enabled", "sec_key", "expected"),
    [
        (True, True, SEC_KEY, [SecurityMode.V2]),
        (True, False, SEC_KEY, [SecurityMode.CLASSIC, SecurityMode.V2]),
        (False, True, SEC_KEY, [SecurityMode.CLASSIC]),
        (False, False, SEC_KEY, [SecurityMode.CLASSIC]),
        (True, True, None, [SecurityMode.CLASSIC]),
        (True, False, None, [SecurityMode.CLASSIC]),
    ],
)
def test_security_modes_follow_the_advertised_state(
    supported: bool,
    enabled: bool,
    sec_key: str | None,
    expected: list[SecurityMode],
) -> None:
    """Only switched-on firmware skips the classic login."""
    assert security_modes(supported, enabled, sec_key) == expected


# ---------------------------------------------------------------------------
# Probe-driven selection
# ---------------------------------------------------------------------------


async def test_supported_security_v2_prefers_the_classic_login() -> None:
    """A device that accepts the classic login verifies without Security V2."""
    credentials = v2_credentials()
    clients: list[Any] = []
    stages: list[ProbeStage] = []
    result = await _detect(
        SUPPORTED,
        credentials,
        lambda: ClassicOnlyFirmware(credentials, SHADE_DATA),
        clients,
        stages,
    )
    assert result is not None
    assert result.connection_profile == ConnectionProfile.V4_20
    assert result.security_mode == SecurityMode.CLASSIC
    # The key is kept so a later Reconfigure can verify Security V2.
    assert result.sec_key == SEC_KEY
    assert credentials.security_mode is None
    assert len(clients) == 1
    assert _sent_flags(clients) == [4, 5, 5]
    assert not stages


async def test_supported_security_v2_falls_back_when_classic_is_ignored(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Security V2 is tried only after every classic attempt went unanswered."""
    credentials = v2_credentials()
    clients: list[Any] = []
    stages: list[ProbeStage] = []
    with caplog.at_level(logging.DEBUG):
        result = await _detect(
            SUPPORTED,
            credentials,
            lambda: V2OnlyFirmware(credentials, SHADE_DATA),
            clients,
            stages,
        )
    assert result is not None
    assert result.connection_profile == ConnectionProfile.V4_20
    assert result.security_mode == SecurityMode.V2
    assert len(clients) == len(ConnectionProfile) + 1
    assert set(_sent_flags(clients[:-1])) == {4}
    assert _sent_flags(clients[-1:]) == [14, 15, 15]
    # Classic silence on a device whose sec_key exists is not a missing key.
    assert ProbeStage.DEVICE_INFO in stages
    assert ProbeStage.MISSING_SEC_KEY not in stages
    assert "with the v2 login" in caplog.text
    assert SEC_KEY not in caplog.text
    assert LOCAL_KEY not in caplog.text


async def test_enabled_security_v2_never_sends_a_classic_frame() -> None:
    """Switched-on firmware rejects flags below 14, so none are sent."""
    credentials = v2_credentials()
    clients: list[Any] = []
    stages: list[ProbeStage] = []
    result = await _detect(
        ENABLED,
        credentials,
        lambda: ClassicOnlyFirmware(credentials, SHADE_DATA),
        clients,
        stages,
    )
    assert result is None
    assert len(clients) == len(ConnectionProfile)
    assert set(_sent_flags(clients)) == {14}


async def test_classic_answer_proves_the_key_and_stops_fallback() -> None:
    """A classic device-info reply is never followed by Security V2 attempts."""
    credentials = v2_credentials()
    clients: list[Any] = []
    stages: list[ProbeStage] = []
    result = await _detect(
        SUPPORTED,
        credentials,
        lambda: ClassicOnlyFirmware(credentials, SHADE_DATA, bound=False),
        clients,
        stages,
    )
    assert result is None
    assert ProbeStage.UNBOUND in stages
    assert len(clients) == len(ConnectionProfile)
    assert set(_sent_flags(clients)) == {4}


async def test_pair_refusal_never_tries_another_login() -> None:
    """A refused pair proves the key was decrypted, so detection stops."""
    credentials = v2_credentials()
    with patch(
        "custom_components.tuya_ble.tuya_ble.probe.async_probe_authenticated_status",
        new=AsyncMock(return_value=ProbeOutcome.REJECTED),
    ) as probe:
        result = await async_detect_v4_credentials(
            make_device()._ble_device,
            v4_advertisement(uuid=credentials.uuid.encode(), service=SUPPORTED),
            credentials,
            EXPECTED,
        )
    assert result is None
    probe.assert_awaited_once()
    assert probe.call_args.args[2].security_mode == SecurityMode.CLASSIC


async def test_device_without_security_v2_ignores_the_cloud_key() -> None:
    """Without the supported bit, the classic login is the only one tried."""
    credentials = v2_credentials()
    clients: list[Any] = []
    stages: list[ProbeStage] = []
    result = await _detect(
        NO_V2,
        credentials,
        lambda: V2OnlyFirmware(credentials, SHADE_DATA),
        clients,
        stages,
    )
    assert result is None
    assert len(clients) == len(ConnectionProfile)
    assert set(_sent_flags(clients)) == {4}


# ---------------------------------------------------------------------------
# Persistence and runtime
# ---------------------------------------------------------------------------


def _entry(**extra: Any) -> MockConfigEntry:
    data = {
        "uuid": "1234567890abcdef",
        "local_key": LOCAL_KEY,
        "device_id": "device123",
        "category": "cl",
        "product_id": "jetlwvfm",
        **extra,
    }
    return MockConfigEntry(domain=DOMAIN, data=data)


@pytest.mark.parametrize(
    ("extra", "security_v2"),
    [
        ({CONF_SEC_KEY: SEC_KEY, CONF_SECURITY_MODE: "classic"}, False),
        ({CONF_SEC_KEY: SEC_KEY, CONF_SECURITY_MODE: "v2"}, True),
        # Entries saved before the mode existed keep their behaviour.
        ({CONF_SEC_KEY: SEC_KEY}, True),
        ({}, False),
    ],
)
def test_runtime_follows_the_saved_security_mode(
    extra: dict[str, Any], security_v2: bool
) -> None:
    """A saved classic login leaves a stored sec_key unused."""
    credentials = _build_credentials_from_entry(_entry(**extra))
    assert credentials.sec_key == extra.get(CONF_SEC_KEY)
    device = make_device()
    device.initialize_with_credentials(credentials)
    assert device.security_v2 is security_v2


async def test_saved_classic_mode_runs_a_classic_session() -> None:
    """Runtime with a stored sec_key and classic mode sends flags 4 and 5."""
    credentials = _build_credentials_from_entry(
        _entry(**{
            CONF_SEC_KEY: SEC_KEY,
            CONF_SECURITY_MODE: "classic",
            CONF_CONNECTION_PROFILE: "v4_20",
        })
    )
    firmware = ClassicOnlyFirmware(credentials, SHADE_DATA)
    device = make_device()
    device.set_ble_device_and_advertisement_data(
        device._ble_device,
        v4_advertisement(uuid=credentials.uuid.encode(), service=SUPPORTED),
    )
    device.initialize_with_credentials(credentials)
    await run_session(device, firmware)
    assert not firmware.violations
    assert not firmware.dropped
    flags = [frame[3] for frame in firmware.received]
    assert flags[0] == 4
    assert set(flags[1:]) == {5}


def _discovery(service: bytes) -> Any:
    info = discovery()
    adv = test_advertisement.v4_advertisement(service)
    info.service_data = adv.service_data
    info.manufacturer_data = adv.manufacturer_data
    info.advertisement = cast(Any, adv)
    info.device = make_device()._ble_device
    return info


async def test_setup_saves_the_verified_classic_login(hass: HomeAssistant) -> None:
    """Setup probes the real handshake and saves the channel that answered."""
    credentials = replace(
        v2_credentials(), category="cl", product_id="jetlwvfm", device_name="Shade"
    )
    flow = build_flow(hass)
    info = _discovery(SUPPORTED)
    flow.discovery_info = cast(Any, info)
    flow._manager = cast(Any, FakeManager(credentials))
    clients: list[Any] = []
    with (
        patch.object(flow, "_async_scan_device", return_value=info),
        patch(
            PROBE_CONNECT,
            side_effect=_establish(
                clients, lambda: ClassicOnlyFirmware(credentials, SHADE_DATA)
            ),
        ),
    ):
        result = await flow.async_step_discovered_device({})
        assert result["type"] == FlowResultType.SHOW_PROGRESS
        assert flow._probe_task is not None
        await flow._probe_task
        result = await flow.async_step_probe_finish()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    data = cast(dict[str, Any], result["data"])
    assert data[CONF_CONNECTION_PROFILE] == "v4_20"
    assert data[CONF_SECURITY_MODE] == "classic"
    assert data[CONF_SEC_KEY] == SEC_KEY
    assert _sent_flags(clients) == [4, 5, 5]


@pytest.mark.parametrize(
    ("mode", "expected"),
    [(SecurityMode.V2, "v2"), (SecurityMode.CLASSIC, "classic"), (None, "classic")],
)
async def test_reconfigure_saves_the_verified_login(
    hass: HomeAssistant, mode: SecurityMode | None, expected: str
) -> None:
    """Reconfigure updates the saved channel together with the profile."""
    flow, entry = await configured_flow(hass)
    info = discovery()
    info.device = make_device()._ble_device
    with (
        patch.object(flow, "_async_scan_device", return_value=info),
        patch(
            DETECT,
            new=AsyncMock(
                side_effect=verified_by(ConnectionProfile.V2_MTU, cast(Any, mode))
            ),
        ),
        patch.object(hass.config_entries, "async_unload", return_value=True),
        patch.object(hass.config_entries, "async_reload", return_value=True),
    ):
        await flow.async_step_reconfigure({})
        assert flow._probe_task is not None
        await flow._probe_task
        result = await flow.async_step_probe_finish()
        await hass.async_block_till_done()
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_CONNECTION_PROFILE] == "v2_mtu"
    assert entry.data[CONF_SECURITY_MODE] == expected


@pytest.mark.parametrize("mode", ["classic", None])
async def test_diagnostics_report_the_saved_security_mode(
    hass: HomeAssistant, mode: str | None
) -> None:
    """Diagnostics name the saved channel without exposing the key."""
    data = _entry_data()
    data[CONF_SEC_KEY] = SEC_KEY
    if mode is not None:
        data[CONF_SECURITY_MODE] = mode
    entry = _add_entry(hass, data=data)
    payload = await async_get_config_entry_diagnostics(hass, entry)
    assert payload["identity"][CONF_SECURITY_MODE] == mode
    assert payload["identity"]["sec_key_stored"] is True
    assert SEC_KEY not in repr(payload)


# ---------------------------------------------------------------------------
# Options-flow key replacement
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("saved", "service", "sec_key", "expected"),
    [
        # Switched-on firmware rejects the classic login.
        ("classic", ENABLED, NEW_SEC_KEY, "v2"),
        ("v2", ENABLED, NEW_SEC_KEY, "v2"),
        # Supported-only firmware keeps the verified login.
        ("classic", SUPPORTED, NEW_SEC_KEY, "classic"),
        ("v2", SUPPORTED, NEW_SEC_KEY, "v2"),
        # The FD50 identity record alone carries the same bits.
        ("classic", record_only(ENABLED), NEW_SEC_KEY, "v2"),
        # Without an advertisement the verified login stays.
        ("classic", None, NEW_SEC_KEY, "classic"),
        # Without a security key only the classic login exists.
        ("v2", ENABLED, None, None),
        (None, ENABLED, NEW_SEC_KEY, None),
    ],
)
async def test_key_replacement_keeps_an_allowed_login(
    hass: HomeAssistant,
    saved: str | None,
    service: bytes | Any,
    sec_key: str | None,
    expected: str | None,
) -> None:
    """Replacing the keys never keeps a login the device rules out."""
    flow, entry = options_flow(hass)
    if saved is not None:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_SECURITY_MODE: saved}
        )
    user_input = {CONF_LOCAL_KEY: NEW_LOCAL_KEY}
    if sec_key is not None:
        user_input[CONF_SEC_KEY] = sec_key
    result, _ = await replace_keys(hass, flow, user_input, service)
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.data.get(CONF_SECURITY_MODE) == expected
    assert entry.data.get(CONF_SEC_KEY) == sec_key


@pytest.mark.parametrize(
    ("changes", "saved"),
    [
        ({}, True),
        ({CONF_LOCAL_KEY: NEW_LOCAL_KEY}, False),
        ({CONF_SEC_KEY: NEW_SEC_KEY}, False),
    ],
)
async def test_reconfigure_never_saves_a_login_for_replaced_keys(
    hass: HomeAssistant, changes: dict[str, str], saved: bool
) -> None:
    """Keys replaced in the options while Reconfigure probes discard its result."""
    flow, entry = await configured_flow(hass)
    info = discovery()
    info.device = make_device()._ble_device
    assert entry.data[CONF_SECURITY_MODE] == "classic"
    detect = verified_by(ConnectionProfile.V2_MTU, SecurityMode.V2)

    async def replace_keys_while_probing(*args: Any, **kwargs: Any) -> Any:
        # The options flow writes a new pair before the probe finishes.
        hass.config_entries.async_update_entry(entry, data={**entry.data, **changes})
        return await detect(*args, **kwargs)

    with (
        patch.object(flow, "_async_scan_device", return_value=info),
        patch(DETECT, side_effect=replace_keys_while_probing),
        patch.object(hass.config_entries, "async_unload", return_value=True),
        patch.object(
            hass.config_entries, "async_reload", return_value=True
        ) as reload_entry,
    ):
        await flow.async_step_reconfigure({})
        assert flow._probe_task is not None
        await flow._probe_task
        result = await flow.async_step_probe_finish()
        await hass.async_block_till_done()
    if saved:
        assert result["type"] == FlowResultType.ABORT
        assert entry.data[CONF_SECURITY_MODE] == "v2"
        return
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "reconfigure_keys_changed"}
    assert entry.data[CONF_SECURITY_MODE] == "classic"
    assert entry.data[CONF_CONNECTION_PROFILE] == "v4_20"
    reload_entry.assert_awaited_once_with(entry.entry_id)
