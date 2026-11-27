"""Conditional app-login setup and transactional recovery of existing entries."""

# pylint: disable=protected-access
# cspell:ignore ecode
import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

from bleak_retry_connector import BleakConnectionError
from homeassistant.config_entries import SOURCE_RECONFIGURE
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import AbortFlow, FlowResultType
from homeassistant.helpers import selector
import probatio
import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.tuya_ble.config_flow_mobile import (
    CONF_APP,
    CONF_COUNTRY,
    mobile_schema,
    needs_mobile_login,
    parse_mobile_login,
)
from custom_components.tuya_ble.const import (
    CONF_CLOUD_REGION,
    CONF_CLOUD_UUID,
    CONF_CONNECTION_PROFILE,
    CONF_CREDENTIAL_SOURCE,
    CONF_ENDPOINT,
    CONF_LOCAL_KEY,
    CONF_MOBILE_SNAPSHOT,
    CONF_SEC_KEY,
    CONF_SECURITY_MODE,
    CONF_USER_CODE,
    CONF_UUID,
    DOMAIN,
)
from custom_components.tuya_ble.mobile_cloud import MobileResult
from custom_components.tuya_ble.mobile_snapshot import capture_mobile_snapshot
from custom_components.tuya_ble.tuya_ble import (
    ConnectionProfile,
    ProbeStage,
    SecurityMode,
)
from tests.conftest import make_credentials, verified_by
from tests.test_advertisement import v4_advertisement
from tests.test_config_flow import FakeManager, build_flow
from tests.test_config_flow_v4 import discovery

DETECT = "custom_components.tuya_ble.config_flow.async_detect_v4_credentials"
FETCH = "custom_components.tuya_ble.config_flow_mobile.async_fetch_mobile_device"
FORM = {
    CONF_APP: "tuya_smart",
    CONF_USERNAME: "private@example.org",
    CONF_PASSWORD: "PRIVATE_PASSWORD",
}
LOCAL = "0123456789abcdef"
SEC = "fedcba9876543210"


def info() -> Any:
    """Build a Security V2 scan record whose UUID matches the cloud identity."""
    result: Any = discovery()
    adv = v4_advertisement(b"\x41\x04\x00\x08abcdefgh")
    result.advertisement = adv
    result.service_data = adv.service_data
    result.manufacturer_data = adv.manufacturer_data
    return result


def snapshot() -> dict[str, Any]:
    """A sanitized mobile record, never a password or session payload."""
    return capture_mobile_snapshot(
        "tuya_smart",
        {
            "device": {
                "productVer": "1.2",
                "localKey": LOCAL,
                "secKey": SEC,
            }
        },
        {"device": True},
    )


async def eligible_flow(hass: HomeAssistant) -> tuple[Any, Any]:
    """Drive regular setup through verification to the missing-key fallback."""
    flow = build_flow(hass)
    flow._data = {CONF_ENDPOINT: "https://apigw.tuyaeu.com"}
    scan = info()
    flow.discovery_info = scan
    flow._manager = cast(Any, FakeManager(make_credentials(local_key=LOCAL)))

    async def fail(*_args: Any, stages: Any = None, **_kwargs: Any) -> None:
        stages.append(ProbeStage.MISSING_SEC_KEY)
        return None

    with (
        patch.object(flow, "_async_scan_device", return_value=scan),
        patch(DETECT, side_effect=fail),
    ):
        result = await flow.async_step_discovered_device({})
        assert result["type"] == FlowResultType.SHOW_PROGRESS
        assert flow._probe_task is not None
        await flow._probe_task
        result = await flow.async_step_probe_finish()
    assert result["type"] == FlowResultType.MENU
    assert result["step_id"] == "mobile_offer"
    return flow, scan


