"""The options flow replaces a device's key pair in one validated update."""

# pylint: disable=protected-access

from __future__ import annotations

import logging
from typing import Any, cast
from unittest.mock import MagicMock, patch

from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.tuya_ble.config_flow_options import TuyaBLEOptionsFlow
from custom_components.tuya_ble.const import (
    CONF_CATEGORY,
    CONF_CONNECTION_PROFILE,
    CONF_DEVICE_ID,
    CONF_LOCAL_KEY,
    CONF_PRODUCT_ID,
    CONF_SEC_KEY,
    CONF_USER_CODE,
    CONF_UUID,
    DOMAIN,
)
from custom_components.tuya_ble.tuya_ble import (
    MANUFACTURER_DATA_ID,
    SERVICE_UUID,
    SERVICE_UUID_V4,
    decode_classic_record,
    decode_fd50_identity_record,
    decode_tuya_ble_advertisement,
)
from tests import test_advertisement
from tests.conftest import FakeAdvertisementData
from tests.test_config_flow import FakeDiscovery

LAST_SERVICE_INFO = (
    "custom_components.tuya_ble.config_flow_options.bluetooth.async_last_service_info"
)
ADDRESS = "AA:BB:CC:DD:EE:FF"
OLD_LOCAL_KEY = "oldlocalkey12345"
OLD_SEC_KEY = "oldsecuritykey12"
NEW_LOCAL_KEY = "0123456789abcdef"
NEW_SEC_KEY = "fedcba9876543210"
V2_SERVICE = b"\x41\x04\x00\x08abcdefgh"
NO_V2_SERVICE = b"\x41\x00\x00\x08abcdefgh"
IDENTITY = {
    CONF_ADDRESS: ADDRESS,
    CONF_UUID: "1234567890abcdef",
    CONF_DEVICE_ID: "device123",
    CONF_CATEGORY: "cl",
    CONF_PRODUCT_ID: "jetlwvfm",
    CONF_CONNECTION_PROFILE: "v2_app",
}


def options_flow(
    hass: HomeAssistant, *, sec_key: str | None = OLD_SEC_KEY
) -> tuple[TuyaBLEOptionsFlow, MockConfigEntry]:
    """Build an options flow for an entry holding an old key pair."""
    data: dict[str, Any] = {**IDENTITY, CONF_LOCAL_KEY: OLD_LOCAL_KEY}
    if sec_key is not None:
        data[CONF_SEC_KEY] = sec_key
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Roller",
        unique_id=ADDRESS,
        data=data,
        options={CONF_USER_CODE: "user1"},
    )
    entry.add_to_hass(hass)
    flow = TuyaBLEOptionsFlow(entry)
    flow.hass = hass
    flow.handler = entry.entry_id
    return flow, entry


def service_info(service: bytes | FakeAdvertisementData | None) -> Any:
    """Return the latest stored advertisement, or none when never seen.

    Bytes are an FD50 identity record with its complete encrypted identity.
    """
    if service is None:
        return None
    info = FakeDiscovery()
    info.advertisement = cast(
        Any,
        test_advertisement.v4_advertisement(service)
        if isinstance(service, bytes)
        else service,
    )
    return info


def record_only(service: bytes) -> FakeAdvertisementData:
    """An FD50 identity record whose encrypted identity is still missing."""
    return FakeAdvertisementData(service_data={SERVICE_UUID_V4: service})


# A bound classic record whose identity does not decrypt (#155).
BOUND_CLASSIC = FakeAdvertisementData(
    service_data={SERVICE_UUID: b"\x00prod"},
    manufacturer_data={MANUFACTURER_DATA_ID: b"\x80\x03" + bytes(4) + bytes(16)},
)
UNRELATED = FakeAdvertisementData(
    service_data={"0000feaa-0000-1000-8000-00805f9b34fb": b"x"}
)


async def replace_keys(
    hass: HomeAssistant,
    flow: TuyaBLEOptionsFlow,
    user_input: dict[str, Any],
    service: bytes | FakeAdvertisementData | None = V2_SERVICE,
) -> tuple[Any, MagicMock]:
    """Submit the key form after applying its schema, as the frontend would."""
    form = await flow.async_step_credentials()
    validated = cast(Any, form["data_schema"])(user_input)
    with (
        patch(LAST_SERVICE_INFO, return_value=service_info(service)),
        patch.object(hass.config_entries, "async_schedule_reload") as reload,
    ):
        result = await flow.async_step_credentials(validated)
    return result, reload


async def test_init_offers_qr_login_and_key_replacement(
    hass: HomeAssistant,
) -> None:
    """The options menu keeps the QR re-authentication as its first choice."""
    flow, _ = options_flow(hass)
    result = await flow.async_step_init()
    assert result["type"] == FlowResultType.MENU
    assert result["menu_options"] == [
        "user",
        "credentials",
        "connection",
        "refresh_unlock_check",
    ]


