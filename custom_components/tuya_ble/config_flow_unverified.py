"""Opt-in setup of protocol-4 devices whose Bluetooth verification failed."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import replace
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
import probatio

from .const import CONF_DEVICE_ID, DOMAIN
from .tuya_ble import ProbeStage, TuyaBLEAdvertisementInfo, TuyaBLEDeviceCredentials

_LOGGER = logging.getLogger(__name__)

# Discovery, advertisement, cloud credentials and the error key to show.
type UnverifiedSetup = tuple[
    BluetoothServiceInfoBleak, TuyaBLEAdvertisementInfo, TuyaBLEDeviceCredentials, str
]

GENERIC_ERROR = "v4_verification_failed"

# The plain-language error for the furthest stage any verification attempt
# reached; earlier attempts' failures are less informative.
_STAGE_ERRORS: dict[ProbeStage, str] = {
    ProbeStage.INELIGIBLE: "v4_failed_unsupported_model",
    ProbeStage.CREDENTIALS: "v4_failed_credentials",
    ProbeStage.CONNECT: "v4_failed_connect",
    ProbeStage.NOTIFY: "v4_failed_connect",
    ProbeStage.DEVICE_INFO: "v4_failed_no_reply",
    ProbeStage.MISSING_SEC_KEY: "v4_failed_missing_key",
    ProbeStage.UNBOUND: "v4_failed_not_added",
    ProbeStage.UNSUPPORTED: "v4_failed_unsupported_version",
    # A PAIR timeout or drop is not a refusal of the cloud login details.
    ProbeStage.PAIR: "v4_failed_no_reply",
    ProbeStage.PAIR_REJECTED: "v4_failed_login",
    ProbeStage.STATUS: "v4_failed_no_data",
    ProbeStage.DATAPOINTS: "v4_failed_no_data",
}
_STAGE_ORDER = tuple(ProbeStage)


def unverified_error(stages: list[ProbeStage]) -> str:
    """Return the error key explaining why verification failed."""
    if not stages:
        return GENERIC_ERROR
    return _STAGE_ERRORS[max(stages, key=_STAGE_ORDER.index)]


class _UnverifiedSetupMixin:
    """Offer to add a device without a verified profile to collect diagnostics."""

    hass: HomeAssistant
    _setup_lock: asyncio.Lock
    _unverified_setup: UnverifiedSetup | None

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

        def _abort_if_unique_id_configured(self) -> None:
            """Provided by ConfigFlow."""

        def _abort_if_flow_removed(self) -> None:
            """Provided by the config flow."""

        def _create_device_entry(
            self,
            discovery_info: BluetoothServiceInfoBleak,
            advertisement: TuyaBLEAdvertisementInfo,
            credentials: TuyaBLEDeviceCredentials,
            pairing_uuid: str | None,
            unverified: bool = False,
        ) -> ConfigFlowResult:
            """Provided by the config flow."""

    def _show_unverified_form(
        self, discovery: BluetoothServiceInfoBleak, error: str
    ) -> ConfigFlowResult:
        """Show why verification failed; submitting adds the device anyway."""
        return self.async_show_form(
            step_id="unverified",
            data_schema=probatio.Schema({}),
            errors={"base": error},
            description_placeholders={
                CONF_ADDRESS: discovery.address,
                "name": discovery.name or discovery.address,
            },
        )

    async def async_step_unverified(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a v4 device whose verification failed once the user submits.

        An unverified entry has no saved connection profile, so runtime uses the
        default handshake. It exists so diagnostics can be collected from
        firmware this integration cannot yet verify; cancelling the flow adds
        nothing.
        """
        pending = self._unverified_setup
        if pending is None:
            return self.async_abort(reason="unknown")
        discovery, advertisement, credentials, error = pending
        if user_input is None:
            return self._show_unverified_form(discovery, error)
        async with self._setup_lock:
            self._abort_if_flow_removed()
            self._unverified_setup = None
            self._abort_if_unique_id_configured()
            if any(
                entry.data.get(CONF_DEVICE_ID) == credentials.device_id
                for entry in self.hass.config_entries.async_entries(DOMAIN)
            ):
                return self.async_abort(reason="already_configured")
            _LOGGER.warning(
                "%s: adding device without a verified Bluetooth connection",
                discovery.address,
            )
            # Keep the cloud credentials, including sec_key: runtime still
            # attempts the link and diagnostics need them to explain failures.
            return self._create_device_entry(
                discovery,
                advertisement,
                replace(credentials, connection_profile=None),
                None,
                unverified=True,
            )