async def complete_mobile_qr(
    flow: Any, credentials: Any, endpoint: str = "https://apigw.tuyaeu.com"
) -> dict[str, Any]:
    """Complete QR authorization through the public login and scan transitions."""
    flow._mobile_region_login = True  # Setup state for the recovery QR return path.
    login_info = {
        "t": 0,
        "uid": "test-uid",
        "expire_time": 3600,
        "access_token": "test-access",
        "refresh_token": "test-refresh",
        "terminal_id": "test-terminal",
        "endpoint": endpoint,
    }
    with (
        patch.object(
            flow._qr_login_control, "login_result", return_value=(True, login_info)
        ),
        patch(
            "custom_components.tuya_ble.config_flow.HASSTuyaBLEDeviceManager",
            return_value=FakeManager(credentials),
        ),
    ):
        assert (await flow.async_step_login({CONF_USER_CODE: "test-code"}))[
            "step_id"
        ] == "qr"
        return cast(dict[str, Any], await flow.async_step_qr({}))


def test_form_order_and_secure_password() -> None:
    """Exact requested order, optional numeric code and no password defaults."""
    schema = mobile_schema().schema
    keys = list(schema)
    assert [str(key) for key in keys] == [
        CONF_APP,
        CONF_USERNAME,
        CONF_PASSWORD,
        CONF_COUNTRY,
    ]
    assert isinstance(schema[keys[3]], selector.NumberSelector)
    assert schema[keys[2]].config["type"] == "password"
    assert keys[2].default is probatio.UNDEFINED


@pytest.mark.parametrize(
    ("region", "code"),
    [
        ("eu", "44"),
        ("us", "1"),
        ("cn", "86"),
        ("in", "91"),
        ("sg", "65"),
    ],
)
def test_email_region_default(region: str, code: str) -> None:
    """Country is derived from the QR region only when omitted for email login."""
    login, errors = parse_mobile_login(FORM, region)
    assert not errors
    assert login is not None
    assert login.country_code == code
    assert login.username == FORM[CONF_USERNAME]
    assert "PRIVATE_PASSWORD" not in repr(login)


@pytest.mark.parametrize(
    "username", ["private@example.org", "+447123456789", "07123 456789"]
)
def test_supplied_numeric_country_overrides_default(username: str) -> None:
    """Both account types use a supplied valid number, including integer floats."""
    login, errors = parse_mobile_login(
        {**FORM, CONF_USERNAME: username, CONF_COUNTRY: 49.0}, "eu"
    )
    assert not errors
    assert login is not None
    assert login.country_code == "49"


@pytest.mark.parametrize(
    "code", [True, "44", 0, -1, 1000, 44.5, float("nan"), float("inf"), 2**2000]
)
def test_invalid_country_prevents_password_submission(code: Any) -> None:
    """Do not silently replace an invalid supplied code with a region default."""
    login, errors = parse_mobile_login({**FORM, CONF_COUNTRY: code}, "eu")
    assert login is None
    assert errors[CONF_COUNTRY] == "mobile_invalid_country"


def test_phone_requires_country() -> None:
    """Representative codes must never be applied to a phone account."""
    login, errors = parse_mobile_login({**FORM, CONF_USERNAME: "07123456789"}, "eu")
    assert login is None
    assert errors == {CONF_COUNTRY: "mobile_country_required"}


@pytest.mark.parametrize(
    ("fields", "field"),
    [
        ({CONF_APP: "other"}, CONF_APP),
        ({CONF_USERNAME: None}, CONF_USERNAME),
        ({CONF_USERNAME: "abc@"}, CONF_USERNAME),
        ({CONF_PASSWORD: ""}, CONF_PASSWORD),
        ({CONF_PASSWORD: None}, CONF_PASSWORD),
    ],
)
def test_invalid_form_fields(fields: dict[str, Any], field: str) -> None:
    """Invalid submissions show field-specific errors."""
    login, errors = parse_mobile_login({**FORM, **fields}, "eu")
    assert login is None
    assert field in errors


def test_unknown_region_is_not_guessed() -> None:
    """Neither a country override nor a valid password chooses an unknown host."""
    login, errors = parse_mobile_login({**FORM, CONF_COUNTRY: 44}, None)
    assert login is None
    assert errors["base"] == "mobile_unknown_region"


