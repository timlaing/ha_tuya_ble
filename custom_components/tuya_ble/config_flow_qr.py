"""Shared QR login steps for configuration and options flows."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.helpers import selector
import probatio
from tuya_sharing import LoginControl

from .const import (
    TUYA_CLIENT_ID,
    TUYA_RESPONSE_CODE,
    TUYA_RESPONSE_MSG,
    TUYA_RESPONSE_QR_CODE,
    TUYA_RESPONSE_RESULT,
    TUYA_RESPONSE_SUCCESS,
    TUYA_SCHEMA,
)

UNKNOWN_ERROR = "Unknown error"
_LOGGER = logging.getLogger(__name__)


class _QRCodeLoginMixin:
    """Shared QR code login logic for config and options flows."""

    _qr_login_control: LoginControl
    _qr_user_code: str
    _qr_code: str
    hass: HomeAssistant

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
        """Proxy to the actual async_show_form method."""
        raise NotImplementedError

    async def _async_qr_login_store_and_advance(
        self, login_info: dict[str, Any]
    ) -> ConfigFlowResult:
        """Require subclasses to persist login data and choose the next step."""
        raise NotImplementedError

    def _qr_code_form(
        self,
        step_id: str,
        errors: dict[str, str] | None = None,
        description_placeholders: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Show the QR code form."""
        return self.async_show_form(
            step_id=step_id,
            data_schema=probatio.Schema({
                probatio.Optional("QR"): selector.QrCodeSelector(
                    config=selector.QrCodeSelectorConfig(
                        data=f"tuyaSmart--qrLogin?token={self._qr_code}",
                        scale=5,
                        error_correction_level=selector.QrErrorCorrectionLevel.QUARTILE,
                    )
                )
            }),
            errors=errors,
            description_placeholders=description_placeholders,
        )

    async def _async_fetch_qr_code(self, user_code: str) -> tuple[bool, dict[str, Any]]:
        """Request a QR code token from the Tuya cloud for the given user code."""
        response = await self.hass.async_add_executor_job(
            self._qr_login_control.qr_code, TUYA_CLIENT_ID, TUYA_SCHEMA, user_code
        )
        if success := response.get(TUYA_RESPONSE_SUCCESS, False):
            self._qr_user_code = user_code
            self._qr_code = response[TUYA_RESPONSE_RESULT][TUYA_RESPONSE_QR_CODE]
            _LOGGER.debug("QR code issued")
        else:
            _LOGGER.debug(
                "QR code request rejected: %s (code %s)",
                response.get(TUYA_RESPONSE_MSG, UNKNOWN_ERROR),
                response.get(TUYA_RESPONSE_CODE, 0),
            )
        return success, response

    async def async_step_qr(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show QR code for scanning."""
        if user_input is None:
            return self._qr_code_form("qr")
        return await self.async_step_scan()

    async def async_step_scan(
        self, _user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Wait for QR code scan and complete login."""
        ret, info = await self.hass.async_add_executor_job(
            self._qr_login_control.login_result,
            self._qr_code,
            TUYA_CLIENT_ID,
            self._qr_user_code,
        )
        if not ret:
            _LOGGER.debug(
                "QR code login not completed, re-issuing QR code: %s (code %s)",
                info.get(TUYA_RESPONSE_MSG, UNKNOWN_ERROR),
                info.get(TUYA_RESPONSE_CODE, 0),
            )
            await self._async_fetch_qr_code(self._qr_user_code)
            return self._qr_code_form(
                "qr",
                errors={"base": "login_error"},
                description_placeholders={
                    TUYA_RESPONSE_MSG: info.get(TUYA_RESPONSE_MSG, UNKNOWN_ERROR),
                    TUYA_RESPONSE_CODE: info.get(TUYA_RESPONSE_CODE, 0),
                },
            )
        return await self._async_qr_login_store_and_advance(info)
