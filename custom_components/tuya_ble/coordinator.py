"""Data coordinator for Tuya BLE devices."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.components import bluetooth
from homeassistant.const import CONF_ADDRESS, CONF_DEVICE_ID
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
)

from .const import (
    DOMAIN,
    FINGERBOT_BUTTON_EVENT,
    SET_DISCONNECTED_DELAY,
)
from .device_registry import get_mapped_dp_ids, get_registry
from .entity import get_device_product_info
from .tuya_ble import (
    TuyaBLEDataPoint,
    TuyaBLEDevice,
)

_LOGGER = logging.getLogger(__name__)


class TuyaBLECoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Data coordinator for receiving Tuya BLE updates."""

    def __init__(self, hass: HomeAssistant, device: TuyaBLEDevice) -> None:
        """Register connect, update, and disconnect callbacks on the BLE device."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
        )
        self.device = device
        self._disconnected: bool = True
        # On-demand availability: the link is up, the device was reached since
        # setup, and Home Assistant still receives its advertisements.
        self._linked = False
        self._reached = False
        self._present = False
        self._poll_interval = 0
        self._unsub_disconnect: CALLBACK_TYPE | None = None
        self._status_task: asyncio.Task[None] | None = None
        self._dp_classification: tuple[bool, frozenset[int]] | None = None
        self._reported_unmapped: dict[int, tuple[str, bytes]] = {}
        device.register_connected_callback(self._async_handle_connect)
        device.register_callback(self._async_handle_update)
        device.register_disconnected_callback(self._async_handle_disconnect)

    async def async_shutdown(self) -> None:
        """Cancel any pending disconnect timer or status request on teardown."""
        if self._unsub_disconnect is not None:
            self._unsub_disconnect()
            self._unsub_disconnect = None
        if self._status_task is not None:
            self._status_task.cancel()
            self._status_task = None
        await super().async_shutdown()

    @property
    def connected(self) -> bool:
        """Return whether the device's entities are available.

        A kept-open link counts as connected until `SET_DISCONNECTED_DELAY`
        after it drops. An on-demand link closes between commands, so the
        device counts as connected while linked, or once it has been reached
        since setup for as long as Home Assistant's Bluetooth stack has not
        marked its connectable advertisements unavailable.
        """
        if self.device.keep_connection:
            return not self._disconnected
        return self._linked or (self._reached and self._present)

    @property
    def poll_interval(self) -> int:
        """Return the on-demand status poll interval in seconds, 0 when off."""
        return self._poll_interval

    @callback
    def async_start_on_demand(self, poll_interval: int) -> CALLBACK_TYPE:
        """Track advertisements, and poll the status if `poll_interval` is set.

        Returns the callback that stops both, for the config entry's unload.
        """
        address = self.device.address
        self._poll_interval = poll_interval
        self._present = bluetooth.async_address_present(
            self.hass, address, connectable=True
        )
        stoppers = [
            bluetooth.async_track_unavailable(
                self.hass, self._async_handle_unavailable, address, connectable=True
            )
        ]
        if poll_interval:
            stoppers.append(
                async_track_time_interval(
                    self.hass,
                    self._async_poll,
                    timedelta(seconds=poll_interval),
                    name=f"{DOMAIN} {address} status poll",
                    cancel_on_shutdown=True,
                )
            )

        @callback
        def _async_stop() -> None:
            for stop in stoppers:
                stop()

        return _async_stop

    @callback
    def async_handle_advertisement(self) -> None:
        """Note a received advertisement; on demand it restores availability."""
        if self._present:
            return
        self._present = True
        if not self.device.keep_connection:
            _LOGGER.debug("%s: Advertising again", self.device.address)
            self.async_update_listeners()

    @callback
    def _async_handle_unavailable(
        self, _service_info: bluetooth.BluetoothServiceInfoBleak
    ) -> None:
        """Mark an on-demand device unavailable once its advertisements stop."""
        self._present = False
        _LOGGER.debug("%s: No longer advertising", self.device.address)
        self.async_update_listeners()

    @callback
    def _async_poll(self, _now: datetime) -> None:
        """Connect an idle on-demand device to refresh its data points."""
        address = self.device.address
        if self._linked or (self._status_task and not self._status_task.done()):
            _LOGGER.debug("%s: Status poll skipped, device is busy", address)
            return
        if not self._present:
            _LOGGER.debug("%s: Status poll skipped, device not advertising", address)
            return
        self._async_request_status()

    @callback
    def _async_handle_connect(self) -> None:
        if self._unsub_disconnect is not None:
            self._unsub_disconnect()
            self._unsub_disconnect = None
        newly_linked = not self._linked
        self._linked = True
        self._reached = True
        if not self.device.keep_connection:
            if newly_linked:
                _LOGGER.debug("%s: Connected on demand", self.device.address)
                # The status request that opened this link is still in flight.
                if self._status_task is None or self._status_task.done():
                    self._async_request_status()
                self.async_update_listeners()
            return
        if self._disconnected:
            self._disconnected = False
            _LOGGER.debug("%s: Connected", self.device.address)
            self._async_request_status()
            self.async_update_listeners()

    @callback
    def _async_request_status(self) -> None:
        """Ask a freshly connected device for the data points it holds.

        Devices only push what changes, so a reconnected device would keep the
        values of the previous session until something moves. Requesting the
        full status once per connection makes the entities reflect the device
        as it actually is; entities that opt into restore cover the short window
        before the answer arrives.
        """
        _LOGGER.debug("%s: Requesting device status", self.device.address)
        if self._status_task is not None:
            self._status_task.cancel()
        self._status_task = self.hass.async_create_task(self.device.update())
        self._status_task.add_done_callback(self._async_status_request_done)

    @callback
    def _async_status_request_done(self, task: asyncio.Task[None]) -> None:
        """Consume the result of a status request.

        A request that fails on a transient BLE drop has already been logged
        with its traceback by the protocol layer; retrieving the exception here
        keeps the task from reporting it a second time as an unretrieved
        exception.
        """
        if task.cancelled():
            return
        if (exception := task.exception()) is None:
            return
        _LOGGER.debug(
            "%s: Status request failed: %s: %s",
            self.device.address,
            type(exception).__name__,
            exception,
        )

    @callback
    def _async_handle_update(self, updates: list[TuyaBLEDataPoint]) -> None:
        """Broadcast coordinator listeners and fire fingerbot button events."""
        self._async_handle_connect()
        self.async_set_updated_data({})
        _LOGGER.debug(
            "%s: Received update with %d data point(s): %s",
            self.device.address,
            len(updates),
            [update.dp_id for update in updates],
        )
        self._log_unmapped_dp(updates)
        info = get_device_product_info(self.device)
        # Only the Fingerbot Plus has a sensor button, so a manual control
        # entity is what makes a switch change worth an event.
        if info.fingerbot_manual_control_dp_id is None:
            return
        switch_dp_id = info.fingerbot_switch_dp_id
        for update in updates:
            if update.dp_id == switch_dp_id and update.changed_by_device:
                _LOGGER.debug(
                    "%s: Fingerbot button event for data point %s",
                    self.device.address,
                    update.dp_id,
                )
                self.hass.bus.fire(
                    FINGERBOT_BUTTON_EVENT,
                    {
                        CONF_ADDRESS: self.device.address,
                        CONF_DEVICE_ID: self.device.device_id,
                    },
                )

    def _resolve_dp_classification(self) -> tuple[bool, frozenset[int]]:
        """Return whether the product is registered, and the ids it maps.

        Resolved once per coordinator: the descriptor registry is a cached
        singleton, but the classification must not be rebuilt on every update.
        """
        if self._dp_classification is None:
            category = self.device.category
            product_id = self.device.product_id
            self._dp_classification = (
                get_registry().get(category, product_id) is not None,
                get_mapped_dp_ids(category, product_id),
            )
        return self._dp_classification

    def _log_unmapped_dp(self, updates: list[TuyaBLEDataPoint]) -> None:
        """Report received data points that no entity of this product uses.

        A data point with no entity is usually harmless, so this only runs at
        DEBUG. Repeat reports of an unchanged data point are suppressed, and a
        product missing from the descriptor registry is reported separately,
        since then every id is effectively unknown.
        """
        if not _LOGGER.isEnabledFor(logging.DEBUG):
            return
        known_product, mapped = self._resolve_dp_classification()
        for update in updates:
            if update.dp_id in mapped:
                continue
            raw = update.raw_value or b""
            signature = (update.dp_type.name, raw)
            if self._reported_unmapped.get(update.dp_id) == signature:
                continue
            self._reported_unmapped[update.dp_id] = signature
            if known_product:
                _LOGGER.debug(
                    "%s: Unmapped DP id=%s type=%s raw=%s decoded=%s "
                    "not used by any entity of %s/%s",
                    self.device.address,
                    update.dp_id,
                    update.dp_type.name,
                    self.device.datapoints.loggable(update.dp_id, raw.hex()),
                    self.device.datapoints.loggable(update.dp_id, update.value),
                    self.device.category,
                    self.device.product_id,
                )
            else:
                _LOGGER.warning(
                    "%s: Unmapped DP id=%s type=%s raw=%s decoded=%s received for "
                    "unknown product %s/%s; support for this device can be added "
                    "with the data points from these log lines",
                    self.device.address,
                    update.dp_id,
                    update.dp_type.name,
                    self.device.datapoints.loggable(update.dp_id, raw.hex()),
                    self.device.datapoints.loggable(update.dp_id, update.value),
                    self.device.category,
                    self.device.product_id,
                )

    @callback
    def _set_disconnected(self, _: Any) -> None:
        """Invoke the idle timeout callback, called when the alarm fires."""
        self._disconnected = True
        self._unsub_disconnect = None
        _LOGGER.debug("%s: Idle timeout, marked as disconnected", self.device.address)
        self.async_update_listeners()

    @callback
    def _async_handle_disconnect(self) -> None:
        """Schedule a delayed transition to disconnected state.

        An on-demand link closes on purpose, so availability is decided at once
        by `connected` instead of after a delay.
        """
        self._linked = False
        if not self.device.keep_connection:
            self.async_update_listeners()
            return
        if self._unsub_disconnect is None:
            delay: float = SET_DISCONNECTED_DELAY
            _LOGGER.debug(
                "%s: Disconnected, marking as disconnected in %ss",
                self.device.address,
                delay,
            )
            self._unsub_disconnect = async_call_later(
                self.hass, delay, self._set_disconnected
            )
        else:
            _LOGGER.debug(
                "%s: Already scheduled to disconnect, skipping", self.device.address
            )