@pytest.mark.parametrize(
    ("version", "security", "sec", "error", "expected"),
    [
        (4, True, None, "v4_failed_missing_key", True),
        (4, True, "bad", "v4_failed_credentials", True),
        (4, True, SEC, "v4_failed_missing_key", False),
        (4, True, None, "v4_failed_connect", False),
        (3, True, None, "v4_failed_missing_key", False),
        (4, False, None, "v4_failed_missing_key", False),
    ],
)
def test_fallback_only_when_needed(
    version: int, security: bool, sec: str | None, error: str, expected: bool
) -> None:
    """Generic connectivity failures and working keys do not solicit passwords."""
    advert = SimpleNamespace(protocol_version=version, security_v2=security)
    creds = replace(make_credentials(), sec_key=sec)
    assert needs_mobile_login(cast(Any, advert), creds, error) is expected


async def test_successful_fallback_persists_keys_not_login(hass: HomeAssistant) -> None:
    """The mobile key pair verifies using the normal probe, then loads offline."""
    flow, _ = await eligible_flow(hass)
    form = await flow.async_step_mobile_login()
    assert form["step_id"] == "mobile_login"
    candidate = replace(make_credentials(), local_key=LOCAL, sec_key=SEC)
    with (
        patch(FETCH, return_value=MobileResult(candidate, snapshot())) as fetch,
        patch(
            DETECT, side_effect=verified_by(ConnectionProfile.V4_20, SecurityMode.V2)
        ),
    ):
        result = await flow.async_step_mobile_login(FORM)
        assert result["type"] in (
            FlowResultType.SHOW_PROGRESS,
            FlowResultType.SHOW_PROGRESS_DONE,
        )
        await flow._mobile_task
        result = await flow.async_step_mobile_fetch()
        assert result["step_id"] == "mobile_finish"
        result = await flow.async_step_mobile_finish()
        assert result["type"] in (
            FlowResultType.SHOW_PROGRESS,
            FlowResultType.SHOW_PROGRESS_DONE,
        )
        await flow._probe_task
        result = await flow.async_step_probe_finish()
    data = result["data"]
    assert data[CONF_LOCAL_KEY] == LOCAL
    assert data[CONF_SEC_KEY] == SEC
    assert data[CONF_CREDENTIAL_SOURCE] == "mobile_app"
    assert data[CONF_CLOUD_REGION] == "eu"
    assert data[CONF_MOBILE_SNAPSHOT]["records"]["device"]["productVer"] == "1.2"
    assert fetch.call_args.args[1].country_code == "44"
    serialized = json.dumps(data)
    assert "PRIVATE_PASSWORD" not in serialized
    assert "private@example.org" not in serialized
    assert "sid" not in data
    assert "ecode" not in data


async def test_cloud_success_bluetooth_failure_still_allows_diagnostics(
    hass: HomeAssistant,
) -> None:
    """Valid mobile credentials remain available on an explicitly unverified entry."""
    flow, _ = await eligible_flow(hass)
    candidate = replace(make_credentials(), local_key=LOCAL, sec_key=SEC)
    with (
        patch(FETCH, return_value=MobileResult(candidate, snapshot())),
        patch(DETECT, side_effect=verified_by(None)),
    ):
        await flow.async_step_mobile_login(FORM)
        await flow._mobile_task
        await flow.async_step_mobile_finish()
        await flow._probe_task
        result = await flow.async_step_probe_finish()
    assert result["step_id"] == "unverified"
    result = await flow.async_step_unverified({})
    assert result["data"][CONF_SEC_KEY] == SEC
    assert CONF_CONNECTION_PROFILE not in result["data"]
    assert CONF_MOBILE_SNAPSHOT in result["data"]


