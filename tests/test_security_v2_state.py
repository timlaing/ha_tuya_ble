"""Announced FD50 Security V2 state (#183): diagnostics and logging only.

TuyaOS BLE firmware announces Security V2 "supported" (FD50 byte 1 ``0x04``,
DEVICE_INFO byte 54 ``0x02``) separately from "enabled" (``0x08``/``0x04``).
Neither changes the key choice. All vectors are synthetic.
"""

# pylint: disable=protected-access
from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

from homeassistant.core import HomeAssistant
import pytest

from custom_components.tuya_ble.const import CONF_SEC_KEY, DOMAIN
from custom_components.tuya_ble.devices import TuyaBLEData
from custom_components.tuya_ble.diagnostics import async_get_config_entry_diagnostics
from custom_components.tuya_ble.tuya_ble import (
    ProbeOutcome,
    TuyaBLEDevice,
    async_probe_authenticated_status,
)
from custom_components.tuya_ble.tuya_ble.const import TuyaBLECode
from custom_components.tuya_ble.tuya_ble.manager import TuyaBLEDeviceCredentials
from custom_components.tuya_ble.tuya_ble.security import DeviceInfoSecurity
from tests import test_advertisement
from tests.conftest import FakeBLEManager, make_device
from tests.protocol_harness import (
    ProtocolHarness,
    device_info_data,
    encrypt_payload,
    frame_packet0,
    v4_advertisement,
)
from tests.test_diagnostics import _add_entry, _entry_data
from tests.test_protocol_v4 import (
    FakeV4Firmware,
    report,
    run_session,
    soil_sensor_datapoints,
)
from tests.test_security_v2 import (
    CLASSIC_SERVICE,
    LOCAL_KEY,
    PROBE_CONNECT,
    SEC_KEY,
    SHADE_DATA,
    SecurityV2Firmware,
    _probe_establish,
    v2_credentials,
)

# Battery BLE Roller (#39): Security V2 supported, not enabled.
V2_SUPPORTED_SERVICE = b"\x41\x04\x00\x08abcdefgh"
# Parkside KC8B105 (#181): bound, Security V2 supported and enabled.
V2_ENABLED_SERVICE = b"\x49\x0c\x00\x08abcdefgh"


class ReportingFirmware(SecurityV2Firmware):
    """Answer DEVICE_INFO with a chosen Security V2 byte (offset 54)."""

    def __init__(
        self, credentials: TuyaBLEDeviceCredentials, report_data: bytes, flags: int
    ) -> None:
        super().__init__(credentials, report_data)
        self.flags = flags

    def _reply(self, code: int, response_to: int, data: bytes, flag: int) -> int:
        if code == TuyaBLECode.FUN_SENDER_DEVICE_INFO.value:
            info = bytearray(data)
            info[54] = self.flags
            data = bytes(info)
        return super()._reply(code, response_to, data, flag)


@pytest.mark.parametrize(
    ("flags", "supported", "enabled"),
    [(0x00, False, False), (0x02, True, False), (0x07, True, True)],
)
async def test_device_info_reports_security_v2_state(
    caplog: pytest.LogCaptureFixture, flags: int, supported: bool, enabled: bool
) -> None:
    """A long DEVICE_INFO reply records byte 54 without changing the channel."""
    credentials = v2_credentials()
    firmware = ReportingFirmware(
        credentials, report(soil_sensor_datapoints(), sn=3), flags
    )
    device = make_device()
    device.set_ble_device_and_advertisement_data(
        device._ble_device,
        v4_advertisement(uuid=credentials.uuid.encode(), service=V2_ENABLED_SERVICE),
    )
    device.initialize_with_credentials(credentials)

    with caplog.at_level(logging.DEBUG):
        await run_session(device, firmware)

    assert not firmware.violations
    # The reported state never selects the key: flags 14/15 throughout.
    assert {frame[3] for frame in firmware.received} == {14, 15}
    state = device.device_info_security
    assert state is not None
    assert state.raw == flags
    assert (state.security_v2, state.security_v2_enabled) == (supported, enabled)
    assert f"reported V2 supported {supported}, enabled {enabled}" in caplog.text


@pytest.mark.parametrize("extra", [0, 8])
async def test_short_device_info_reports_unknown(
    caplog: pytest.LogCaptureFixture, extra: int
) -> None:
    """Replies without byte 54 stay valid and report no Security V2 state."""
    harness = ProtocolHarness()
    await harness.register_notify()
    assert harness.device._login_key is not None
    data = device_info_data() + bytes(extra)
    with caplog.at_level(logging.DEBUG):
        harness.notify(
            frame_packet0(
                encrypt_payload(
                    harness.device._login_key,
                    4,
                    1,
                    0,
                    TuyaBLECode.FUN_SENDER_DEVICE_INFO,
                    data,
                )
            )
        )
    assert harness.device.device_version == "1.2"
    assert harness.device._session_key is not None
    assert harness.device.device_info_security is None
    assert "reported V2 supported unknown, enabled unknown" in caplog.text


def test_device_info_security_from_reply() -> None:
    """Only a reply reaching byte 54 carries the Security V2 byte."""
    assert DeviceInfoSecurity.from_device_info(bytes(54)) is None
    state = DeviceInfoSecurity.from_device_info(bytes(54) + b"\x06")
    assert state == DeviceInfoSecurity(0x06)
    assert state.security_v2
    assert state.security_v2_enabled


