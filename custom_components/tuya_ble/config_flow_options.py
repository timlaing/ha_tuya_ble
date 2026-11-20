"""Options flow for Tuya BLE: QR-code re-authentication and key replacement."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components import bluetooth
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlowResult,
    OptionsFlowWithConfigEntry,
)
from homeassistant.const import CONF_ADDRESS
import probatio
from tuya_sharing import LoginControl

from .config_flow_manual import PASSWORD_SELECTOR, validate_key_pair
from .config_flow_qr import UNKNOWN_ERROR, _QRCodeLoginMixin
from .const import (
    CONF_ENDPOINT,
    CONF_LOCAL_KEY,
    CONF_SEC_KEY,
    CONF_SECURITY_MODE,
    CONF_TERMINAL_ID,
    CONF_TOKEN_INFO,
    CONF_USER_CODE,
    TUYA_RESPONSE_CODE,
    TUYA_RESPONSE_MSG,
)
from .tuya_ble import (
    SecurityMode,
    TuyaBLEAdvertisementInfo,
    TuyaBLEClassicRecord,
    TuyaBLEIdentityRecord,
    decode_classic_record,
    decode_fd50_identity_record,
    decode_tuya_ble_advertisement,
    security_modes,
)

_LOGGER = logging.getLogger(__name__)

type SecurityRecord = (
    TuyaBLEAdvertisementInfo | TuyaBLEIdentityRecord | TuyaBLEClassicRecord
)


class TuyaBLEOptionsFlow(OptionsFlowWithConfigEntry, _QRCodeLoginMixin):
    """Handle a Tuya BLE options flow."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Set up mutable state for the re-authentication QR-code flow."""
        super().__init__(config_entry)
        self._qr_user_code: str = ""
        self._qr_code: str = ""
        self._qr_login_control = LoginControl()

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer QR-code re-authentication or replacing the device keys."""
        return self.async_show_menu(
            step_id="init", menu_options=["user", "credentials"]
        )

    async def async_step_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Replace the local key and security key together, as one pair.

        Nothing is written until the whole pair is valid, and the device
        identity (device ID, UUID and product) is never changed, so entity
        unique IDs stay the same. Stored keys are never shown in the form; an
        empty security key removes the stored one.
        """
        errors: dict[str, str] = {}
        if user_input is not None:
            keys, errors = validate_key_pair(user_input)
            if keys is not None:
                local_key, sec_key = keys
                record = self._security_record()
                if (
                    sec_key is not None
                    and record is not None
                    and not record.security_v2
                ):
                    errors[CONF_SEC_KEY] = "sec_key_not_used"
                else:
                    return self._replace_key_pair(local_key, sec_key, record)
        return self.async_show_form(
            step_id="credentials",
            data_schema=probatio.Schema({
                probatio.Required(CONF_LOCAL_KEY): PASSWORD_SELECTOR,
                probatio.Optional(CONF_SEC_KEY): PASSWORD_SELECTOR,
            }),
            errors=errors,
        )

    def _security_record(self) -> SecurityRecord | None:
        """Return the Security V2 state the device advertises, if known.

        A full identity, an FD50 identity record alone (the encrypted identity
        may only arrive in a scan response) or a classic record each say
        whether the device supports and has switched on Security V2. Only an
        advertisement carrying none of them leaves the choice to the user.
        """
        info = bluetooth.async_last_service_info(
            self.hass, self.config_entry.data[CONF_ADDRESS], connectable=True
        )
        if info is None:
            return None
        for decode in (
            decode_tuya_ble_advertisement,
            decode_fd50_identity_record,
            decode_classic_record,
        ):
            if (record := decode(info.advertisement)) is not None:
                return record
        return None

    def _replace_key_pair(
        self, local_key: str, sec_key: str | None, record: SecurityRecord | None
    ) -> ConfigFlowResult:
        """Write the new pair in one entry update, then reload the device.

        Without a security key only the classic login exists, so the saved
        login is dropped. A saved login the advertisement no longer allows,
        such as a classic login on firmware that has switched Security V2 on,
        is replaced by the first allowed one, as verification would choose.
        """
        entry = self.config_entry
        data = {
            key: value
            for key, value in entry.data.items()
            if key not in (CONF_SEC_KEY, CONF_SECURITY_MODE)
        }
        data[CONF_LOCAL_KEY] = local_key
        if sec_key is not None:
            data[CONF_SEC_KEY] = sec_key
            if (mode := entry.data.get(CONF_SECURITY_MODE)) is not None:
                data[CONF_SECURITY_MODE] = self._allowed_login(mode, sec_key, record)
        self.hass.config_entries.async_update_entry(entry, data=data)
        self.hass.config_entries.async_schedule_reload(entry.entry_id)
        _LOGGER.debug(
            "%s: device keys replaced (security key %s)",
            entry.data.get(CONF_ADDRESS),
            "stored" if sec_key is not None else "not stored",
        )
        return self.async_create_entry(title="", data=dict(entry.options))

    @staticmethod
    def _allowed_login(mode: str, sec_key: str, record: SecurityRecord | None) -> str:
        """Keep the saved login unless the advertisement now rules it out."""
        if record is None:
            return mode
        allowed = security_modes(
            record.security_v2, record.security_v2_enabled, sec_key
        )
        return mode if SecurityMode(mode) in allowed else allowed[0].value

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle re-authentication via QR code."""
        errors: dict[str, str] = {}
        placeholders: dict[str, Any] = {}

        if user_input is not None:
            success, response = await self._async_fetch_qr_code(
                user_input[CONF_USER_CODE]
            )
            if success:
                return await self.async_step_qr()

            errors["base"] = "login_error"
            placeholders = {
                TUYA_RESPONSE_MSG: response.get(TUYA_RESPONSE_MSG, UNKNOWN_ERROR),
                TUYA_RESPONSE_CODE: response.get(TUYA_RESPONSE_CODE, "0"),
            }
            _LOGGER.debug(
                "Re-authentication rejected (code %s): %s",
                placeholders[TUYA_RESPONSE_CODE],
                placeholders[TUYA_RESPONSE_MSG],
            )
        else:
            user_input = {}
            user_input[CONF_USER_CODE] = self.config_entry.options.get(
                CONF_USER_CODE, ""
            )

        return self.async_show_form(
            step_id="user",
            data_schema=probatio.Schema({
                probatio.Required(
                    CONF_USER_CODE, default=user_input.get(CONF_USER_CODE, "")
                ): str,
            }),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def _async_qr_login_store_and_advance(
        self, login_info: dict[str, Any]
    ) -> ConfigFlowResult:
        """Store login data as an options update."""
        new_data = dict(self.config_entry.options)
        new_data[CONF_USER_CODE] = self._qr_user_code
        new_data[CONF_TOKEN_INFO] = {
            "t": login_info["t"],
            "uid": login_info["uid"],
            "expire_time": login_info["expire_time"],
            "access_token": login_info["access_token"],
            "refresh_token": login_info["refresh_token"],
        }
        new_data[CONF_TERMINAL_ID] = login_info[CONF_TERMINAL_ID]
        new_data[CONF_ENDPOINT] = login_info[CONF_ENDPOINT]

        return self.async_create_entry(
            title="",
            data=new_data,
        )