@pytest.mark.parametrize(
    "error", ["mobile_invalid_auth", "mobile_missing_key", "mobile_invalid_keys"]
)
async def test_mobile_errors_allow_explicit_retry_or_add(
    hass: HomeAssistant, error: str
) -> None:
    """One request per submission, without frontend password defaults."""
    flow, _ = await eligible_flow(hass)
    with patch(
        FETCH, return_value=MobileResult(snapshot=snapshot(), error=error)
    ) as fetch:
        await flow.async_step_mobile_login(FORM)
        await flow._mobile_task
        result = await flow.async_step_mobile_finish()
        assert result["step_id"] == "mobile_result"
        assert result["errors"] == {"base": error}
        fetch.assert_awaited_once()
        result = await flow.async_step_mobile_result({"next_action": "retry"})
        assert result["step_id"] == "mobile_login"
        result = await flow.async_step_mobile_result({"next_action": "continue"})
        assert result["step_id"] == "unverified"
        result = await flow.async_step_unverified({})
    assert CONF_MOBILE_SNAPSHOT in result["data"]
    assert CONF_CREDENTIAL_SOURCE not in result["data"]
    assert CONF_SEC_KEY not in result["data"]


async def test_invalid_phone_form_never_calls_cloud(hass: HomeAssistant) -> None:
    """Country validation runs before any password submission."""
    flow, _ = await eligible_flow(hass)
    with patch(FETCH) as fetch:
        result = await flow.async_step_mobile_login({
            **FORM,
            CONF_USERNAME: "07123456789",
        })
    assert result["errors"] == {CONF_COUNTRY: "mobile_country_required"}
    fetch.assert_not_called()


async def test_transport_failure_never_offers_mobile_password_login(
    hass: HomeAssistant,
) -> None:
    """Exercise real profile probing: an unreachable device is not a missing key."""
    flow = build_flow(hass)
    scan = info()
    flow.discovery_info = scan
    flow._data = {CONF_ENDPOINT: "https://apigw.tuyaeu.com"}
    flow._manager = cast(Any, FakeManager(make_credentials(local_key=LOCAL)))
    with (
        patch.object(flow, "_async_scan_device", return_value=scan),
        patch(
            "custom_components.tuya_ble.tuya_ble.probe.establish_connection",
            side_effect=BleakConnectionError("out of range"),
        ),
        patch(FETCH) as fetch,
    ):
        await flow.async_step_discovered_device({})
        assert flow._probe_task is not None
        await flow._probe_task
        result = await flow.async_step_probe_finish()
    assert result["step_id"] == "unverified"
    assert result["errors"] == {"base": "v4_failed_connect"}
    assert flow._mobile_pending is None
    fetch.assert_not_called()


async def test_cancel_mobile_login_releases_task(hass: HomeAssistant) -> None:
    """A removed flow cancels the account request and never creates an entry."""
    flow, _ = await eligible_flow(hass)
    started = asyncio.Event()

    async def wait(*_args: Any) -> None:
        started.set()
        await asyncio.Event().wait()

    with patch(FETCH, side_effect=wait):
        await flow.async_step_mobile_login(FORM)
        task = flow._mobile_task
        await started.wait()
        # A duplicate submission returns the same task, not a second login.
        await flow.async_step_mobile_login(FORM)
        assert flow._mobile_task is task
        assert (await flow.async_step_mobile_offer())[
            "type"
        ] == FlowResultType.SHOW_PROGRESS
        flow.async_remove()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert flow._mobile_pending is None
    with pytest.raises(AbortFlow):
        await flow.async_step_mobile_finish()


async def test_steps_without_pending_state_abort(hass: HomeAssistant) -> None:
    """Mobile steps cannot be reached without a known failed target device."""
    flow = build_flow(hass)
    for step in (
        flow.async_step_mobile_offer,
        flow.async_step_mobile_login,
        flow.async_step_mobile_fetch,
        flow.async_step_mobile_finish,
        flow.async_step_mobile_result,
    ):
        assert (await step())["type"] == FlowResultType.ABORT
    assert (await complete_mobile_qr(flow, None))["type"] == FlowResultType.ABORT


@pytest.mark.parametrize(
    "username",
    [
        "a@@example.org",
        "a@.example.org",
        "a@example.",
        "a b@example.org",
        "x" * 255 + "@example.org",
    ],
)
def test_email_validation_is_bounded(username: str) -> None:
    """Validate malformed emails without a super-linear backtracking pattern."""
    login, errors = parse_mobile_login({**FORM, CONF_USERNAME: username}, "eu")
    assert login is None
    assert errors[CONF_USERNAME] == "mobile_invalid_username"


