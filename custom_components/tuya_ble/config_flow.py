"""Config flow for Tuya BLE integration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithConfigEntry,
)
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector
from tuya_sharing import LoginControl
import voluptuous as vol

from .cloud import HASSTuyaBLEDeviceManager
from .const import (
    CONF_CATEGORY,
    CONF_DEVICE_NAME,
    CONF_ENDPOINT,
    CONF_FUNCTIONS,
    CONF_LOCAL_KEY,
    CONF_PRODUCT_ID,
    CONF_PRODUCT_MODEL,
    CONF_PRODUCT_NAME,
    CONF_STATUS_RANGE,
    CONF_TERMINAL_ID,
    CONF_TOKEN_INFO,
    CONF_USER_CODE,
    CONF_UUID,
    DOMAIN,
    TUYA_CLIENT_ID,
    TUYA_RESPONSE_CODE,
    TUYA_RESPONSE_MSG,
    TUYA_RESPONSE_QR_CODE,
    TUYA_RESPONSE_RESULT,
    TUYA_RESPONSE_SUCCESS,
    TUYA_SCHEMA,
)
from .tuya_ble import SERVICE_UUID, decode_tuya_ble_advertisement

UNKNOWN_ERROR = "Unknown error"


class _QRCodeLoginMixin:
    """Shared QR code login logic for config and options flows."""

    # Subclasses must set these before calling mixin methods.
    _qr_login_control: LoginControl
    _qr_user_code: str
    _qr_code: str
    hass: HomeAssistant

    # Proxy to the actual async_show_form method. Subclasses must implement this.
    def async_show_form(
        self,
        *,
        step_id: str | None = None,
        data_schema: vol.Schema | None = None,
        errors: dict[str, str] | None = None,
        description_placeholders: Mapping[str, str] | None = None,
        last_step: bool | None = None,
        preview: str | None = None,
    ) -> ConfigFlowResult:
        """Proxy to the actual async_show_form method."""
        raise NotImplementedError

    # Subclass hook: store login data and return next step.
    async def _async_qr_login_store_and_advance(
        self, login_info: dict[str, Any]
    ) -> ConfigFlowResult:
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
            data_schema=vol.Schema({
                vol.Optional("QR"): selector.QrCodeSelector(
                    config=selector.QrCodeSelectorConfig(
                        data=f"tuyaSmart--qrLogin?token={self._qr_code}",
                        scale=5,
                        error_correction_level=(
                            selector.QrErrorCorrectionLevel.QUARTILE
                        ),
                    )
                )
            }),
            errors=errors,
            description_placeholders=description_placeholders,
        )

    async def _async_fetch_qr_code(self, user_code: str) -> tuple[bool, dict[str, Any]]:
        """Request a QR code token from the Tuya cloud for the given user code."""
        response = await self.hass.async_add_executor_job(
            self._qr_login_control.qr_code,
            TUYA_CLIENT_ID,
            TUYA_SCHEMA,
            user_code,
        )
        if success := response.get(TUYA_RESPONSE_SUCCESS, False):
            self._qr_user_code = user_code
            self._qr_code = response[TUYA_RESPONSE_RESULT][TUYA_RESPONSE_QR_CODE]
        return success, response

    async def async_step_qr(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show QR code for scanning."""
        if user_input is None:
            return self._qr_code_form("qr")
        return await self.async_step_scan()

    async def async_step_scan(
        self,
        user_input: dict[str, Any] | None = None,  # noqa: S1172
    ) -> ConfigFlowResult:
        """Wait for QR code scan and complete login."""
        hass = self.hass
        ret, info = await hass.async_add_executor_job(
            self._qr_login_control.login_result,
            self._qr_code,
            TUYA_CLIENT_ID,
            self._qr_user_code,
        )
        if not ret:
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