async def test_key_form_never_shows_stored_keys(hass: HomeAssistant) -> None:
    """Stored keys are neither defaults nor suggestions in the form."""
    flow, _ = options_flow(hass)
    result = await flow.async_step_credentials()
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "credentials"
    for key in cast(Any, result["data_schema"]).schema:
        assert not key.description
        assert not callable(getattr(key, "default", None))
    assert OLD_LOCAL_KEY not in repr(result)
    assert OLD_SEC_KEY not in repr(result)


async def test_pair_replaced_together_and_identity_kept(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Both keys change in one update; identity, options and profile remain."""
    flow, entry = options_flow(hass)
    with caplog.at_level(logging.DEBUG):
        result, reload = await replace_keys(
            hass,
            flow,
            {CONF_LOCAL_KEY: f" {NEW_LOCAL_KEY} ", CONF_SEC_KEY: NEW_SEC_KEY},
        )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_USER_CODE: "user1"}
    assert entry.data == {
        **IDENTITY,
        CONF_LOCAL_KEY: NEW_LOCAL_KEY,
        CONF_SEC_KEY: NEW_SEC_KEY,
    }
    assert entry.unique_id == ADDRESS
    reload.assert_called_once_with(entry.entry_id)
    assert "device keys replaced (security key stored)" in caplog.text
    for secret in (OLD_LOCAL_KEY, OLD_SEC_KEY, NEW_LOCAL_KEY, NEW_SEC_KEY):
        assert secret not in caplog.text


@pytest.mark.parametrize("cleared", [None, "", "   "])
async def test_empty_security_key_clears_the_stored_one(
    hass: HomeAssistant, cleared: str | None
) -> None:
    """An omitted or blank security key removes the old one, never keeps it."""
    flow, entry = options_flow(hass)
    user_input: dict[str, Any] = {CONF_LOCAL_KEY: NEW_LOCAL_KEY}
    if cleared is not None:
        user_input[CONF_SEC_KEY] = cleared
    result, reload = await replace_keys(hass, flow, user_input)
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.data == {**IDENTITY, CONF_LOCAL_KEY: NEW_LOCAL_KEY}
    reload.assert_called_once()


@pytest.mark.parametrize(
    ("user_input", "errors"),
    [
        ({CONF_LOCAL_KEY: "short"}, {CONF_LOCAL_KEY: "invalid_local_key"}),
        (
            {CONF_LOCAL_KEY: NEW_LOCAL_KEY, CONF_SEC_KEY: "short"},
            {CONF_SEC_KEY: "invalid_sec_key"},
        ),
    ],
)
async def test_invalid_pair_changes_nothing(
    hass: HomeAssistant, user_input: dict[str, Any], errors: dict[str, str]
) -> None:
    """A rejected pair leaves the working keys and the running device alone."""
    flow, entry = options_flow(hass)
    before = dict(entry.data)
    result, reload = await replace_keys(hass, flow, user_input)
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == errors
    assert entry.data == before
    reload.assert_not_called()


@pytest.mark.parametrize(
    ("service", "accepted"),
    [
        (V2_SERVICE, True),
        (NO_V2_SERVICE, False),
        (record_only(V2_SERVICE), True),
        (record_only(NO_V2_SERVICE), False),
        (BOUND_CLASSIC, False),
        (UNRELATED, True),
        (None, True),
    ],
    ids=[
        "identity-v2",
        "identity-classic-login",
        "record-v2",
        "record-classic-login",
        "bound-classic",
        "no-tuya-data",
        "never-seen",
    ],
)
async def test_security_key_refused_when_the_device_does_not_use_one(
    hass: HomeAssistant,
    service: bytes | FakeAdvertisementData | None,
    accepted: bool,
) -> None:
    """Setup's rule holds: a device without Security V2 gets no security key."""
    if isinstance(service, FakeAdvertisementData) and service is not UNRELATED:
        # The case reaches the intended decoder, not an earlier one.
        adv = cast(Any, service)
        assert decode_tuya_ble_advertisement(adv) is None
        assert (decode_fd50_identity_record(adv) is None) is (service is BOUND_CLASSIC)
        assert (decode_classic_record(adv) is None) is (service is not BOUND_CLASSIC)
    flow, entry = options_flow(hass, sec_key=None)
    result, reload = await replace_keys(
        hass,
        flow,
        {CONF_LOCAL_KEY: NEW_LOCAL_KEY, CONF_SEC_KEY: NEW_SEC_KEY},
        service,
    )
    if accepted:
        assert entry.data[CONF_SEC_KEY] == NEW_SEC_KEY
        reload.assert_called_once()
    else:
        assert result["errors"] == {CONF_SEC_KEY: "sec_key_not_used"}
        assert CONF_SEC_KEY not in entry.data
        assert entry.data[CONF_LOCAL_KEY] == OLD_LOCAL_KEY
        reload.assert_not_called()


async def test_classic_device_key_replacement_without_security_key(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A classic device only ever needs its local key replaced."""
    flow, entry = options_flow(hass, sec_key=None)
    with caplog.at_level(logging.DEBUG):
        await replace_keys(hass, flow, {CONF_LOCAL_KEY: NEW_LOCAL_KEY}, NO_V2_SERVICE)
    assert entry.data[CONF_LOCAL_KEY] == NEW_LOCAL_KEY
    assert "security key not stored" in caplog.text