@pytest.mark.parametrize(
    "task_error", [RuntimeError("PRIVATE_PASSWORD"), asyncio.CancelledError()]
)
async def test_task_failure_is_safely_reported(
    hass: HomeAssistant, task_error: BaseException
) -> None:
    """Cancelled or raised requests never leak exception text."""
    flow, _ = await eligible_flow(hass)
    with patch(FETCH, side_effect=task_error):
        await flow.async_step_mobile_login(FORM)
        task = flow._mobile_task
        with pytest.raises(type(task_error)):
            await task
        result = await flow.async_step_mobile_finish()
    assert result["errors"] == {"base": "mobile_connection_failed"}
    assert "PRIVATE_PASSWORD" not in repr(result)


def reconfigure(flow: Any, hass: HomeAssistant) -> MockConfigEntry:
    """Configure a pre-existing entry; no secrets are part of mobile snapshots."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="AA:BB:CC:DD:EE:FF",
        data={
            "address": "AA:BB:CC:DD:EE:FF",
            CONF_UUID: make_credentials().uuid,
            CONF_CLOUD_UUID: make_credentials().uuid,
            "device_id": make_credentials().device_id,
            "category": "wk",
            "product_id": make_credentials().product_id,
            CONF_LOCAL_KEY: "original-key",
            CONF_CLOUD_REGION: "eu",
        },
    )
    entry.add_to_hass(hass)
    flow.context = {"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id}
    return entry


async def test_reconfigure_failure_preserves_keys_and_saves_metadata(
    hass: HomeAssistant,
) -> None:
    """Only sanitized metadata changes when an existing entry cannot recover."""
    flow, _ = await eligible_flow(hass)
    entry = reconfigure(flow, hass)
    flow._data = {}
    with patch(
        FETCH,
        return_value=MobileResult(snapshot=snapshot(), error="mobile_missing_key"),
    ):
        await flow.async_step_mobile_login(FORM)
        await flow._mobile_task
        result = await flow.async_step_mobile_finish()
    assert result["errors"] == {"base": "mobile_missing_key"}
    assert entry.data[CONF_LOCAL_KEY] == "original-key"
    assert CONF_SEC_KEY not in entry.data
    assert entry.data[CONF_MOBILE_SNAPSHOT]["records"]["device"]["productVer"] == "1.2"
    result = await flow.async_step_mobile_result({"next_action": "continue"})
    assert result["step_id"] == "reconfigure"


async def test_reconfigure_success_updates_pair_and_profile_atomically(
    hass: HomeAssistant,
) -> None:
    """Replace credentials only alongside a verified profile."""
    flow, _ = await eligible_flow(hass)
    entry = reconfigure(flow, hass)
    flow._probe_source_step = "reconfigure"
    candidate = replace(make_credentials(), local_key=LOCAL, sec_key=SEC)
    with (
        patch(FETCH, return_value=MobileResult(candidate, snapshot())),
        patch(
            DETECT, side_effect=verified_by(ConnectionProfile.V4_20, SecurityMode.V2)
        ),
        patch.object(hass.config_entries, "async_unload", return_value=True),
        patch.object(
            flow,
            "async_update_reload_and_abort",
            return_value={"type": FlowResultType.ABORT},
        ) as update,
    ):
        await flow.async_step_mobile_login(FORM)
        await flow._mobile_task
        await flow.async_step_mobile_finish()
        await flow._probe_task
        await flow.async_step_probe_finish()
    updates = update.call_args.kwargs["data_updates"]
    assert updates[CONF_LOCAL_KEY] == LOCAL
    assert updates[CONF_SEC_KEY] == SEC
    assert updates[CONF_CONNECTION_PROFILE] == "v4_20"
    assert updates[CONF_SECURITY_MODE] == "v2"
    assert updates[CONF_CREDENTIAL_SOURCE] == "mobile_app"
    assert entry.data[CONF_LOCAL_KEY] == "original-key"  # update itself is stubbed


async def test_reconfigure_without_region_requests_qr_login(
    hass: HomeAssistant,
) -> None:
    """Old entries obtain their region from a fresh QR login."""
    flow, _ = await eligible_flow(hass)
    entry = reconfigure(flow, hass)
    hass.config_entries.async_update_entry(
        entry,
        data={
            key: value for key, value in entry.data.items() if key != CONF_CLOUD_REGION
        },
    )
    flow._data = {}
    result = await flow.async_step_mobile_login()
    assert result["step_id"] == "login"
    assert flow._mobile_region_login
    result = await complete_mobile_qr(flow, make_credentials())
    assert result["step_id"] == "mobile_login"


async def test_qr_reauthorization_must_match_device(hass: HomeAssistant) -> None:
    """An unrelated QR account cannot set this existing entry's login region."""
    flow, _ = await eligible_flow(hass)
    reconfigure(flow, hass)
    result = await complete_mobile_qr(flow, None)
    assert result["errors"] == {"base": "mobile_identity_mismatch"}
    assert not flow._data
    assert flow._manager is None
    # A previously known region must not bypass the rejected-account guard.
    result = await flow.async_step_mobile_result({"next_action": "retry"})
    assert result["step_id"] == "login"
    result = await complete_mobile_qr(flow, make_credentials())
    assert result["step_id"] == "mobile_login"


