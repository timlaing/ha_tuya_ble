"""One-time mobile login: exact identity, atomic keys and best-effort metadata."""

# pylint: disable=protected-access

import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
import pytest
from tuya_mobile import (
    TuyaMobileAccountLocked,
    TuyaMobileCaptchaRequired,
    TuyaMobileInvalidAuth,
    TuyaMobileMFARequired,
    TuyaMobileProfileExpired,
)

from custom_components.tuya_ble.mobile_cloud import (
    MobileLogin,
    async_fetch_mobile_device,
    region_from_endpoint,
)
from tests.conftest import make_credentials

LOGIN = MobileLogin("tuya_smart", "private@example.org", "PRIVATE_PASSWORD", "44")
LOCAL = "0123456789abcdef"
SEC = "fedcba9876543210"
FACTORY = "custom_components.tuya_ble.mobile_cloud.TuyaPasswordClient.for_application"


@pytest.mark.parametrize(
    ("host", "region"),
    [
        ("https://apigw.tuyaeu.com", "eu"),
        ("https://apigw.tuyaus.com", "us"),
        ("https://apigw.tuyacn.com", "cn"),
        ("https://apigw.tuyain.com", "in"),
        ("https://a1-sg.iotbing.com", "sg"),
        ("https://tuyaeu.com", "eu"),
        (None, None),
        ("http://apigw.tuyaeu.com", None),
        ("https://tuyaeu.com.attacker.org", None),
        ("https://user:password@apigw.tuyaeu.com", None),
        ("https://apigw.tuyaeu.com:444", None),
        ("https://apigw.tuyaeu.com:bad", None),
    ],
)
def test_qr_region_resolution(host: Any, region: str | None) -> None:
    """Credentials only go to fixed endpoints for recognized Tuya regions."""
    assert region_from_endpoint(host) == region


def record(credentials: Any, **changes: Any) -> dict[str, Any]:
    """Build a target-device mobile detail response with test-only keys."""
    return {
        "devId": credentials.device_id,
        "uuid": credentials.uuid,
        "productId": credentials.product_id,
        "localKey": LOCAL,
        "secKey": SEC,
        "communication": {"ble": True},
        **changes,
    }


async def fetch(
    hass: HomeAssistant,
    credentials: Any = None,
    device: Any = None,
    responses: Any = None,
    login_error: Any = None,
) -> tuple[Any, Any, Any]:
    """Drive the public cloud adapter with a no-network client."""
    credentials = credentials or make_credentials()
    fake = SimpleNamespace(
        login_with_password=AsyncMock(side_effect=login_error),
        _call=AsyncMock(
            side_effect=responses
            or [
                {"result": device if device is not None else record(credentials)},
                {"result": {"id": credentials.product_id, "productVer": "1.2"}},
                {"dps": {"13": 88}},
            ]
        ),
    )
    with (
        patch(
            "custom_components.tuya_ble.mobile_cloud.async_get_clientsession",
            return_value=object(),
        ),
        patch(
            FACTORY,
            return_value=fake,
        ) as factory,
    ):
        result = await async_fetch_mobile_device(
            hass, LOGIN, "eu", credentials, credentials.uuid
        )
    return result, fake, factory


async def test_atomic_mobile_key_pair_and_snapshot(hass: HomeAssistant) -> None:
    """The pair comes entirely from mobile, while QR metadata stays intact."""
    original = replace(make_credentials(), cloud_info={"product_name": "QR product"})
    result, fake, factory = await fetch(hass, original)
    assert result.credentials is not None
    assert result.credentials.local_key == LOCAL
    assert result.credentials.sec_key == SEC
    assert original.local_key != LOCAL
    assert original.sec_key is None
    assert result.credentials.cloud_info == original.cloud_info
    assert not result.error
    assert result.snapshot["records"]["device"]["communication"]["ble"] is True
    assert result.snapshot["operations"] == {
        "device": True,
        "product": True,
        "status": True,
    }
    assert LOCAL not in json.dumps(result.snapshot)
    assert SEC not in json.dumps(result.snapshot)
    assert "PRIVATE_PASSWORD" not in repr(LOGIN)
    factory.assert_called_once()
    assert factory.call_args.kwargs["endpoint"] == "https://a1.tuyaeu.com/api.json"
    assert factory.call_args.kwargs["max_login_attempts"] == 1
    fake.login_with_password.assert_awaited_once_with(LOGIN.password, country_code="44")
    assert [call.args[0] for call in fake._call.await_args_list] == [
        "thing.m.device.get",
        "thing.m.device.ref.info.list",
        "s.m.dev.dp.get",
    ]


@pytest.mark.parametrize(
    "changes",
    [
        {"devId": "another"},
        {"uuid": "another"},
        {"productId": "another"},
    ],
)
async def test_mismatched_identity_never_imports_keys(
    hass: HomeAssistant, changes: dict[str, Any]
) -> None:
    """No fuzzy matching and no metadata from another device."""
    original = make_credentials()
    result, fake, _ = await fetch(hass, original, record(original, **changes))
    assert result.error == "mobile_identity_mismatch"
    assert result.credentials is None
    assert result.snapshot is None
    assert fake._call.await_count == 1