MISMATCH = "advertises Security V2"


@pytest.mark.parametrize(
    ("service", "sec_key", "message"),
    [
        (V2_ENABLED_SERVICE, None, "enabled but has no valid sec_key"),
        (V2_SUPPORTED_SERVICE, SEC_KEY, "supported but not enabled; using sec_key"),
        (V2_ENABLED_SERVICE, SEC_KEY, None),
        (V2_SUPPORTED_SERVICE, None, None),
        (CLASSIC_SERVICE, SEC_KEY, None),
    ],
)
def test_security_v2_mismatch_is_logged_once(
    caplog: pytest.LogCaptureFixture,
    service: bytes,
    sec_key: str | None,
    message: str | None,
) -> None:
    """A contradiction between advert and key is logged once, keys unchanged."""
    credentials = v2_credentials(sec_key)
    device = make_device()
    adv = v4_advertisement(uuid=credentials.uuid.encode(), service=service)
    with caplog.at_level(logging.DEBUG):
        # No credentials yet: nothing to compare against.
        device.set_ble_device_and_advertisement_data(device._ble_device, adv)
        assert MISMATCH not in caplog.text
        device.initialize_with_credentials(credentials)
        for _ in range(2):
            device.set_ble_device_and_advertisement_data(device._ble_device, adv)
    assert device.security_v2 is (sec_key is not None)
    if message is None:
        assert MISMATCH not in caplog.text
    else:
        assert caplog.text.count(f"{MISMATCH} {message}") == 1
    assert SEC_KEY not in caplog.text
    assert LOCAL_KEY not in caplog.text


def test_security_v2_mismatch_needs_an_fd50_record(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A classic advertisement announces nothing to contradict."""
    device = make_device()
    device.initialize_with_credentials(v2_credentials(None))
    with caplog.at_level(logging.DEBUG):
        device.set_ble_device_and_advertisement_data(
            device._ble_device,
            test_advertisement.classic_advertisement(b"\x00abcdefgh"),
        )
    assert MISMATCH not in caplog.text


@pytest.mark.parametrize(
    ("service", "supported", "enabled"),
    [
        (V2_SUPPORTED_SERVICE, True, False),
        (V2_ENABLED_SERVICE, True, True),
        (CLASSIC_SERVICE, False, False),
    ],
)
async def test_probe_outcome_logs_advertised_state(
    caplog: pytest.LogCaptureFixture, service: bytes, supported: bool, enabled: bool
) -> None:
    """The probe outcome line names the advertised Security V2 state."""
    credentials = v2_credentials()
    clients: list[FakeV4Firmware] = []
    firmware = SecurityV2Firmware if supported else FakeV4Firmware
    establish = _probe_establish(clients, lambda: firmware(credentials, SHADE_DATA))
    with caplog.at_level(logging.DEBUG), patch(PROBE_CONNECT, side_effect=establish):
        outcome = await async_probe_authenticated_status(
            make_device()._ble_device,
            v4_advertisement(uuid=credentials.uuid.encode(), service=service),
            credentials,
            credentials.uuid,
            frozenset({1, 3}),
        )
    assert outcome == ProbeOutcome.MATCHED
    assert f"advertised V2 supported {supported}, enabled {enabled}" in caplog.text


async def test_diagnostics_report_announced_security_v2_state(
    hass: HomeAssistant,
) -> None:
    """Export frame control and DEVICE_INFO flags, never identity or keys."""
    credentials = v2_credentials()
    data = _entry_data()
    data[CONF_SEC_KEY] = SEC_KEY
    entry = _add_entry(hass, data=data)
    firmware = ReportingFirmware(credentials, report(soil_sensor_datapoints()), 0x07)
    device = make_device()
    device.set_ble_device_and_advertisement_data(
        device._ble_device,
        v4_advertisement(uuid=credentials.uuid.encode(), service=V2_ENABLED_SERVICE),
    )
    device.initialize_with_credentials(credentials)
    await run_session(device, firmware)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = TuyaBLEData(
        title="Device",
        device=device,
        product=MagicMock(),
        manager=MagicMock(),
        coordinator=MagicMock(),
    )

    payload = await async_get_config_entry_diagnostics(hass, entry)

    assert payload["device"]["security_v2"] is True
    assert payload["device"]["security_v2_state"] == {
        "advertisement": {
            "frame_control": "490c",
            "bound": True,
            "shared": False,
            "supported": True,
            "enabled": True,
        },
        "device_info": {"flags": "07", "supported": True, "enabled": True},
    }
    state = repr(payload["device"])
    for secret in (
        SEC_KEY,
        LOCAL_KEY,
        credentials.uuid,
        credentials.device_id,
        data["uuid"],
        data["device_id"],
    ):
        assert secret not in state
    assert SEC_KEY not in repr(payload)
    assert LOCAL_KEY not in repr(payload)


def test_device_without_advertisement_reports_no_record() -> None:
    """A device built without an advertisement announces nothing."""
    template = make_device()
    device = TuyaBLEDevice(FakeBLEManager(v2_credentials()), template._ble_device)
    assert device.advertised_identity_record is None
    device.initialize_with_credentials(v2_credentials(None))
    assert device.advertised_identity_record is None