async def test_reauthorization_unknown_gateway_does_not_loop(
    hass: HomeAssistant,
) -> None:
    """Unsupported endpoints produce a result error instead of guessing a region."""
    flow, _ = await eligible_flow(hass)
    entry = reconfigure(flow, hass)
    hass.config_entries.async_update_entry(
        entry,
        data={
            key: value for key, value in entry.data.items() if key != CONF_CLOUD_REGION
        },
    )
    result = await complete_mobile_qr(
        flow, make_credentials(), "https://unknown.example.org"
    )
    assert result["errors"] == {"base": "mobile_unknown_region"}


async def test_failed_unload_does_not_replace_working_keys(hass: HomeAssistant) -> None:
    """Recovery cannot probe while the existing runtime still owns the device."""
    flow, _ = await eligible_flow(hass)
    entry = reconfigure(flow, hass)
    candidate = replace(make_credentials(), local_key=LOCAL, sec_key=SEC)
    with (
        patch(FETCH, return_value=MobileResult(candidate, snapshot())),
        patch.object(hass.config_entries, "async_unload", return_value=False),
    ):
        await flow.async_step_mobile_login(FORM)
        await flow._mobile_task
        result = await flow.async_step_mobile_finish()
    assert result["errors"] == {"base": "v4_verification_failed"}
    assert entry.data[CONF_LOCAL_KEY] == "original-key"
    assert CONF_MOBILE_SNAPSHOT in entry.data


async def test_removal_during_unload_restores_existing_entry(
    hass: HomeAssistant,
) -> None:
    """A cancelled recovery cannot leave runtime permanently unloaded."""
    flow, _ = await eligible_flow(hass)
    reconfigure(flow, hass)
    candidate = replace(make_credentials(), local_key=LOCAL, sec_key=SEC)

    async def unload(_entry_id: str) -> bool:
        flow._flow_removed = True
        return True

    with (
        patch(FETCH, return_value=MobileResult(candidate, snapshot())),
        patch.object(hass.config_entries, "async_unload", side_effect=unload),
        patch.object(
            hass.config_entries, "async_reload", return_value=True
        ) as reload_entry,
    ):
        await flow.async_step_mobile_login(FORM)
        await flow._mobile_task
        with pytest.raises(AbortFlow):
            await flow.async_step_mobile_finish()
    reload_entry.assert_awaited_once()


@pytest.mark.parametrize("stored", ["unknown", {}, None])
async def test_invalid_saved_region_uses_trusted_options_endpoint(
    hass: HomeAssistant, stored: Any
) -> None:
    """Malformed region markers do not crash the recovery form."""
    flow, _ = await eligible_flow(hass)
    entry = reconfigure(flow, hass)
    flow._data = {}
    hass.config_entries.async_update_entry(
        entry,
        data={**entry.data, CONF_CLOUD_REGION: stored},
        options={CONF_ENDPOINT: "https://apigw.tuyaeu.com"},
    )
    assert (await flow.async_step_mobile_login())["step_id"] == "mobile_login"
    assert not flow._reconfigure_unloaded