@pytest.mark.parametrize("changes", [{"uuid": None}, {"productId": None}])
async def test_missing_optional_identity_keeps_exact_device_id(
    hass: HomeAssistant, changes: dict[str, Any]
) -> None:
    """A device ID is mandatory; optional fields need not be present."""
    original = make_credentials()
    result, _, _ = await fetch(hass, original, record(original, **changes))
    assert result.credentials is not None


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"secKey": None}, "mobile_missing_key"),
        ({"secKey": ""}, "mobile_missing_key"),
        ({"secKey": "short"}, "mobile_invalid_keys"),
        ({"localKey": None}, "mobile_invalid_keys"),
        ({"localKey": "short"}, "mobile_invalid_keys"),
    ],
)
async def test_missing_or_invalid_keys_preserve_diagnostics(
    hass: HomeAssistant, changes: dict[str, Any], error: str
) -> None:
    """Successfully fetched metadata survives even when the key pair is unusable."""
    original = make_credentials()
    result, _, _ = await fetch(hass, original, record(original, **changes))
    assert result.error == error
    assert result.credentials is None
    assert result.snapshot is not None


async def test_snake_case_keys(hass: HomeAssistant) -> None:
    """The adapter accepts either verified API field naming convention."""
    original = make_credentials()
    result, _, _ = await fetch(
        hass,
        original,
        record(original, localKey=None, secKey=None, local_key=LOCAL, sec_key=SEC),
    )
    assert result.credentials.sec_key == SEC


@pytest.mark.parametrize(
    "optional",
    [
        RuntimeError("SECRET"),
        {"success": False},
        {"errorCode": "PRIVATE_CODE"},
        {"result": {"id": "another-product"}},
        {},
    ],
)
async def test_optional_enrichment_failure_preserves_pair(
    hass: HomeAssistant, optional: Any
) -> None:
    """Failures of extra reads cannot discard credentials or device details."""
    original = make_credentials()
    result, _, _ = await fetch(
        hass,
        original,
        responses=[
            {"result": record(original)},
            optional,
            optional,
        ],
    )
    assert result.credentials is not None
    assert "SECRET" not in json.dumps(result.snapshot)


@pytest.mark.parametrize(
    "response", [{"success": False}, {"errorCode": "PRIVATE_CODE"}]
)
async def test_device_business_failure(
    hass: HomeAssistant, response: dict[str, Any]
) -> None:
    """The public error is safe and no optional calls follow a failed detail read."""
    result, fake, _ = await fetch(hass, responses=[response])
    assert result.error == "mobile_device_not_found"
    assert result.snapshot is None
    assert fake._call.await_count == 1


@pytest.mark.parametrize(
    ("exception", "error"),
    [
        (TuyaMobileInvalidAuth, "mobile_invalid_auth"),
        (TuyaMobileCaptchaRequired, "mobile_captcha"),
        (TuyaMobileMFARequired, "mobile_mfa"),
        (TuyaMobileAccountLocked, "mobile_account_locked"),
        (TuyaMobileProfileExpired, "mobile_profile_expired"),
        (RuntimeError, "mobile_connection_failed"),
    ],
)
async def test_safe_login_errors(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture, exception: Any, error: str
) -> None:
    """Never expose exception text that could contain login secrets."""
    result, fake, _ = await fetch(hass, login_error=exception("PRIVATE_PASSWORD"))
    assert result.error == error
    assert not fake._call.await_count
    assert "PRIVATE_PASSWORD" not in caplog.text


async def test_unsupported_app_or_region(hass: HomeAssistant) -> None:
    """Unknown routing must not send user credentials anywhere."""
    for login, region in ((LOGIN, "unknown"), (replace(LOGIN, app="unknown"), "eu")):
        result = await async_fetch_mobile_device(
            hass, login, region, make_credentials(), "uuid"
        )
        assert result.error == "mobile_unknown_region"