class TuyaBLEConfigFlow(ConfigFlow, _QRCodeLoginMixin, domain=DOMAIN):
    """Handle a config flow for Tuya BLE."""

    VERSION = 1

    def __init__(self) -> None:
        """Set up mutable state for the QR-code login flow."""
        super().__init__()
        self.discovery_info: BluetoothServiceInfoBleak | None = None
        self._discovered_devices: dict[str, BluetoothServiceInfoBleak] = {}
        self._data: dict[str, Any] = {}
        self._manager: HASSTuyaBLEDeviceManager | None = None
        self._get_device_info_error = False
        self._qr_user_code: str = ""
        self._qr_code: str = ""
        self._qr_login_control = LoginControl()

    def is_matching(self, other_flow: ConfigFlow) -> bool:
        """Return True if other_flow is matching this flow."""
        return (
            isinstance(other_flow, TuyaBLEConfigFlow)
            and self.discovery_info is not None
            and other_flow.discovery_info is not None
            and self.discovery_info.address == other_flow.discovery_info.address
        )

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle the bluetooth discovery step."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self.discovery_info = discovery_info
        return await self.async_step_user()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the user step - ask for user code."""
        errors: dict[str, str] = {}
        placeholders: dict[str, Any] = {}

        if user_input is not None:
            success, response = await self._async_fetch_qr_code(
                user_input[CONF_USER_CODE]
            )
            if success:
                self._data[CONF_USER_CODE] = user_input[CONF_USER_CODE]
                return await self.async_step_qr()

            errors["base"] = "login_error"
            placeholders = {
                TUYA_RESPONSE_MSG: response.get(TUYA_RESPONSE_MSG, UNKNOWN_ERROR),
                TUYA_RESPONSE_CODE: response.get(TUYA_RESPONSE_CODE, "0"),
            }
        else:
            user_input = {}

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(
                    CONF_USER_CODE, default=user_input.get(CONF_USER_CODE, "")
                ): str,
            }),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the user step to pick discovered device."""
        errors: dict[str, str] = {}

        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            discovery_info = self._discovered_devices[address]
            await self.async_set_unique_id(
                discovery_info.address, raise_on_progress=False
            )
            self._abort_if_unique_id_configured()
            if self._manager is None:
                return self.async_abort(reason="unknown")
            advertisement = decode_tuya_ble_advertisement(
                discovery_info.service_data,
                discovery_info.manufacturer_data,
            )
            credentials = await self._manager.get_device_credentials(
                discovery_info.address,
                self._get_device_info_error,
                True,
                uuid=advertisement.uuid if advertisement else None,
                product_id=advertisement.product_id if advertisement else None,
            )
            if credentials is None:
                self._get_device_info_error = True
                errors["base"] = "device_not_registered"
            else:
                entry_data: dict[str, Any] = {
                    CONF_ADDRESS: discovery_info.address,
                    CONF_UUID: credentials.uuid,
                    CONF_LOCAL_KEY: credentials.local_key,
                    "device_id": credentials.device_id,
                    CONF_CATEGORY: credentials.category,
                    CONF_PRODUCT_ID: credentials.product_id,
                    CONF_DEVICE_NAME: credentials.device_name,
                    CONF_PRODUCT_MODEL: credentials.product_model,
                    CONF_PRODUCT_NAME: credentials.product_name,
                    CONF_FUNCTIONS: credentials.functions,
                    CONF_STATUS_RANGE: credentials.status_range,
                }
                return self.async_create_entry(
                    title=discovery_info.name or discovery_info.address,
                    data=entry_data,
                )

        if discovery := self.discovery_info:
            self._discovered_devices[discovery.address] = discovery
        else:
            self._refresh_discovered_devices()

        if not self._discovered_devices:
            return self.async_abort(reason="no_unconfigured_devices")

        def_address: str
        if user_input:
            def_address = str(user_input.get(CONF_ADDRESS, ""))
        else:
            def_address = next(iter(self._discovered_devices))

        return self.async_show_form(
            step_id="device",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_ADDRESS,
                        default=def_address,
                    ): vol.In({
                        service_info.address: service_info.name or service_info.address
                        for service_info in self._discovered_devices.values()
                    }),
                },
            ),
            errors=errors,
        )

    @callback
    def _refresh_discovered_devices(self) -> None:
        """Scan for newly discovered Tuya BLE devices."""
        current_addresses = self._async_current_ids()
        for discovery in async_discovered_service_info(self.hass):
            if (
                discovery.address in current_addresses
                or discovery.address in self._discovered_devices
                or discovery.service_data is None
                or SERVICE_UUID not in discovery.service_data
            ):
                continue
            self._discovered_devices[discovery.address] = discovery

    async def _async_qr_login_store_and_advance(
        self, login_info: dict[str, Any]
    ) -> ConfigFlowResult:
        """Store login data and initialise the device manager."""
        self._data[CONF_TOKEN_INFO] = {
            "t": login_info["t"],
            "uid": login_info["uid"],
            "expire_time": login_info["expire_time"],
            "access_token": login_info["access_token"],
            "refresh_token": login_info["refresh_token"],
        }
        self._data[CONF_TERMINAL_ID] = login_info[CONF_TERMINAL_ID]
        self._data[CONF_ENDPOINT] = login_info[CONF_ENDPOINT]

        self._manager = HASSTuyaBLEDeviceManager(self.hass, self._data)
        await self._manager.initialize()

        return await self.async_step_device()

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> TuyaBLEOptionsFlow:
        """Get the options flow for this handler."""
        return TuyaBLEOptionsFlow(config_entry)


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
        else:
            user_input = {}
            user_input[CONF_USER_CODE] = self.config_entry.options.get(
                CONF_USER_CODE, ""
            )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(
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