async def test_reconfigure_ble_failure_keeps_existing_pair(hass: HomeAssistant) -> None:
    """A newly fetched pair is never committed if the BLE verification fails."""
    flow, _ = await eligible_flow(hass)
    entry = reconfigure(flow, hass)
    flow._probe_source_step = "reconfigure"
    candidate = replace(make_credentials(), local_key=LOCAL, sec_key=SEC)
    with (
        patch(FETCH, return_value=MobileResult(candidate, snapshot())),
        patch(DETECT, side_effect=verified_by(None)),
        patch.object(hass.config_entries, "async_unload", return_value=True),
        patch.object(
            hass.config_entries, "async_reload", return_value=True
        ) as reload_entry,
    ):
        await flow.async_step_mobile_login(FORM)
        await flow._mobile_task
        await flow.async_step_mobile_finish()
        await flow._probe_task
        result = await flow.async_step_probe_finish()
    assert result["step_id"] == "reconfigure"
    assert entry.data[CONF_LOCAL_KEY] == "original-key"
    assert CONF_SEC_KEY not in entry.data
    assert CONF_MOBILE_SNAPSHOT in entry.data
    reload_entry.assert_awaited_once()


@pytest.mark.parametrize("failure", ["ble", "unload"])
async def test_explicit_recovery_retry_uses_staged_pair_without_login(
    hass: HomeAssistant, failure: str
) -> None:
    """Transient failures retain the pair without saving it or repeating app login."""
    flow, scan = await eligible_flow(hass)
    entry = reconfigure(flow, hass)
    flow._probe_source_step = "reconfigure"
    candidate = replace(make_credentials(), local_key=LOCAL, sec_key=SEC)
    detectors = [
        verified_by(profile, SecurityMode.V2)
        for profile in (
            [None, ConnectionProfile.V4_20]
            if failure == "ble"
            else [ConnectionProfile.V4_20]
        )
    ]

    async def profiles(*args: Any, **kwargs: Any) -> Any:
        return await detectors.pop(0)(*args, **kwargs)

    unloads = [True, True] if failure == "ble" else [False, True]
    with (
        patch(FETCH, return_value=MobileResult(candidate, snapshot())) as fetch,
        patch(DETECT, side_effect=profiles) as detect,
        patch.object(flow, "_async_scan_device", return_value=scan),
        patch.object(hass.config_entries, "async_unload", side_effect=unloads),
        patch.object(hass.config_entries, "async_reload", return_value=True),
        patch.object(
            flow,
            "async_update_reload_and_abort",
            return_value={"type": FlowResultType.ABORT},
        ) as update,
    ):
        await flow.async_step_mobile_login(FORM)
        await flow._mobile_task
        result = await flow.async_step_mobile_finish()
        if failure == "ble":
            await flow._probe_task
            result = await flow.async_step_probe_finish()
        assert entry.data[CONF_LOCAL_KEY] == "original-key"
        assert CONF_SEC_KEY not in entry.data
        if failure == "ble":
            assert result["step_id"] == "reconfigure"
            await flow.async_step_reconfigure({})
        else:
            assert result["step_id"] == "mobile_result"
            await flow.async_step_mobile_result({"next_action": "retry"})
        await flow._probe_task
        await flow.async_step_probe_finish()
    fetch.assert_awaited_once()
    assert detect.call_args.args[2].local_key == LOCAL
    assert detect.call_args.args[2].sec_key == SEC
    updates = update.call_args.kwargs["data_updates"]
    assert updates[CONF_LOCAL_KEY] == LOCAL
    assert updates[CONF_SEC_KEY] == SEC
    assert updates[CONF_CONNECTION_PROFILE] == "v4_20"


REPLACED = {CONF_LOCAL_KEY: "replacedlocalkey", CONF_SEC_KEY: "replacedsecurity"}