async def test_bounded_fetch_and_cancellation(hass: HomeAssistant) -> None:
    """Cloud requests are bounded and cancelling a flow is not an auth failure."""
    started = asyncio.Event()

    async def wait(*_args: Any, **_kwargs: Any) -> None:
        started.set()
        await asyncio.Event().wait()

    fake = SimpleNamespace(login_with_password=wait)
    with (
        patch(
            "custom_components.tuya_ble.mobile_cloud.async_get_clientsession",
            return_value=object(),
        ),
        patch(
            FACTORY,
            return_value=fake,
        ),
        patch("custom_components.tuya_ble.mobile_cloud.MOBILE_TIMEOUT", 0.01),
    ):
        result = await async_fetch_mobile_device(
            hass, LOGIN, "eu", make_credentials(), "uuid"
        )
        assert result.error == "mobile_connection_failed"
        started.clear()
        task = asyncio.create_task(
            async_fetch_mobile_device(hass, LOGIN, "eu", make_credentials(), "uuid")
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_bounded_identity_search(hass: HomeAssistant) -> None:
    """No unbounded recursion through malformed device responses."""
    original = make_credentials()
    deep = record(original)
    for _ in range(12):
        deep = {"child": deep}
    result, _, _ = await fetch(hass, device=deep)
    assert result.error == "mobile_identity_mismatch"


async def test_device_record_inside_a_list(hass: HomeAssistant) -> None:
    """Device matching handles list-wrapped records without fuzzy selection."""
    original = make_credentials()
    result, _, _ = await fetch(hass, device=[None, record(original)])
    assert result.credentials is not None


async def test_optional_read_timeouts_do_not_discard_keys(hass: HomeAssistant) -> None:
    """Enrichment has its own deadline inside the overall login deadline."""
    original = make_credentials()

    async def response(action: str, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        if action == "thing.m.device.get":
            return {"result": record(original)}
        await asyncio.Event().wait()
        return {}

    fake = SimpleNamespace(login_with_password=AsyncMock(), _call=response)
    with (
        patch(
            "custom_components.tuya_ble.mobile_cloud.async_get_clientsession",
            return_value=object(),
        ),
        patch(FACTORY, return_value=fake),
        patch("custom_components.tuya_ble.mobile_cloud.ENRICH_TIMEOUT", 0.01),
    ):
        result = await async_fetch_mobile_device(
            hass, LOGIN, "eu", original, original.uuid
        )
    assert result.credentials is not None
    assert result.snapshot is not None
    assert result.snapshot["operations"] == {
        "device": True,
        "product": False,
        "status": False,
    }


async def test_identity_search_stops_at_node_budget(hass: HomeAssistant) -> None:
    """Wide JSON responses cannot cause an unbounded account/device scan."""
    result, _, _ = await fetch(hass, device=[[None] * 256 for _ in range(256)])
    assert result.error == "mobile_identity_mismatch"


async def test_slow_mandatory_read_preserves_keys_when_overall_budget_runs_out(
    hass: HomeAssistant,
) -> None:
    """Optional reads cannot erase a pair retrieved just before the deadline."""
    original = make_credentials()
    loop = asyncio.get_running_loop()
    start = loop.time()
    clock = start
    operations: list[str] = []

    def now() -> float:
        """Expose controlled monotonic time to deadline and event-loop timers."""
        return clock

    async def response(action: str, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal clock
        operations.append(action)
        if action == "thing.m.device.get":
            # No yield: return the successful record before any timer can fire.
            clock = start + 0.15
            return {"result": record(original)}
        # The mandatory timeout scope has ended. Expire only enrichment's timer.
        clock = start + 0.3
        await asyncio.Event().wait()
        return {}

    fake = SimpleNamespace(login_with_password=AsyncMock(), _call=response)
    with (
        patch(
            "custom_components.tuya_ble.mobile_cloud.async_get_clientsession",
            return_value=object(),
        ),
        patch(FACTORY, return_value=fake),
        patch("custom_components.tuya_ble.mobile_cloud.MOBILE_TIMEOUT", 0.2),
        patch.object(loop, "time", side_effect=now),
    ):
        result = await async_fetch_mobile_device(
            hass, LOGIN, "eu", original, original.uuid
        )
    assert result.credentials is not None
    assert result.credentials.local_key == LOCAL
    assert result.credentials.sec_key == SEC
    assert result.snapshot is not None
    assert result.snapshot["operations"] == {
        "device": True,
        "product": False,
        "status": False,
    }
    assert operations == ["thing.m.device.get", "thing.m.device.ref.info.list"]


async def test_cancellation_during_enrichment_is_not_a_partial_success(
    hass: HomeAssistant,
) -> None:
    """External flow cancellation still propagates after mandatory reads succeed."""
    original = make_credentials()
    enriching = asyncio.Event()

    async def response(action: str, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        if action == "thing.m.device.get":
            return {"result": record(original)}
        enriching.set()
        await asyncio.Event().wait()
        return {}

    fake = SimpleNamespace(login_with_password=AsyncMock(), _call=response)
    with (
        patch(
            "custom_components.tuya_ble.mobile_cloud.async_get_clientsession",
            return_value=object(),
        ),
        patch(FACTORY, return_value=fake),
    ):
        task = asyncio.create_task(
            async_fetch_mobile_device(hass, LOGIN, "eu", original, original.uuid)
        )
        await enriching.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.parametrize("alias", ["devId", "deviceId"])
async def test_primary_device_identifier_cannot_be_overridden_by_id_alias(
    hass: HomeAssistant,
    alias: str,
) -> None:
    """Match the canonical alias ordering used by the pinned mobile library."""
    original = make_credentials()
    response = record(original, devId=None, id=original.device_id)
    response[alias] = "different-device"
    result, _, _ = await fetch(hass, device=response)
    assert result.error == "mobile_identity_mismatch"
