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
        device.register_connected_callback(self._async_handle_connect)
        device.register_callback(self._async_handle_update)
        device.register_disconnected_callback(self._async_handle_disconnect)

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
            self.async_update_listeners()

    @callback
    def _async_handle_update(self, updates: list[TuyaBLEDataPoint]) -> None:
        """Broadcast coordinator listeners and fire fingerbot button events."""
        self._async_handle_connect()
        self.async_set_updated_data({})
        info = get_device_product_info(self.device)
        if info and info.fingerbot and info.fingerbot.manual_control != 0:
            for update in updates:
                if update.dp_id == info.fingerbot.switch and update.changed_by_device:
                    self.hass.bus.fire(
                        FINGERBOT_BUTTON_EVENT,
                        {
                            CONF_ADDRESS: self.device.address,
                            CONF_DEVICE_ID: self.device.device_id,
                        },
                    )

    @callback
    def _set_disconnected(self, _: Any) -> None:
        """Invoke the idle timeout callback, called when the alarm fires."""
        self._disconnected = True
        self._unsub_disconnect = None
        self.async_update_listeners()

    @callback
    def _async_handle_disconnect(self) -> None:
        """Schedule a delayed transition to disconnected state."""
        if self._unsub_disconnect is None:
            delay: float = SET_DISCONNECTED_DELAY
            self._unsub_disconnect = async_call_later(
                self.hass, delay, self._set_disconnected
            )