@pytest.mark.parametrize("when", ["during_probe", "before_retry"])
async def test_replaced_keys_discard_the_staged_mobile_pair(
    hass: HomeAssistant, when: str
) -> None:
    """A mobile pair staged for old keys never overwrites keys replaced later."""
    flow, scan = await eligible_flow(hass)
    entry = reconfigure(flow, hass)
    flow._probe_source_step = "reconfigure"
    candidate = replace(make_credentials(), local_key=LOCAL, sec_key=SEC)
    first = verified_by(
        ConnectionProfile.V4_20 if when == "during_probe" else None, SecurityMode.V2
    )
    calls: list[Any] = []

    async def detect(*args: Any, **kwargs: Any) -> Any:
        calls.append(args[2])
        if len(calls) == 1:
            # The options flow replaces the pair while the mobile probe runs.
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, **REPLACED}
            )
            return await first(*args, **kwargs)
        return await verified_by(ConnectionProfile.V4_20, SecurityMode.V2)(
            *args, **kwargs
        )

    with (
        patch(FETCH, return_value=MobileResult(candidate, snapshot())) as fetch,
        patch(DETECT, side_effect=detect),
        patch.object(flow, "_async_scan_device", return_value=scan),
        patch.object(hass.config_entries, "async_unload", return_value=True),
        patch.object(hass.config_entries, "async_reload", return_value=True),
        patch.object(
            flow,
            "async_update_reload_and_abort",
            return_value={"type": FlowResultType.ABORT},
        ) as update,
    ):
        await flow.async_step_mobile_login(FORM)
        await flow._mobile_task
        await flow.async_step_mobile_finish()
        await flow._probe_task
        result = await flow.async_step_probe_finish()
        assert result["step_id"] == "reconfigure"
        if when == "during_probe":
            # The verified result is discarded together with the staged pair.
            assert result["errors"] == {"base": "reconfigure_keys_changed"}
            assert flow._mobile_staged_credentials is None
        else:
            # A failed probe keeps the pair staged until the retry checks keys.
            assert flow._mobile_staged_credentials is not None
        await flow.async_step_reconfigure({})
        await flow._probe_task
        await flow.async_step_probe_finish()
    fetch.assert_awaited_once()
    # The retry verifies the entry's replaced pair, not the staged mobile one.
    assert calls[-1].local_key == REPLACED[CONF_LOCAL_KEY]
    assert calls[-1].sec_key == REPLACED[CONF_SEC_KEY]
    assert flow._mobile_staged_credentials is None
    updates = update.call_args.kwargs["data_updates"]
    assert CONF_LOCAL_KEY not in updates
    assert CONF_SEC_KEY not in updates
    assert CONF_CREDENTIAL_SOURCE not in updates
    assert updates[CONF_SECURITY_MODE] == "v2"


async def test_keys_replaced_during_the_app_login_reject_the_recovered_pair(
    hass: HomeAssistant,
) -> None:
    """A pair fetched for the old keys is never staged or verified."""
    flow, _ = await eligible_flow(hass)
    entry = reconfigure(flow, hass)
    flow._probe_source_step = "reconfigure"
    candidate = replace(make_credentials(), local_key=LOCAL, sec_key=SEC)

    async def fetch_while_keys_change(*_args: Any, **_kwargs: Any) -> MobileResult:
        # The options flow replaces the pair before the app login finishes.
        hass.config_entries.async_update_entry(entry, data={**entry.data, **REPLACED})
        return MobileResult(candidate, snapshot())

    with (
        patch(FETCH, side_effect=fetch_while_keys_change),
        patch(DETECT) as detect,
        patch.object(hass.config_entries, "async_unload") as unload,
    ):
        await flow.async_step_mobile_login(FORM)
        await flow._mobile_task
        result = await flow.async_step_mobile_finish()
    assert result["step_id"] == "mobile_result"
    assert result["errors"] == {"base": "reconfigure_keys_changed"}
    assert flow._mobile_staged_credentials is None
    assert not flow._mobile_keys_imported
    detect.assert_not_called()
    unload.assert_not_called()
    assert entry.data[CONF_LOCAL_KEY] == REPLACED[CONF_LOCAL_KEY]
    assert entry.data[CONF_SEC_KEY] == REPLACED[CONF_SEC_KEY]
