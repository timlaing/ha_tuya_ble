"""Data coordinator for Tuya BLE devices."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.const import CONF_ADDRESS, CONF_DEVICE_ID
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
)

from .const import (
    DOMAIN,
    FINGERBOT_BUTTON_EVENT,
    SET_DISCONNECTED_DELAY,
)
from .device_registry import get_mapped_dp_ids, get_registry
from .products import get_device_product_info
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
        self._unsub_disconnect: CALLBACK_TYPE | None = None
        self._dp_classification: tuple[bool, frozenset[int]] | None = None
        self._reported_unmapped: dict[int, tuple[str, bytes]] = {}
        device.register_connected_callback(self._async_handle_connect)
        device.register_callback(self._async_handle_update)
        device.register_disconnected_callback(self._async_handle_disconnect)

    async def async_shutdown(self) -> None:
        """Cancel any pending disconnect timer on teardown."""
        if self._unsub_disconnect is not None:
            self._unsub_disconnect()
            self._unsub_disconnect = None
        await super().async_shutdown()

    @property
    def connected(self) -> bool:
        """Return whether the device is currently connected."""
        return not self._disconnected

    @callback
    def _async_handle_connect(self) -> None:
        if self._unsub_disconnect is not None:
            self._unsub_disconnect()
        if self._disconnected:
            self._disconnected = False
            _LOGGER.debug("%s: Connected", self.device.address)
            self.async_update_listeners()

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
        if info and info.fingerbot and info.fingerbot.manual_control != 0:
            for update in updates:
                if update.dp_id == info.fingerbot.switch and update.changed_by_device:
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
                    raw.hex(),
                    update.value,
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
                    raw.hex(),
                    update.value,
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
        """Schedule a delayed transition to disconnected state."""
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
