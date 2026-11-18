"""Config flow for Tuya BLE integration."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import logging
import time
from typing import Any

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
)
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback
from homeassistant.data_entry_flow import AbortFlow
import probatio
from tuya_sharing import LoginControl

from . import _build_credentials_from_entry
from .cloud import HASSTuyaBLEDeviceManager
from .config_flow_options import TuyaBLEOptionsFlow
from .config_flow_qr import UNKNOWN_ERROR, _QRCodeLoginMixin
from .config_flow_scan import async_scan_device, bound_classic_advertisement
from .config_flow_state import PROBE_CLAIM_TTL, _consume_probe_result
from .config_flow_state import advertisement_hex as _advertisement_hex
from .config_flow_state import probe_state as _probe_state
from .config_flow_unverified import (
    UnverifiedSetup,
    _UnverifiedSetupMixin,
    unverified_error,
)
from .const import (
    CONF_ADVERTISED_UUID,
    CONF_CATEGORY,
    CONF_CLOUD_INFO,
    CONF_CLOUD_UUID,
    CONF_CONNECTION_PROFILE,
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_ENDPOINT,
    CONF_FUNCTIONS,
    CONF_LOCAL_KEY,
    CONF_LOCAL_SCHEMA,
    CONF_PRODUCT_ID,
    CONF_PRODUCT_MODEL,
    CONF_PRODUCT_NAME,
    CONF_SEC_KEY,
    CONF_STATUS_RANGE,
    CONF_TERMINAL_ID,
    CONF_TOKEN_INFO,
    CONF_USER_CODE,
    CONF_UUID,
    DOMAIN,
    TUYA_RESPONSE_CODE,
    TUYA_RESPONSE_MSG,
)
from .device_registry import async_setup_registry, get_mapped_dp_ids
from .tuya_ble import (
    MANUFACTURER_DATA_ID,
    SERVICE_UUID_V4,
    SERVICE_UUIDS,
    ConnectionProfile,
    ProbeOutcome,
    ProbeStage,
    TuyaBLEAdvertisementInfo,
    TuyaBLEDeviceCredentials,
    async_detect_v4_profile,
    async_probe_authenticated_status,
    decode_tuya_ble_advertisement,
    is_valid_sec_key,
)

PROBE_MAX_CANDIDATES = 4

_LOGGER = logging.getLogger(__name__)


def _probe_task_result(
    task: asyncio.Task[tuple[TuyaBLEDeviceCredentials | None, str | None, str]],
) -> tuple[TuyaBLEDeviceCredentials | None, str | None, str]:
    """Return a finished verification task's result, mapping failures to errors."""
    if task.cancelled():
        return None, None, "bluetooth_probe_failed"
    try:
        return task.result()
    except Exception:  # pylint: disable=broad-exception-caught
        _LOGGER.debug("Cloud credential verification failed")
        return None, None, "probe_setup_failed"


