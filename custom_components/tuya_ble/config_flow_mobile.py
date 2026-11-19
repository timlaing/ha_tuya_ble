"""Conditional app-login forms for devices missing their Security V2 key."""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, Any, cast

from homeassistant.config_entries import SOURCE_RECONFIGURE, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers import selector
import probatio

from .config_flow_state import _consume_probe_result
from .config_flow_unverified import UnverifiedSetup
from .const import (
    CONF_CLOUD_REGION,
    CONF_CLOUD_UUID,
    CONF_CONNECTION_PROFILE,
    CONF_CREDENTIAL_SOURCE,
    CONF_ENDPOINT,
    CONF_LOCAL_KEY,
    CONF_MOBILE_SNAPSHOT,
    CONF_SEC_KEY,
    CONF_UUID,
)
from .mobile_cloud import (
    REGIONS,
    MobileLogin,
    MobileResult,
    async_fetch_mobile_device,
    region_from_endpoint,
)
from .mobile_snapshot import filter_mobile_snapshot
from .tuya_ble import (
    TuyaBLEAdvertisementInfo,
    TuyaBLEDeviceCredentials,
    is_valid_sec_key,
)

if TYPE_CHECKING:
    from .config_flow import TuyaBLEConfigFlow

CONF_APP = "mobile_app"
CONF_COUNTRY = "country_code"
_PHONE = re.compile(r"\+?\d[\d ()-]{4,24}", re.ASCII)


def _is_email(value: str) -> bool:
    """Basic bounded email validation without a backtracking expression."""
    local, separator, domain = value.rpartition("@")
    return bool(
        len(value) <= 254
        and local
        and separator
        and "@" not in local
        and "." in domain
        and not domain.startswith(".")
        and not domain.endswith(".")
        and not any(character.isspace() for character in value)
    )


def needs_mobile_login(
    advertisement: TuyaBLEAdvertisementInfo,
    credentials: TuyaBLEDeviceCredentials,
    error: str,
) -> bool:
    """Do not request account passwords for generic Bluetooth range failures."""
    return (
        advertisement.protocol_version == 4
        and advertisement.security_v2
        and (credentials.sec_key is None or not is_valid_sec_key(credentials.sec_key))
        and error in ("v4_failed_missing_key", "v4_failed_credentials")
    )


def mobile_schema() -> probatio.Schema:
    """Keep the requested field order, with no password or login defaults."""
    return probatio.Schema({
        probatio.Required(CONF_APP): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=["tuya_smart", "smart_life"], translation_key="mobile_app"
            )
        ),
        probatio.Required(CONF_USERNAME): str,
        probatio.Required(CONF_PASSWORD): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
        ),
        probatio.Optional(CONF_COUNTRY): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=1, max=999, step=1, mode=selector.NumberSelectorMode.BOX
            )
        ),
    })


def parse_mobile_login(
    submitted: dict[str, Any], region: str | None
) -> tuple[MobileLogin | None, dict[str, str]]:
    """Validate before a password submission; email may override its region code."""
    errors: dict[str, str] = {}
    app = submitted.get(CONF_APP)
    username = submitted.get(CONF_USERNAME)
    password = submitted.get(CONF_PASSWORD)
    username = username.strip() if isinstance(username, str) else ""
    email = "@" in username
    if app not in ("tuya_smart", "smart_life"):
        errors[CONF_APP] = "mobile_invalid_app"
    if not (_is_email(username) if email else _PHONE.fullmatch(username)):
        errors[CONF_USERNAME] = "mobile_invalid_username"
    if not isinstance(password, str) or not password:
        errors[CONF_PASSWORD] = "mobile_password_required"
    code = submitted.get(CONF_COUNTRY)
    if code is not None:
        if (
            isinstance(code, bool)
            or not isinstance(code, int | float)
            or not 1 <= code <= 999
            or int(code) != code
        ):
            errors[CONF_COUNTRY] = "mobile_invalid_country"
    elif not email:
        errors[CONF_COUNTRY] = "mobile_country_required"
    if region not in REGIONS:
        errors["base"] = "mobile_unknown_region"
    if errors:
        return None, errors
    country = str(int(code)) if code is not None else REGIONS[cast(str, region)][0]
    return MobileLogin(cast(str, app), username, cast(str, password), country), {}


