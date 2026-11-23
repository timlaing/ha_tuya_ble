"""Validation and framing of the cloud ``ble_unlock_check`` for Tuya BLE locks.

Some Tuya BLE locks only open on a DP 71 (``ble_unlock_check``) command built
from a check code the Tuya cloud issues to one app member. The cloud value is
base64 of a 19-byte record::

    member id (2) | 0xFFFF | check code (8 ASCII digits) | 0x01 | time (4) | 0x0000

and the lock accepts::

    0xFFFF | member id (2) | check code (8) | 0x01 | unix time (4, BE) | 0x0001

Some locks instead echo the four bytes stored in the cloud value (the ``time``
field above) rather than the current time; see ``build_stored_check_unlock_command``.
The member id is the app user slot the check was issued for (``0x0001`` for the
first member) and is taken from the value, never assumed. The format was
reverse-engineered from the Tuya app by GeorgeBark (ha-tuya-ble/ha_tuya_ble#353)
and extended to any member id by albertgoca93 (ha-tuya-ble/ha_tuya_ble#360),
both MIT licensed. Values are credentials: never log or export them.
"""

from __future__ import annotations

import base64

BLE_UNLOCK_CHECK_LENGTH = 19
_MEMBER_ID = slice(0, 2)
_SEPARATOR = slice(2, 4)
_CHECK_CODE = slice(4, 12)
_CHECK_FLAG_INDEX = 12
_KEY = slice(13, 17)
_TRAILER = slice(17, 19)
_INVALID_MEMBER_IDS = frozenset({b"\x00\x00", b"\xff\xff"})
_MAX_TIMESTAMP = 0xFFFFFFFF


def decode_ble_unlock_check(value: str) -> bytes:
    """Return the decoded cloud check, raising ValueError when it is malformed.

    The error message never contains the value itself.
    """
    if not isinstance(value, str):
        raise ValueError("ble_unlock_check must be a string")
    try:
        payload = base64.b64decode(value, validate=True)
    except ValueError:  # binascii.Error is a ValueError
        raise ValueError("ble_unlock_check must be valid base64") from None
    if len(payload) != BLE_UNLOCK_CHECK_LENGTH or not _has_check_layout(payload):
        raise ValueError("ble_unlock_check has an invalid format")
    return payload


def _has_check_layout(payload: bytes) -> bool:
    """Return whether a 19-byte record has the cloud check's fixed fields."""
    return (
        payload[_MEMBER_ID] not in _INVALID_MEMBER_IDS
        and payload[_SEPARATOR] == b"\xff\xff"
        and payload[_CHECK_CODE].isdigit()
        and payload[_CHECK_FLAG_INDEX] == 1
        and payload[_TRAILER] == b"\x00\x00"
    )


def build_stored_check_unlock_command(ble_unlock_check: str) -> bytes:
    """Build a DP 71 unlock command that replays the stored check code and key.

    Some locks (e.g. the Drawer Lock CTL20H, ha-tuya-ble/ha_tuya_ble#361) ignore
    a timestamped command and only accept the check code and the four key bytes
    stored in the cloud value. The layout is otherwise the same as the
    timestamped variant; the member id still comes from the value, never fixed.
    """
    check = decode_ble_unlock_check(ble_unlock_check)
    return (
        b"\xff\xff"
        + check[_MEMBER_ID]
        + check[_CHECK_CODE]
        + b"\x01"
        + check[_KEY]
        + b"\x00\x01"
    )


def is_valid_ble_unlock_check(value: object) -> bool:
    """Return whether *value* is a well-formed cloud check."""
    if not isinstance(value, str):
        return False
    try:
        decode_ble_unlock_check(value)
    except ValueError:
        return False
    return True


def build_timestamped_unlock_command(ble_unlock_check: str, timestamp: int) -> bytes:
    """Build the DP 71 unlock command for *timestamp* (unix seconds)."""
    if not 0 <= timestamp <= _MAX_TIMESTAMP:
        raise ValueError("timestamp does not fit the unlock command")
    check = decode_ble_unlock_check(ble_unlock_check)
    return (
        b"\xff\xff"
        + check[_MEMBER_ID]
        + check[_CHECK_CODE]
        + b"\x01"
        + timestamp.to_bytes(4, "big")
        + b"\x00\x01"
    )
