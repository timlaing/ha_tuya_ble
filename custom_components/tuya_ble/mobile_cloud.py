"""One-time mobile-app login and read-only, exact-device credential retrieval."""

# cspell:ignore tuyaeu tuyaus tuyacn tuyain iotbing

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from itertools import islice
from typing import Any
from urllib.parse import urlsplit

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from tuya_mobile import TuyaPasswordClient

from .config_flow_manual import validate_key_pair
from .const import CONF_LOCAL_KEY, CONF_SEC_KEY
from .mobile_snapshot import capture_mobile_snapshot
from .tuya_ble import TuyaBLEDeviceCredentials

MOBILE_TIMEOUT = 60
ENRICH_TIMEOUT = 5
REGIONS = {
    "eu": ("44", "https://a1.tuyaeu.com/api.json"),
    "us": ("1", "https://a1.tuyaus.com/api.json"),
    "cn": ("86", "https://a1.tuyacn.com/api.json"),
    "in": ("91", "https://a1.tuyain.com/api.json"),
    "sg": ("65", "https://a1-sg.iotbing.com/api.json"),
}
_DOMAINS = {
    "tuyaeu.com": "eu",
    "tuyaus.com": "us",
    "tuyacn.com": "cn",
    "tuyain.com": "in",
    "iotbing.com": "sg",
}
_ERRORS = {
    "TuyaMobileInvalidAuth": "mobile_invalid_auth",
    "TuyaMobileCaptchaRequired": "mobile_captcha",
    "TuyaMobileMFARequired": "mobile_mfa",
    "TuyaMobileAccountLocked": "mobile_account_locked",
    "TuyaMobileProfileExpired": "mobile_profile_expired",
}


def region_from_endpoint(endpoint: Any) -> str | None:
    """Map trusted QR-service hosts to fixed mobile endpoints, never arbitrary URLs."""
    if not isinstance(endpoint, str):
        return None
    try:
        url = urlsplit(endpoint)
        if (
            url.scheme != "https"
            or url.username
            or url.password
            or url.port not in (None, 443)
        ):
            return None
    except ValueError:
        return None
    host = url.hostname or ""
    return next(
        (
            region
            for domain, region in _DOMAINS.items()
            if host == domain or host.endswith("." + domain)
        ),
        None,
    )


@dataclass(frozen=True)
class MobileLogin:
    """Ephemeral user submission; representations must not contain secrets."""

    app: str
    username: str = field(repr=False)
    password: str = field(repr=False)
    country_code: str


@dataclass(frozen=True)
class MobileResult:
    """Only sanitized metadata and the operational key pair leave the adapter."""

    credentials: TuyaBLEDeviceCredentials | None = field(default=None, repr=False)
    snapshot: dict[str, Any] | None = None
    error: str = ""


def _target(
    response: Any, expected: str, names: tuple[str, ...]
) -> dict[str, Any] | None:
    """Find only an explicitly matching record, bounded in size and depth."""
    pending = deque([(response, 0)])
    for _ in range(4096):
        if not pending:
            break
        item, depth = pending.popleft()
        if isinstance(item, dict):
            if any(str(item.get(name, "")) == expected for name in names):
                return item
            if depth < 8:
                pending.extend(
                    (child, depth + 1)
                    for child in islice(item.values(), min(256, 4096 - len(pending)))
                )
        elif isinstance(item, list) and depth < 8:
            pending.extend(
                (child, depth + 1) for child in item[: min(256, 4096 - len(pending))]
            )
    return None


def _identity_matches(
    record: Mapping[str, Any], credentials: TuyaBLEDeviceCredentials, cloud_uuid: str
) -> bool:
    """Do not import keys for another product or cloud identity."""
    product = record.get("productId") or record.get("product_id")
    uuid = record.get("uuid")
    return (not product or product == credentials.product_id) and (
        not uuid or uuid == cloud_uuid
    )


async def _enrich(
    client: TuyaPasswordClient, credentials: TuyaBLEDeviceCredentials
) -> tuple[dict[str, Any], dict[str, bool]]:
    """Best-effort reads of product metadata and status; never modify a device."""
    records: dict[str, Any] = {}
    operations: dict[str, bool] = {}
    for name, action, version, payload in (
        (
            "product",
            "thing.m.device.ref.info.list",
            "5.4",
            {"productIds": [credentials.product_id], "zigbeeGroup": True},
        ),
        (
            "status",
            "s.m.dev.dp.get",
            "1.0",
            {"devId": credentials.device_id, "gwId": ""},
        ),
    ):
        try:
            async with asyncio.timeout(ENRICH_TIMEOUT):
                response = await client._call(action, payload, version=version)  # pylint: disable=protected-access
            if response.get("success") is False or response.get("errorCode"):
                operations[name] = False
                continue
            if name == "product":
                response = _target(
                    response, credentials.product_id, ("id", "productId")
                )
            if response is not None:
                records[name] = response
            operations[name] = response is not None
        except Exception:  # pylint: disable=broad-exception-caught
            # Do not log exceptions from an authentication-sensitive API.
            operations[name] = False
    return records, operations


async def async_fetch_mobile_device(
    hass: HomeAssistant,
    login: MobileLogin,
    region: str,
    credentials: TuyaBLEDeviceCredentials,
    cloud_uuid: str,
) -> MobileResult:
    """Login once, retrieve this device's pair and capture useful metadata."""
    if region not in REGIONS or login.app not in ("smart_life", "tuya_smart"):
        return MobileResult(error="mobile_unknown_region")
    try:
        async with asyncio.timeout(MOBILE_TIMEOUT):
            return await _fetch(hass, login, region, credentials, cloud_uuid)
    except Exception as err:  # pylint: disable=broad-exception-caught
        return MobileResult(
            error=_ERRORS.get(type(err).__name__, "mobile_connection_failed")
        )


async def _fetch(
    hass: HomeAssistant,
    login: MobileLogin,
    region: str,
    credentials: TuyaBLEDeviceCredentials,
    cloud_uuid: str,
) -> MobileResult:
    """Do not close HA's shared session or persist mobile login/session values."""
    client = TuyaPasswordClient.for_application(
        login.app,
        async_get_clientsession(hass),
        username=login.username,
        endpoint=REGIONS[region][1],
        max_login_attempts=1,
    )
    await client.login_with_password(login.password, country_code=login.country_code)
    response = await client._call(  # pylint: disable=protected-access
        "thing.m.device.get", {"devId": credentials.device_id}, version="4.1"
    )
    if response.get("success") is False or response.get("errorCode"):
        return MobileResult(error="mobile_device_not_found")
    record = _target(response, credentials.device_id, ("devId", "deviceId", "id"))
    if record is None or not _identity_matches(record, credentials, cloud_uuid):
        return MobileResult(error="mobile_identity_mismatch")
    records, operations = await _enrich(client, credentials)
    records["device"] = record
    operations["device"] = True
    snapshot = capture_mobile_snapshot(login.app, records, operations)
    # Import the two keys together, never mix a mobile secKey with the old localKey.
    local = record.get("localKey") or record.get("local_key")
    sec = record.get("secKey") or record.get("sec_key")
    pair, errors = validate_key_pair({CONF_LOCAL_KEY: local, CONF_SEC_KEY: sec})
    if errors or pair is None:
        return MobileResult(snapshot=snapshot, error="mobile_invalid_keys")
    if pair[1] is None:
        return MobileResult(snapshot=snapshot, error="mobile_missing_key")
    return MobileResult(
        credentials=replace(
            credentials, local_key=pair[0], sec_key=pair[1], connection_profile=None
        ),
        snapshot=snapshot,
    )
