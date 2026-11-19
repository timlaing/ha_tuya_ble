"""Manual credential entry, the fallback when the Tuya cloud cannot help.

The QR-code login stays the primary setup route. This route accepts the
credentials a user already has, and creates an entry only after an
authenticated Bluetooth read proves that they belong to the selected device.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine, Mapping
import logging
import re
from typing import TYPE_CHECKING, Any

from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import selector
import probatio

from .config_flow_state import (
    ProbeResult,
    claimed_elsewhere,
    probe_state,
    reserve_device,
)
from .const import (
    CONF_CATEGORY,
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_LOCAL_KEY,
    CONF_PRODUCT_ID,
    CONF_SEC_KEY,
    CONF_UUID,
    DOMAIN,
)
from .device_registry import async_setup_registry, get_mapped_dp_ids
from .tuya_ble import (
    MAX_DEVICE_ID_LENGTH,
    MAX_UUID_LENGTH,
    ProbeOutcome,
    TuyaBLEAdvertisementInfo,
    TuyaBLEDeviceCredentials,
    is_valid_sec_key,
)

MANUAL_STEP = "manual"

# The classic login uses the first six bytes of the local key.
MIN_LOCAL_KEY_LENGTH = 6
_MAX_CODE_LENGTH = 32
# Printable ASCII without spaces: every identifier and key packed into a frame.
_TOKEN = re.compile(r"[!-~]+")

PASSWORD_SELECTOR = selector.TextSelector(
    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
)
# Secrets are never sent back to the frontend as defaults or suggestions.
_SECRET_FIELDS = frozenset({CONF_LOCAL_KEY, CONF_SEC_KEY})

# Manual setup messages stay on the documented config-flow logger.
_LOGGER = logging.getLogger(f"{__package__}.config_flow")


def _text(user_input: Mapping[str, Any], key: str) -> str:
    """Return a submitted field without surrounding whitespace."""
    value = user_input.get(key)
    return value.strip() if isinstance(value, str) else ""


def _is_token(value: str, max_length: int) -> bool:
    """Return whether a value fits a frame field as printable ASCII."""
    return 0 < len(value) <= max_length and _TOKEN.fullmatch(value) is not None


def validate_key_pair(
    user_input: Mapping[str, Any],
) -> tuple[tuple[str, str | None] | None, dict[str, str]]:
    """Validate a local key and optional security key as one pair.

    An empty or omitted security key means the pair has none, so it can be
    cleared; it is never taken from previously stored credentials.
    """
    errors: dict[str, str] = {}
    local_key = _text(user_input, CONF_LOCAL_KEY)
    sec_key = _text(user_input, CONF_SEC_KEY) or None
    if len(local_key) < MIN_LOCAL_KEY_LENGTH or not _TOKEN.fullmatch(local_key):
        errors[CONF_LOCAL_KEY] = "invalid_local_key"
    if sec_key is not None and not (
        _TOKEN.fullmatch(sec_key) and is_valid_sec_key(sec_key)
    ):
        errors[CONF_SEC_KEY] = "invalid_sec_key"
    if errors:
        return None, errors
    return (local_key, sec_key), errors


def parse_manual_credentials(
    user_input: Mapping[str, Any],
) -> tuple[TuyaBLEDeviceCredentials | None, dict[str, str]]:
    """Build credentials from the manual form, reporting each invalid field."""
    keys, errors = validate_key_pair(user_input)
    values = {
        field: _text(user_input, field)
        for field in (CONF_UUID, CONF_DEVICE_ID, CONF_CATEGORY, CONF_PRODUCT_ID)
    }
    limits = {
        CONF_UUID: MAX_UUID_LENGTH,
        CONF_DEVICE_ID: MAX_DEVICE_ID_LENGTH,
        CONF_CATEGORY: _MAX_CODE_LENGTH,
        CONF_PRODUCT_ID: _MAX_CODE_LENGTH,
    }
    for field, value in values.items():
        if not _is_token(value, limits[field]):
            errors[field] = f"invalid_{field}"
    if keys is None or errors:
        return None, errors
    local_key, sec_key = keys
    return TuyaBLEDeviceCredentials(
        uuid=values[CONF_UUID],
        local_key=local_key,
        device_id=values[CONF_DEVICE_ID],
        category=values[CONF_CATEGORY],
        product_id=values[CONF_PRODUCT_ID],
        device_name=_text(user_input, CONF_DEVICE_NAME) or None,
        product_model=None,
        product_name=None,
        sec_key=sec_key,
    ), errors


def manual_schema(addresses: Mapping[str, str]) -> probatio.Schema:
    """Return the manual credential form for the selectable addresses."""
    return probatio.Schema({
        probatio.Required(CONF_ADDRESS): probatio.In(dict(addresses)),
        probatio.Required(CONF_UUID): str,
        probatio.Required(CONF_LOCAL_KEY): PASSWORD_SELECTOR,
        probatio.Optional(CONF_SEC_KEY): PASSWORD_SELECTOR,
        probatio.Required(CONF_DEVICE_ID): str,
        probatio.Required(CONF_CATEGORY): str,
        probatio.Required(CONF_PRODUCT_ID): str,
        probatio.Optional(CONF_DEVICE_NAME): str,
    })


class _ManualCredentialsMixin:
    """Set up a device from credentials the user enters by hand."""

    hass: HomeAssistant
    discovery_info: BluetoothServiceInfoBleak | None
    _discovered_devices: dict[str, BluetoothServiceInfoBleak]
    _manual_defaults: dict[str, Any]
    _probe_claim_owner: object
    _probe_error: str | None
    _probe_refresh_failed: bool
    _probe_task: asyncio.Task[ProbeResult] | None
    _setup_lock: asyncio.Lock

    if TYPE_CHECKING:

        def async_show_form(
            self,
            *,
            step_id: str | None = None,
            data_schema: probatio.Schema | None = None,
            errors: dict[str, str] | None = None,
            description_placeholders: Mapping[str, str] | None = None,
            last_step: bool | None = None,
            preview: str | None = None,
        ) -> ConfigFlowResult:
            """Provided by ConfigFlow."""

        def async_abort(self, *, reason: str) -> ConfigFlowResult:
            """Provided by ConfigFlow."""

        def add_suggested_values_to_schema(
            self, data_schema: probatio.Schema, suggested_values: Mapping[str, Any]
        ) -> probatio.Schema:
            """Provided by ConfigFlow."""

        async def async_set_unique_id(
            self, unique_id: str | None = None, *, raise_on_progress: bool = True
        ) -> Any:
            """Provided by ConfigFlow."""

        def _abort_if_unique_id_configured(self) -> None:
            """Provided by ConfigFlow."""

        def _abort_if_flow_removed(self) -> None:
            """Provided by the config flow."""

        async def _async_refresh_discovered_devices(self) -> None:
            """Provided by the config flow."""

        async def _async_scan_identity(
            self, address: str
        ) -> tuple[
            BluetoothServiceInfoBleak | None,
            TuyaBLEAdvertisementInfo | None,
            str | None,
        ]:
            """Provided by the config flow."""

        def _start_probe(
            self,
            discovery_info: BluetoothServiceInfoBleak,
            advertisement: TuyaBLEAdvertisementInfo,
            verification: Coroutine[Any, Any, ProbeResult],
            source_step: str,
        ) -> ConfigFlowResult:
            """Provided by the config flow."""

        async def async_step_probe(
            self, user_input: dict[str, Any] | None = None
        ) -> ConfigFlowResult:
            """Provided by the config flow."""

        async def _async_verify_v4(
            self,
            discovery: BluetoothServiceInfoBleak,
            credentials: TuyaBLEDeviceCredentials,
        ) -> ProbeResult:
            """Provided by the config flow."""

        async def _async_try_probe_cloud_candidate(
            self,
            discovery_info: BluetoothServiceInfoBleak,
            advertisement: TuyaBLEAdvertisementInfo,
            credentials: TuyaBLEDeviceCredentials,
        ) -> tuple[ProbeOutcome | None, str | None]:
            """Provided by the config flow."""

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for credentials, then verify them against the selected device."""
        errors: dict[str, str] = {}
        if self._probe_error is not None:
            errors["base"], self._probe_error = self._probe_error, None
        if user_input is not None:
            self._manual_defaults = {
                key: value
                for key, value in user_input.items()
                if key not in _SECRET_FIELDS
            }
            credentials, errors = parse_manual_credentials(user_input)
            if credentials is not None:
                result = await self._async_setup_manual(
                    user_input[CONF_ADDRESS], credentials
                )
                if not isinstance(result, str):
                    return result
                errors["base"] = result
        addresses = await self._async_manual_addresses()
        if not addresses:
            return self.async_abort(reason="no_unconfigured_devices")
        suggested = {CONF_ADDRESS: next(iter(addresses)), **self._manual_defaults}
        return self.async_show_form(
            step_id=MANUAL_STEP,
            data_schema=self.add_suggested_values_to_schema(
                manual_schema(addresses), suggested
            ),
            errors=errors,
        )

    async def _async_manual_addresses(self) -> dict[str, str]:
        """List the discovered device, or every unconfigured Tuya BLE device."""
        if self.discovery_info is not None:
            devices = [self.discovery_info]
        else:
            await self._async_refresh_discovered_devices()
            devices = list(self._discovered_devices.values())
        return {
            device.address: f"{device.name or device.address} ({device.address})"
            for device in devices
        }

    async def _async_setup_manual(
        self, address: str, credentials: TuyaBLEDeviceCredentials
    ) -> ConfigFlowResult | str:
        """Start verifying the entered credentials, or return the error to show."""
        async with self._setup_lock:
            self._abort_if_flow_removed()
            if self._probe_task is not None:
                return await self.async_step_probe()
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_flow_removed()
            self._abort_if_unique_id_configured()
            if any(
                entry.data.get(CONF_DEVICE_ID) == credentials.device_id
                for entry in self.hass.config_entries.async_entries(DOMAIN)
            ):
                return self.async_abort(reason="already_configured")
            await async_setup_registry(self.hass)
            if not get_mapped_dp_ids(credentials.category, credentials.product_id):
                # Without a descriptor nothing can prove the credentials match.
                return "unknown_product"
            discovery, advertisement, error = await self._async_scan_identity(address)
            if discovery is None or advertisement is None:
                return str(error)
            _LOGGER.debug(
                "%s: verifying manually entered credentials (category: %s, "
                "product_id: %s)",
                address,
                credentials.category,
                credentials.product_id,
            )
            self._probe_refresh_failed = False
            return self._start_probe(
                discovery,
                advertisement,
                self._async_verify_manual(discovery, advertisement, credentials),
                MANUAL_STEP,
            )

    async def _async_verify_manual(
        self,
        discovery: BluetoothServiceInfoBleak,
        advertisement: TuyaBLEAdvertisementInfo,
        credentials: TuyaBLEDeviceCredentials,
    ) -> ProbeResult:
        """Accept entered credentials only after an authenticated read."""
        if advertisement.protocol_version == 4:
            return await self._async_verify_v4(discovery, credentials)
        lock, claims = probe_state(self.hass)
        async with lock:
            if claimed_elsewhere(
                claims, credentials.device_id, self._probe_claim_owner
            ):
                return None, None, "device_verification_expired"
            outcome, pairing_uuid = await self._async_try_probe_cloud_candidate(
                discovery, advertisement, credentials
            )
            self._abort_if_flow_removed()
            if outcome == ProbeOutcome.MATCHED and pairing_uuid is not None:
                reserve_device(
                    claims,
                    credentials.device_id,
                    self._probe_claim_owner,
                    discovery.address,
                )
                return credentials, pairing_uuid, ""
        if outcome in (ProbeOutcome.INCONCLUSIVE, ProbeOutcome.RETRYABLE):
            return None, None, "bluetooth_probe_failed"
        return None, None, "manual_credentials_rejected"
