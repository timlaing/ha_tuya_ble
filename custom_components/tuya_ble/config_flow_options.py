"""Options flow for Tuya BLE: QR-code re-authentication."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlowResult,
    OptionsFlowWithConfigEntry,
)
import probatio
from tuya_sharing import LoginControl

from .config_flow_qr import UNKNOWN_ERROR, _QRCodeLoginMixin
from .const import (
    CONF_ENDPOINT,
    CONF_TERMINAL_ID,
    CONF_TOKEN_INFO,
    CONF_USER_CODE,
    TUYA_RESPONSE_CODE,
    TUYA_RESPONSE_MSG,
)

_LOGGER = logging.getLogger(__name__)


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
        """Delegate to the user step for re-authentication."""
        return await self.async_step_user(user_input)

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