class _MobileLoginMixin:
    """Fetch mobile keys once, then reuse the normal bounded BLE verification."""

    _mobile_pending: UnverifiedSetup | None = None
    _mobile_task: asyncio.Task[MobileResult] | None = None
    _mobile_snapshot: dict[str, Any] | None = None
    _mobile_attempted = False
    _mobile_keys_imported = False
    _mobile_staged_credentials: TuyaBLEDeviceCredentials | None = None
    _mobile_qr_rejected = False
    _mobile_region_login = False
    _mobile_error = ""

    def _mobile_region(self) -> str | None:
        """Prefer the QR gateway region; existing entries may need a new QR login."""
        flow = cast("TuyaBLEConfigFlow", self)
        if self._mobile_qr_rejected:
            return None
        region = region_from_endpoint(flow._data.get(CONF_ENDPOINT))  # pylint: disable=protected-access
        if region is not None or flow.source != SOURCE_RECONFIGURE:
            return region
        entry = flow._get_reconfigure_entry()  # pylint: disable=protected-access
        stored = entry.data.get(CONF_CLOUD_REGION)
        return (
            stored
            if isinstance(stored, str) and stored in REGIONS
            else region_from_endpoint(entry.options.get(CONF_ENDPOINT))
        )

    def _mobile_entry_updates(self) -> dict[str, Any]:
        """Separate metadata from BLE credentials and never store app login data."""
        updates: dict[str, Any] = {}
        region = self._mobile_region()
        if region is not None:
            updates[CONF_CLOUD_REGION] = region
        if self._mobile_snapshot is not None:
            updates[CONF_MOBILE_SNAPSHOT] = filter_mobile_snapshot(
                self._mobile_snapshot
            )
            if self._mobile_keys_imported:
                updates[CONF_CREDENTIAL_SOURCE] = "mobile_app"
        return updates

    def _mobile_reconfigure_updates(
        self, credentials: TuyaBLEDeviceCredentials
    ) -> dict[str, Any]:
        """Replace operational keys only after a successful mobile/BLE verification."""
        updates = {
            **self._mobile_entry_updates(),
            CONF_CONNECTION_PROFILE: str(credentials.connection_profile),
        }
        if self._mobile_keys_imported:
            updates[CONF_LOCAL_KEY] = credentials.local_key
            updates[CONF_SEC_KEY] = credentials.sec_key
        return updates

    def _cancel_mobile_login(self) -> None:
        """Cancel the ephemeral login task when Home Assistant removes the flow."""
        if self._mobile_task is not None and not self._mobile_task.done():
            self._mobile_task.cancel()
        self._mobile_pending = None
        self._mobile_staged_credentials = None

    def _store_mobile_diagnostics(self) -> None:
        """On failed recovery, update metadata only after BLE ownership is restored."""
        flow = cast("TuyaBLEConfigFlow", self)
        if self._mobile_snapshot is not None and flow.source == SOURCE_RECONFIGURE:
            entry = flow._get_reconfigure_entry()  # pylint: disable=protected-access
            flow.hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_MOBILE_SNAPSHOT: self._mobile_snapshot}
            )

    async def async_step_mobile_offer(
        self, _user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer the extra app login, without forcing it on the user."""
        flow = cast("TuyaBLEConfigFlow", self)
        if self._mobile_pending is None:
            return flow.async_abort(reason="unknown")
        if self._mobile_task is not None:
            return await self.async_step_mobile_fetch()
        return flow.async_show_menu(
            step_id="mobile_offer",
            menu_options=[
                "mobile_login",
                "reconfigure" if flow.source == SOURCE_RECONFIGURE else "unverified",
            ],
        )

    async def async_step_mobile_login(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show app, username, secure password and optional numeric country code."""
        flow = cast("TuyaBLEConfigFlow", self)
        flow._abort_if_flow_removed()  # pylint: disable=protected-access
        if self._mobile_pending is None:
            return flow.async_abort(reason="unknown")
        if self._mobile_task is not None:
            return await self.async_step_mobile_fetch()
        if self._mobile_region() is None and flow.source == SOURCE_RECONFIGURE:
            self._mobile_region_login = True
            return await flow.async_step_login()
        errors: dict[str, str] = {}
        if user_input is not None:
            login, errors = parse_mobile_login(user_input, self._mobile_region())
            if login is not None:
                _, _, credentials, _ = self._mobile_pending
                cloud_uuid = credentials.uuid
                if flow.source == SOURCE_RECONFIGURE:
                    entry = flow._get_reconfigure_entry()  # pylint: disable=protected-access
                    cloud_uuid = entry.data.get(CONF_CLOUD_UUID, entry.data[CONF_UUID])
                self._mobile_task = flow.hass.async_create_task(
                    async_fetch_mobile_device(
                        flow.hass,
                        login,
                        cast(str, self._mobile_region()),
                        credentials,
                        cloud_uuid,
                    )
                )
                self._mobile_task.add_done_callback(_consume_probe_result)
                return await self.async_step_mobile_fetch()
        return flow.async_show_form(
            step_id="mobile_login", data_schema=mobile_schema(), errors=errors
        )

    async def _async_mobile_region_ready(self) -> ConfigFlowResult:
        """Validate a new QR login for an old entry before using its region."""
        flow = cast("TuyaBLEConfigFlow", self)
        self._mobile_region_login = False
        if self._mobile_pending is None or flow._manager is None:  # pylint: disable=protected-access
            return flow.async_abort(reason="unknown")
        credentials = self._mobile_pending[2]
        entry = flow._get_reconfigure_entry()  # pylint: disable=protected-access
        matched = await flow._manager.get_device_credentials_by_uuid(  # pylint: disable=protected-access
            entry.data.get(CONF_CLOUD_UUID, credentials.uuid)
        )
        flow._abort_if_flow_removed()  # pylint: disable=protected-access
        if matched is None or matched.device_id != credentials.device_id:
            flow._data.clear()  # pylint: disable=protected-access
            flow._manager = None  # pylint: disable=protected-access
            self._mobile_qr_rejected = True
            self._mobile_error = "mobile_identity_mismatch"
            return await self.async_step_mobile_result()
        self._mobile_qr_rejected = False
        if self._mobile_region() is None:
            self._mobile_error = "mobile_unknown_region"
            return await self.async_step_mobile_result()
        return await self.async_step_mobile_login()

    async def async_step_mobile_fetch(
        self, _user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Keep long-running cloud reads off the frontend request path."""
        flow = cast("TuyaBLEConfigFlow", self)
        task = self._mobile_task
        if task is None:
            return flow.async_abort(reason="unknown")
        done, _ = await asyncio.wait((task,), timeout=0)
        if done:
            return flow.async_show_progress_done(next_step_id="mobile_finish")
        return flow.async_show_progress(
            step_id="mobile_fetch", progress_action="fetch_mobile", progress_task=task
        )

    async def async_step_mobile_finish(
        self, _user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Keep entry identity stable while accepting a mobile result."""
        flow = cast("TuyaBLEConfigFlow", self)
        async with flow._setup_lock:  # pylint: disable=protected-access
            flow._abort_if_flow_removed()  # pylint: disable=protected-access
            pending, task = self._mobile_pending, self._mobile_task
            if pending is None or task is None or not task.done():
                return flow.async_abort(reason="unknown")
            self._mobile_task = None
            if task.cancelled():
                result = MobileResult(error="mobile_connection_failed")
            else:
                try:
                    result = task.result()
                except Exception:  # pylint: disable=broad-exception-caught
                    result = MobileResult(error="mobile_connection_failed")
            return await self._async_accept_mobile(result, pending)

    async def _async_accept_mobile(
        self, result: MobileResult, pending: UnverifiedSetup
    ) -> ConfigFlowResult:
        """Persist safe metadata on existing entries; stage keys until verification."""
        flow = cast("TuyaBLEConfigFlow", self)
        if result.snapshot is not None:
            self._mobile_snapshot = filter_mobile_snapshot(result.snapshot)
        if result.error or result.credentials is None:
            self._store_mobile_diagnostics()
            self._mobile_error = result.error or "mobile_invalid_keys"
            return await self.async_step_mobile_result()
        discovery, advertisement, _, _ = pending
        self._mobile_staged_credentials = result.credentials
        self._mobile_attempted = True
        self._mobile_keys_imported = True
        if flow.source == SOURCE_RECONFIGURE:
            entry = flow._get_reconfigure_entry()  # pylint: disable=protected-access
            if not await flow.hass.config_entries.async_unload(entry.entry_id):
                self._store_mobile_diagnostics()
                self._mobile_error = "v4_verification_failed"
                return await self.async_step_mobile_result()
            flow._reconfigure_unloaded = True  # pylint: disable=protected-access
            if flow._flow_removed:  # pylint: disable=protected-access
                await flow._async_restore_entry()  # pylint: disable=protected-access
                flow._abort_if_flow_removed()  # pylint: disable=protected-access
        flow._probe_discovery = discovery  # pylint: disable=protected-access
        flow._probe_advertisement = advertisement  # pylint: disable=protected-access
        flow._probe_task = flow.hass.async_create_task(  # pylint: disable=protected-access
            flow._async_verify_v4(discovery, result.credentials)  # pylint: disable=protected-access
        )
        flow._probe_task.add_done_callback(_consume_probe_result)  # pylint: disable=protected-access
        return await flow.async_step_probe()

    async def async_step_mobile_result(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """An explicit retry is required; never automatically resubmit a password."""
        flow = cast("TuyaBLEConfigFlow", self)
        if self._mobile_pending is None:
            return flow.async_abort(reason="unknown")
        if user_input is not None:
            if user_input.get("next_action") == "retry":
                if (
                    self._mobile_staged_credentials is not None
                    and flow.source == SOURCE_RECONFIGURE
                ):
                    return await flow.async_step_reconfigure({})
                return await self.async_step_mobile_login()
            if flow.source == SOURCE_RECONFIGURE:
                return await flow.async_step_reconfigure()
            flow._unverified_setup = self._mobile_pending  # pylint: disable=protected-access
            return await flow.async_step_unverified()
        return flow.async_show_form(
            step_id="mobile_result",
            data_schema=probatio.Schema({
                probatio.Required("next_action"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=["retry", "continue"], translation_key="mobile_result"
                    )
                )
            }),
            errors={"base": self._mobile_error},
        )