class TuyaBLEConfigFlow(
    ConfigFlow, _QRCodeLoginMixin, _UnverifiedSetupMixin, domain=DOMAIN
):
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
        self._probe_refresh_failed = False
        self._probe_claim_owner = object()
        self._setup_lock = asyncio.Lock()
        self._flow_removed = False
        self._probe_task: (
            asyncio.Task[tuple[TuyaBLEDeviceCredentials | None, str | None, str]] | None
        ) = None
        self._probe_discovery: BluetoothServiceInfoBleak | None = None
        self._probe_advertisement: TuyaBLEAdvertisementInfo | None = None
        self._probe_source_step = "device"
        self._probe_error: str | None = None
        self._verified_profile: ConnectionProfile | None = None
        self._unverified_credentials: tuple[TuyaBLEDeviceCredentials, str] | None = None
        self._unverified_setup: UnverifiedSetup | None = None
        self._reconfigure_unloaded = False
        self._qr_user_code: str = ""
        self._qr_code: str = ""
        self._qr_login_control = LoginControl()

    @callback
    def async_remove(self) -> None:
        """Cancel pending verification and release only this flow's device claims."""
        self._flow_removed = True
        if self._probe_task is not None and not self._probe_task.done():
            self._probe_task.cancel()
        if self._reconfigure_unloaded:
            self.hass.async_create_task(self._async_restore_entry())
        claims = _probe_state(self.hass)[1]
        for device_id, claim in tuple(claims.items()):
            if claim[0] is self._probe_claim_owner:
                claims.pop(device_id, None)
        super().async_remove()

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
        _LOGGER.debug(
            "Bluetooth discovery started: %s (name: %s, RSSI: %s)",
            discovery_info.address,
            discovery_info.name,
            discovery_info.rssi,
        )
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        complete_info = await self._async_resolve_discovery(discovery_info)
        if complete_info is None:
            return self.async_abort(reason="unsupported_device")
        self.discovery_info = complete_info
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
            _LOGGER.debug(
                "Login failed: %s (code %s)",
                placeholders[TUYA_RESPONSE_MSG],
                placeholders[TUYA_RESPONSE_CODE],
            )
        else:
            user_input = {}

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

    async def _async_scan_device(
        self, address: str
    ) -> BluetoothServiceInfoBleak | None:
        """Actively scan until Tuya service and manufacturer data are available."""
        return await async_scan_device(self.hass, address)

    async def _async_setup_address(
        self, address: str
    ) -> tuple[ConfigFlowResult | None, str | None]:
        """Scan, decode, and create an entry for one BLE address."""
        async with self._setup_lock:
            return await self._async_setup_address_locked(address)

    @callback
    def _abort_if_flow_removed(self) -> None:
        """Stop an executing setup step once Home Assistant removes its flow."""
        if self._flow_removed:
            raise AbortFlow("unknown")

    async def _async_setup_address_locked(
        self, address: str
    ) -> tuple[ConfigFlowResult | None, str | None]:
        """Keep the flow identity stable while setup submissions overlap."""
        self._abort_if_flow_removed()
        if self._probe_task is not None:
            return await self.async_step_probe(), None
        await self.async_set_unique_id(address, raise_on_progress=False)
        self._abort_if_flow_removed()
        self._abort_if_unique_id_configured()
        if self._manager is None:
            _LOGGER.warning(
                "%s: cannot set up device, cloud manager is not initialised", address
            )
            return self.async_abort(reason="unknown"), None

        discovery_info = await self._async_scan_device(address)
        self._abort_if_flow_removed()
        if discovery_info is None:
            _LOGGER.debug("%s: bluetooth_scan_failed", address)
            return None, "bluetooth_scan_failed"

        advertisement = decode_tuya_ble_advertisement(
            discovery_info.advertisement
        ) or bound_classic_advertisement(discovery_info)
        if advertisement is None:
            _LOGGER.debug(
                "%s: identity_not_decoded, %s",
                address,
                _advertisement_hex(discovery_info),
            )
            return None, "identity_not_decoded"

        if advertisement.uuid:
            credentials, refresh_failed = await self._async_lookup_credentials(
                self._manager, address, advertisement.uuid
            )
        else:
            _LOGGER.debug(
                "%s: bound classic identity not decoded, probing cloud candidates, %s",
                address,
                _advertisement_hex(discovery_info),
            )
            credentials, refresh_failed = (
                None,
                await self._async_refresh_cloud(self._manager, address),
            )
        self._abort_if_flow_removed()
        pairing_uuid: str | None = None
        self._verified_profile = None
        if credentials is None or advertisement.protocol_version == 4:
            self._get_device_info_error = credentials is None
            self._probe_refresh_failed = refresh_failed
            self._probe_discovery = discovery_info
            self._probe_advertisement = advertisement
            self._probe_source_step = (
                "discovered_device"
                if self.discovery_info is not None
                and self.discovery_info.address == address
                else "device"
            )
            self._probe_task = self.hass.async_create_task(
                self._async_probe_fallback(discovery_info, advertisement)
                if credentials is None
                else self._async_verify_v4(discovery_info, credentials)
            )
            self._probe_task.add_done_callback(_consume_probe_result)
            return self.async_show_progress(
                step_id="probe",
                progress_action="verify_device",
                progress_task=self._probe_task,
            ), None

        return self._create_device_entry(
            discovery_info, advertisement, credentials, pairing_uuid
        ), None

    async def _async_lookup_credentials(
        self, manager: HASSTuyaBLEDeviceManager, address: str, uuid: str
    ) -> tuple[TuyaBLEDeviceCredentials | None, bool]:
        """Look up a decoded identity, forcing one cache refresh on a miss."""
        try:
            credentials = await manager.get_device_credentials_by_uuid(
                uuid, force_update=self._get_device_info_error
            )
            self._abort_if_flow_removed()
            if credentials is None and not self._get_device_info_error:
                credentials = await manager.get_device_credentials_by_uuid(
                    uuid, force_update=True
                )
        except AbortFlow:
            raise
        except Exception:  # pylint: disable=broad-exception-caught
            # A cached candidate may still pass a genuine authenticated read.
            # Do not log SDK exceptions: they can contain cloud credentials.
            _LOGGER.warning("%s: Tuya cloud refresh failed", address)
            return None, True
        return credentials, False

    async def _async_refresh_cloud(
        self, manager: HASSTuyaBLEDeviceManager, address: str
    ) -> bool:
        """Refresh cloud candidates, returning True when the refresh failed."""
        try:
            await manager.async_refresh()
        except Exception:  # pylint: disable=broad-exception-caught
            # Do not log SDK exceptions: they can contain cloud credentials.
            _LOGGER.warning("%s: Tuya cloud refresh failed", address)
            return True
        return False

    def _create_device_entry(
        self,
        discovery_info: BluetoothServiceInfoBleak,
        advertisement: TuyaBLEAdvertisementInfo,
        credentials: TuyaBLEDeviceCredentials,
        pairing_uuid: str | None,
    ) -> ConfigFlowResult:
        """Persist both original identities and the verified BLE pairing UUID."""
        entry_data: dict[str, Any] = {
            CONF_ADDRESS: discovery_info.address,
            CONF_UUID: pairing_uuid or credentials.uuid,
            CONF_CLOUD_UUID: credentials.uuid,
            CONF_ADVERTISED_UUID: advertisement.uuid or None,
            CONF_LOCAL_KEY: credentials.local_key,
            CONF_DEVICE_ID: credentials.device_id,
            CONF_CATEGORY: credentials.category,
            CONF_PRODUCT_ID: credentials.product_id,
            CONF_DEVICE_NAME: credentials.device_name,
            CONF_PRODUCT_MODEL: credentials.product_model,
            CONF_PRODUCT_NAME: credentials.product_name,
            CONF_FUNCTIONS: credentials.functions,
            CONF_STATUS_RANGE: credentials.status_range,
            CONF_LOCAL_SCHEMA: credentials.local_schema,
            CONF_CLOUD_INFO: credentials.cloud_info,
        }
        if credentials.connection_profile is not None:
            entry_data[CONF_CONNECTION_PROFILE] = credentials.connection_profile.value
        if (
            credentials.sec_key is not None
            and advertisement.security_v2
            and is_valid_sec_key(credentials.sec_key)
        ):
            # Only a device advertising Security V2 needs, and receives, the key.
            # A malformed key is never stored: runtime could not load the entry.
            entry_data[CONF_SEC_KEY] = credentials.sec_key
        _LOGGER.debug(
            "%s: config entry created (category: %s, product_id: %s, device_name: %s)",
            discovery_info.address,
            credentials.category,
            credentials.product_id,
            credentials.device_name,
        )
        try:
            result = self.async_create_entry(
                title=credentials.device_name
                or discovery_info.name
                or discovery_info.address,
                data=entry_data,
            )
        except Exception:
            if pairing_uuid is not None:
                claims = _probe_state(self.hass)[1]
                claim = claims.get(credentials.device_id)
                if claim is not None and claim[0] is self._probe_claim_owner:
                    claims.pop(credentials.device_id, None)
            raise
        return result

    async def async_step_probe(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show progress while testing bound cloud candidates over Bluetooth."""
        task = self._probe_task
        if task is None:
            return self.async_abort(reason="unknown")
        if not task.done():
            return self.async_show_progress(
                step_id="probe", progress_action="verify_device", progress_task=task
            )
        return self.async_show_progress_done(next_step_id="probe_finish")

    async def async_step_probe_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Finish entry creation, or return to the originating device form."""
        async with self._setup_lock:
            return await self._async_finish_probe()

    async def _async_finish_probe(self) -> ConfigFlowResult:
        """Complete verification without permitting a concurrent identity change."""
        task = self._probe_task
        discovery = self._probe_discovery
        advertisement = self._probe_advertisement
        if (
            task is None
            or discovery is None
            or advertisement is None
            or not task.done()
        ):
            return self.async_abort(reason="unknown")
        self._probe_task = None
        self._probe_discovery = None
        self._probe_advertisement = None
        credentials, pairing_uuid, error = _probe_task_result(task)
        if credentials is not None and pairing_uuid is not None:
            result = self._complete_verified_probe(
                discovery, advertisement, credentials, pairing_uuid
            )
            if result is not None:
                return result
            error = "device_verification_expired"
        elif error == "device_not_registered" and self._probe_refresh_failed:
            error = "cloud_refresh_failed"
        return await self._async_probe_failed(discovery, advertisement, error)

    async def _async_probe_failed(
        self,
        discovery: BluetoothServiceInfoBleak,
        advertisement: TuyaBLEAdvertisementInfo,
        error: str,
    ) -> ConfigFlowResult:
        """Return to the originating step, or offer an unverified v4 setup."""
        unverified, self._unverified_credentials = self._unverified_credentials, None
        if error == "v4_verification_failed":
            self._get_device_info_error = True
        if unverified is not None:
            # Only a failed v4 verification leaves these; show why it failed.
            error = unverified[1]
        if self._probe_source_step == "reconfigure":
            self._probe_error = error
            await self._async_restore_entry()
            return await self._async_reconfigure_locked(None)
        if unverified is not None:
            self._unverified_setup = (discovery, advertisement, *unverified)
            return self._show_unverified_form(discovery, error)
        self._probe_error = error
        if self._probe_source_step == "discovered_device":
            return await self.async_step_discovered_device()
        return await self.async_step_device()

    def _complete_verified_probe(
        self,
        discovery: BluetoothServiceInfoBleak,
        advertisement: TuyaBLEAdvertisementInfo,
        credentials: TuyaBLEDeviceCredentials,
        pairing_uuid: str,
    ) -> ConfigFlowResult | None:
        """Persist a verified profile or complete the original ownership check."""
        profile = credentials.connection_profile
        if profile is not None and self.source == SOURCE_RECONFIGURE:
            self._reconfigure_unloaded = False
            return self.async_update_reload_and_abort(
                self._get_reconfigure_entry(),
                data_updates={CONF_CONNECTION_PROFILE: str(profile)},
                reload_even_if_entry_is_unchanged=True,
            )
        if profile is not None:
            self._abort_if_unique_id_configured()
        return self._create_verified_entry(
            discovery, advertisement, credentials, pairing_uuid
        )

    def _create_verified_entry(
        self,
        discovery: BluetoothServiceInfoBleak,
        advertisement: TuyaBLEAdvertisementInfo,
        credentials: TuyaBLEDeviceCredentials,
        pairing_uuid: str,
    ) -> ConfigFlowResult | None:
        """Atomically validate ownership and create an entry on the event loop."""
        # Claim validation and creation are atomic: no awaits or unrelated probe waits.
        claims = _probe_state(self.hass)[1]
        claim = claims.get(credentials.device_id)
        still_owned = (
            claim is not None
            and claim[0] is self._probe_claim_owner
            and claim[1] == discovery.address
            and claim[2] > time.monotonic()
            and not any(
                entry.data.get(CONF_DEVICE_ID) == credentials.device_id
                for entry in self.hass.config_entries.async_entries(DOMAIN)
            )
        )
        if still_owned:
            return self._create_device_entry(
                discovery, advertisement, credentials, pairing_uuid
            )
        if claim is not None and claim[0] is self._probe_claim_owner:
            claims.pop(credentials.device_id, None)
        return None

    async def _async_probe_fallback(
        self,
        discovery_info: BluetoothServiceInfoBleak,
        advertisement: TuyaBLEAdvertisementInfo,
    ) -> tuple[TuyaBLEDeviceCredentials | None, str | None, str]:
        """Use only an authenticated read to associate a cloud record with BLE."""
        if not advertisement.is_bound:
            return None, None, "device_not_registered"
        manager = self._manager
        if manager is None:
            return None, None, "device_not_registered"
        await async_setup_registry(self.hass)
        lock, claims = _probe_state(self.hass)
        async with lock:
            count, candidates = await manager.get_probe_candidates(
                advertisement.uuid, advertisement.product_id
            )
            for device_id, (_, _, expires) in tuple(claims.items()):
                if expires < time.monotonic():
                    claims.pop(device_id, None)
            if count > PROBE_MAX_CANDIDATES:
                return None, None, "too_many_candidates"
            probe_failed = False
            for credentials in candidates:
                self._abort_if_flow_removed()
                if credentials.device_id in claims:
                    continue
                outcome, pairing_uuid = await self._async_try_probe_cloud_candidate(
                    discovery_info, advertisement, credentials
                )
                self._abort_if_flow_removed()
                if outcome == ProbeOutcome.MATCHED and pairing_uuid is not None:
                    claims[credentials.device_id] = (
                        self._probe_claim_owner,
                        discovery_info.address,
                        time.monotonic() + PROBE_CLAIM_TTL,
                    )
                    return (
                        replace(credentials, connection_profile=self._verified_profile),
                        pairing_uuid,
                        "",
                    )
                if outcome in (ProbeOutcome.INCONCLUSIVE, ProbeOutcome.RETRYABLE):
                    probe_failed = True
            return (
                None,
                None,
                ("bluetooth_probe_failed" if probe_failed else "device_not_registered"),
            )

    async def _async_try_probe_cloud_candidate(
        self,
        discovery_info: BluetoothServiceInfoBleak,
        advertisement: TuyaBLEAdvertisementInfo,
        credentials: TuyaBLEDeviceCredentials,
    ) -> tuple[ProbeOutcome | None, str | None]:
        """Isolate candidate-specific failures without suppressing flow aborts."""
        try:
            return await self._async_probe_cloud_candidate(
                discovery_info, advertisement, credentials
            )
        except AbortFlow:
            raise
        except Exception:  # pylint: disable=broad-exception-caught
            _LOGGER.debug("Cloud credential candidate verification failed")
            return ProbeOutcome.INCONCLUSIVE, None

    async def _async_probe_cloud_candidate(
        self,
        discovery_info: BluetoothServiceInfoBleak,
        advertisement: TuyaBLEAdvertisementInfo,
        credentials: TuyaBLEDeviceCredentials,
    ) -> tuple[ProbeOutcome | None, str | None]:
        """Try each distinct pairing UUID, advancing after every probe failure."""
        expected = get_mapped_dp_ids(credentials.category, credentials.product_id)
        if not expected:
            return None, None
        failure = ProbeOutcome.REJECTED
        # An undecoded identity has no advertised UUID to try.
        for uuid in dict.fromkeys(filter(None, (advertisement.uuid, credentials.uuid))):
            if advertisement.protocol_version == 4:
                profile = await async_detect_v4_profile(
                    discovery_info.device,
                    discovery_info.advertisement,
                    replace(credentials, uuid=uuid),
                    expected,
                )
                if profile is not None:
                    self._verified_profile = profile
                    return ProbeOutcome.MATCHED, uuid
                failure = ProbeOutcome.INCONCLUSIVE
                continue
            outcome = await self._async_probe_uuid(
                discovery_info, credentials, uuid, expected
            )
            if outcome == ProbeOutcome.MATCHED:
                return outcome, uuid
            if outcome != ProbeOutcome.REJECTED:
                failure = outcome
        return failure, None

    async def _async_probe_uuid(
        self,
        discovery_info: BluetoothServiceInfoBleak,
        credentials: TuyaBLEDeviceCredentials,
        uuid: str,
        expected_dp_ids: frozenset[int],
    ) -> ProbeOutcome:
        """Retry a timeout or silent disconnect once, using a fresh probe."""
        for attempt in range(2):
            self._abort_if_flow_removed()
            try:
                outcome = await async_probe_authenticated_status(
                    discovery_info.device,
                    discovery_info.advertisement,
                    credentials,
                    uuid,
                    expected_dp_ids,
                )
            except TimeoutError, ConnectionError:
                outcome = ProbeOutcome.RETRYABLE
            except AbortFlow:
                raise
            except Exception:  # pylint: disable=broad-exception-caught
                _LOGGER.debug("Authenticated cloud credential probe failed")
                outcome = ProbeOutcome.INCONCLUSIVE
            self._abort_if_flow_removed()
            _LOGGER.debug(
                "%s: credential probe attempt %s returned %s",
                discovery_info.address,
                attempt + 1,
                outcome,
            )
            if outcome != ProbeOutcome.RETRYABLE:
                return outcome
        return ProbeOutcome.RETRYABLE

    async def _async_verify_v4(
        self,
        discovery: BluetoothServiceInfoBleak,
        credentials: TuyaBLEDeviceCredentials,
    ) -> tuple[TuyaBLEDeviceCredentials | None, str | None, str]:
        """Automatically verify a cloud-matched v4 device before saving its entry."""
        await async_setup_registry(self.hass)
        lock, claims = _probe_state(self.hass)
        async with lock:
            claim = claims.get(credentials.device_id)
            if (
                claim is not None
                and claim[0] is not self._probe_claim_owner
                and claim[2] > time.monotonic()
            ):
                return None, None, "device_verification_expired"
            stages: list[ProbeStage] = []
            profile = await async_detect_v4_profile(
                discovery.device,
                discovery.advertisement,
                credentials,
                get_mapped_dp_ids(credentials.category, credentials.product_id),
                stages=stages,
            )
            self._abort_if_flow_removed()
            if profile is not None:
                claims[credentials.device_id] = (
                    self._probe_claim_owner,
                    discovery.address,
                    time.monotonic() + PROBE_CLAIM_TTL,
                )
        self._abort_if_flow_removed()
        if profile is None:
            # Kept so the user may still add the device to collect diagnostics.
            self._unverified_credentials = (credentials, unverified_error(stages))
            return None, None, "v4_verification_failed"
        return replace(credentials, connection_profile=profile), credentials.uuid, ""

    async def _async_restore_entry(self) -> None:
        """Restore runtime ownership after failed or cancelled reconfiguration."""
        if self._reconfigure_unloaded:
            self._reconfigure_unloaded = False
            if self._probe_task is not None:
                await asyncio.gather(self._probe_task, return_exceptions=True)
            await self.hass.config_entries.async_reload(
                self._get_reconfigure_entry().entry_id
            )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Verify compatibility for an existing entry without changing entity IDs."""
        async with self._setup_lock:
            return await self._async_reconfigure_locked(user_input)

    async def _async_reconfigure_locked(
        self, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        """Keep the task check and all awaited setup stages under one flow lock."""
        self._abort_if_flow_removed()
        errors: dict[str, str] = {}
        if self._probe_error is not None:
            errors["base"], self._probe_error = self._probe_error, None
        if self._probe_task is not None:
            return await self.async_step_probe()
        if user_input is not None:
            entry = self._get_reconfigure_entry()
            await self.async_set_unique_id(entry.unique_id)
            self._abort_if_flow_removed()
            self._abort_if_unique_id_mismatch()
            discovery = await self._async_scan_device(entry.data[CONF_ADDRESS])
            self._abort_if_flow_removed()
            if discovery is None:
                errors["base"] = "bluetooth_scan_failed"
            elif (
                advertisement := decode_tuya_ble_advertisement(discovery.advertisement)
            ) is None:
                errors["base"] = "identity_not_decoded"
            elif advertisement.protocol_version != 4:
                return self.async_abort(reason="unsupported_device")
            elif not await self.hass.config_entries.async_unload(entry.entry_id):
                self._abort_if_flow_removed()
                errors["base"] = "v4_verification_failed"
            else:
                self._reconfigure_unloaded = True
                if self._flow_removed:
                    await self._async_restore_entry()
                    self._abort_if_flow_removed()
                self._probe_source_step = "reconfigure"
                self._probe_discovery = discovery
                self._probe_advertisement = advertisement
                self._probe_task = self.hass.async_create_task(
                    self._async_verify_v4(
                        discovery, _build_credentials_from_entry(entry)
                    )
                )
                self._probe_task.add_done_callback(_consume_probe_result)
                return await self.async_step_probe()
        return self.async_show_form(
            step_id="reconfigure", data_schema=probatio.Schema({}), errors=errors
        )

    async def async_step_discovered_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set up the device that initiated this Bluetooth discovery flow."""
        if self.discovery_info is None:
            return self.async_abort(reason="unknown")

        errors: dict[str, str] = {}
        if self._probe_error is not None:
            errors["base"], self._probe_error = self._probe_error, None
        if user_input is not None:
            result, error = await self._async_setup_address(self.discovery_info.address)
            if result is not None:
                return result
            if error is not None:
                errors["base"] = error

        return self.async_show_form(
            step_id="discovered_device",
            data_schema=probatio.Schema({}),
            errors=errors,
            description_placeholders={
                CONF_ADDRESS: self.discovery_info.address,
                "name": self.discovery_info.name or self.discovery_info.address,
            },
        )

    async def async_step_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the user step to pick discovered device."""
        errors: dict[str, str] = {}
        if self._probe_error is not None:
            errors["base"], self._probe_error = self._probe_error, None

        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            result, error = await self._async_setup_address(address)
            if result is not None:
                return result
            if error is not None:
                errors["base"] = error

        await self._async_refresh_discovered_devices()

        if not self._discovered_devices:
            _LOGGER.debug("No unconfigured Tuya BLE devices discovered")
            return self.async_abort(reason="no_unconfigured_devices")

        def_address = str(user_input.get(CONF_ADDRESS, "")) if user_input else ""
        if def_address not in self._discovered_devices:
            def_address = next(iter(self._discovered_devices))

        return self.async_show_form(
            step_id="device",
            data_schema=probatio.Schema(
                {
                    probatio.Required(
                        CONF_ADDRESS,
                        default=def_address,
                    ): probatio.In({
                        service_info.address: service_info.name or service_info.address
                        for service_info in self._discovered_devices.values()
                    }),
                },
            ),
            errors=errors,
        )

    async def _async_refresh_discovered_devices(self) -> None:
        """Collect discovered devices, actively completing partial v4 identities."""
        self._discovered_devices.clear()
        current_addresses = self._async_current_ids()
        candidates = [
            discovery
            for discovery in async_discovered_service_info(self.hass)
            if discovery.address not in current_addresses
            and discovery.service_data is not None
            and any(uuid in discovery.service_data for uuid in SERVICE_UUIDS)
        ]
        resolved, deferred = await self._async_resolve_candidates(candidates)
        for discovery in candidates:
            complete_info = resolved.get(discovery.address)
            if complete_info is None:
                continue
            discovery = complete_info
            self._discovered_devices[discovery.address] = discovery
            _LOGGER.debug(
                "Discovered Tuya BLE device %s (name: %s, total: %d)",
                discovery.address,
                discovery.name,
                len(self._discovered_devices),
            )
        # Keep deferred v4 candidates selectable after ready entries. Setup always
        # actively validates the selected address before requesting credentials.
        for discovery in candidates:
            if discovery.address in deferred:
                self._discovered_devices[discovery.address] = deferred[
                    discovery.address
                ]
        # Another flow can finish configuration while an active scan is awaiting.
        for address in self._async_current_ids():
            if address is not None:
                self._discovered_devices.pop(address, None)

    async def _async_resolve_candidates(
        self, candidates: list[BluetoothServiceInfoBleak]
    ) -> tuple[
        dict[str, BluetoothServiceInfoBleak | None],
        dict[str, BluetoothServiceInfoBleak],
    ]:
        """Resolve in parallel until eligible devices are ready for selection."""
        if not candidates:
            return {}, {}
        tasks = {
            asyncio.create_task(self._async_resolve_discovery(discovery)): discovery
            for discovery in candidates
        }
        pending = set(tasks)
        resolved: dict[str, BluetoothServiceInfoBleak | None] = {}
        try:
            while pending:
                done, pending = await asyncio.wait(
                    pending, return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    resolved[tasks[task].address] = task.result()
                configured = self._async_current_ids()
                if any(
                    discovery is not None and discovery.address not in configured
                    for discovery in resolved.values()
                ):
                    break
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        deferred = {
            discovery.address: discovery
            for task, discovery in tasks.items()
            if task.cancelled()
        }
        return resolved, deferred

    async def _async_resolve_discovery(
        self, discovery: BluetoothServiceInfoBleak
    ) -> BluetoothServiceInfoBleak | None:
        """Reject unsupported records, scanning incomplete FD50 candidates first."""
        if SERVICE_UUID_V4 not in (discovery.service_data or {}):
            return discovery
        if decode_tuya_ble_advertisement(discovery.advertisement) is not None:
            return discovery
        service = discovery.service_data[SERVICE_UUID_V4]
        if not service or service[0] >> 4 != 4:
            return None
        manufacturer = (discovery.manufacturer_data or {}).get(
            MANUFACTURER_DATA_ID, b""
        )
        if (
            len(service) >= 4
            and len(service) == 4 + service[3]
            and len(manufacturer) == 20
        ):
            # A complete but undecodable record is not an incomplete scan response.
            return None
        return await self._async_scan_device(discovery.address)

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
        if self.discovery_info is not None:
            _LOGGER.debug(
                "Login complete, setting up discovered device %s directly",
                self.discovery_info.address,
            )
            return await self.async_step_discovered_device(user_input={})
        _LOGGER.debug("Login complete, selecting a device to set up")
        return await self.async_step_device()

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> TuyaBLEOptionsFlow:
        """Get the options flow for this handler."""
        return TuyaBLEOptionsFlow(config_entry)
