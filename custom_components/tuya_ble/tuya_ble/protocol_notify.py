"""Tuya BLE protocol notify mixin: accumulating BLE notification fragments."""

from __future__ import annotations

import logging
import struct
import time
from typing import Any

from .const import (
    INPUT_REASSEMBLY_TIMEOUT,
    MAX_INPUT_LENGTH,
)
from .exceptions import (
    TuyaBLEDataLengthError,
    TuyaBLEError,
)
from .protocol_parse import TuyaBLEProtocolParseMixin

_LOGGER = logging.getLogger(__name__)


class TuyaBLEProtocolNotifyMixin(TuyaBLEProtocolParseMixin):
    """Mixin providing BLE notification accumulation and handling."""

    def _notification_handler(self, _sender: Any, data: bytearray) -> None:
        """Accumulate fragmented BLE notification packets and parse when complete."""
        _LOGGER.debug("%s: Packet received: %s", self.address, data.hex())

        pos: int = 0
        packet_num: int

        packet_num, pos = self._unpack_int(bytes(data), pos)

        if packet_num < self._input_expected_packet_num and not self._resync_stale(
            packet_num
        ):
            return

        if packet_num == self._input_expected_packet_num:
            buffer = self._append_expected_packet(packet_num, data, pos)
            if buffer is None:
                return
        else:
            _LOGGER.error(
                "%s: Missing packet (number %s) in notifications, received %s",
                self.address,
                self._input_expected_packet_num,
                packet_num,
            )
            self._clean_input()
            return

        if len(buffer) > self._input_expected_length:
            _LOGGER.error(
                "%s: Unexpected length of data in notifications, "
                "received %s expected %s",
                self.address,
                len(buffer),
                self._input_expected_length,
            )
            self._clean_input()
            return

        if len(buffer) == self._input_expected_length:
            self._parse_input()

    def _resync_stale(self, packet_num: int) -> bool:
        """Handle a packet number below the expected one.

        A fresh packet 0 restarts reassembly; any other stale packet is
        discarded. Returns True when processing may continue.
        """
        if packet_num != 0:
            _LOGGER.error(
                "%s: Unexpected packet (number %s) in notifications, expected %s",
                self.address,
                packet_num,
                self._input_expected_packet_num,
            )
            self._clean_input()
            return False
        _LOGGER.debug(
            "%s: Received fresh packet 0, restarting notification reassembly "
            "(expected %s)",
            self.address,
            self._input_expected_packet_num,
        )
        self._clean_input()
        return True

    def _append_expected_packet(
        self, packet_num: int, data: bytearray, pos: int
    ) -> bytearray | None:
        """Start or extend reassembly for the expected packet.

        Returns the accumulated buffer, or None when reassembly must stop.
        """
        if packet_num == 0:
            self._input_buffer = bytearray()
            expected_length, pos = self._unpack_int(bytes(data), pos)
            pos += 1
            if expected_length > MAX_INPUT_LENGTH:
                _LOGGER.error(
                    "%s: Announced length %s in notifications exceeds"
                    " the %s byte maximum; discarding",
                    self.address,
                    expected_length,
                    MAX_INPUT_LENGTH,
                )
                self._clean_input()
                raise TuyaBLEDataLengthError()
            self._input_expected_length = expected_length
            self._input_reassembly_deadline = (
                time.monotonic() + INPUT_REASSEMBLY_TIMEOUT
            )
        elif time.monotonic() > self._input_reassembly_deadline:
            _LOGGER.error(
                "%s: Timed out waiting for packet %s of %s bytes",
                self.address,
                self._input_expected_packet_num,
                self._input_expected_length,
            )
            self._clean_input()
            return None
        buffer = self._input_buffer
        if buffer is None:
            _LOGGER.error("%s: Buffer not initialized", self.address)
            return None
        buffer = buffer + data[pos:]
        self._input_buffer = buffer
        self._input_expected_packet_num += 1
        return buffer

    def _safe_notification_handler(self, sender: Any, data: bytearray) -> None:
        """Process a BLE notification without leaking protocol errors to Bleak."""
        try:
            self._notification_handler(sender, data)
        except (TuyaBLEError, ValueError, struct.error, IndexError) as err:
            self._clean_input()
            _LOGGER.warning(
                "%s: Ignoring malformed BLE notification: %s",
                self.address,
                err,
            )
