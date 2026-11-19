"""Reconfigure verification without changing an existing device's identity."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_ADDRESS
import probatio

from . import _build_credentials_from_entry
from .config_flow_state import _consume_probe_result
from .tuya_ble import decode_tuya_ble_advertisement

if TYPE_CHECKING:
    from .config_flow import TuyaBLEConfigFlow


class _ReconfigureMixin:
    """Keep the form-stage scan/unload lifecycle under the setup lock."""

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Verify compatibility for an existing entry without changing entity IDs."""
        flow = cast("TuyaBLEConfigFlow", self)
        async with flow._setup_lock:  # pylint: disable=protected-access
            return await self._async_reconfigure_locked(user_input)

    async def _async_reconfigure_locked(
        self, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        """Keep the task check and all awaited setup stages under one flow lock."""
        flow = cast("TuyaBLEConfigFlow", self)
        flow._abort_if_flow_removed()  # pylint: disable=protected-access
        errors: dict[str, str] = {}
        if flow._probe_error is not None:  # pylint: disable=protected-access
            errors["base"], flow._probe_error = flow._probe_error, None  # pylint: disable=protected-access
        if flow._probe_task is not None:  # pylint: disable=protected-access
            return await flow.async_step_probe()
        if user_input is not None:
            entry = flow._get_reconfigure_entry()  # pylint: disable=protected-access
            await flow.async_set_unique_id(entry.unique_id)
            flow._abort_if_flow_removed()  # pylint: disable=protected-access
            flow._abort_if_unique_id_mismatch()  # pylint: disable=protected-access
            discovery = await flow._async_scan_device(entry.data[CONF_ADDRESS])  # pylint: disable=protected-access
            flow._abort_if_flow_removed()  # pylint: disable=protected-access
            if discovery is None:
                errors["base"] = "bluetooth_scan_failed"
            elif (
                advertisement := decode_tuya_ble_advertisement(discovery.advertisement)
            ) is None:
                errors["base"] = "identity_not_decoded"
            elif advertisement.protocol_version != 4:
                return flow.async_abort(reason="unsupported_device")
            elif not await flow.hass.config_entries.async_unload(entry.entry_id):
                flow._abort_if_flow_removed()  # pylint: disable=protected-access
                errors["base"] = "v4_verification_failed"
            else:
                flow._reconfigure_unloaded = True  # pylint: disable=protected-access
                if flow._flow_removed:  # pylint: disable=protected-access
                    await flow._async_restore_entry()  # pylint: disable=protected-access
                    flow._abort_if_flow_removed()  # pylint: disable=protected-access
                flow._probe_source_step = "reconfigure"  # pylint: disable=protected-access
                flow._probe_discovery = discovery  # pylint: disable=protected-access
                flow._probe_advertisement = advertisement  # pylint: disable=protected-access
                probe = flow._async_verify_v4  # pylint: disable=protected-access
                flow._probe_task = flow.hass.async_create_task(  # pylint: disable=protected-access
                    probe(discovery, _build_credentials_from_entry(entry))
                )
                flow._probe_task.add_done_callback(_consume_probe_result)  # pylint: disable=protected-access
                return await flow.async_step_probe()
        return flow.async_show_form(
            step_id="reconfigure", data_schema=probatio.Schema({}), errors=errors
        )
