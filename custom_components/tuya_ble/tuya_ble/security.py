"""Login key derivation for the classic and FD50 Security V2 channels."""

from __future__ import annotations

import hashlib

from .const import SECURITY_V2_KEY_LENGTH
from .exceptions import TuyaBLEDeviceError

CLASSIC_LOCAL_KEY_LENGTH = 6


def is_valid_sec_key(sec_key: str) -> bool:
    """Return whether a cloud sec_key can derive Security V2 keys."""
    return len(sec_key.encode()) == SECURITY_V2_KEY_LENGTH


def derive_login_keys(
    local_key: str, sec_key: str | None
) -> tuple[bytes, bytes | None, bytes]:
    """Return the pairing local key, Security V2 key material and login key.

    The classic channel uses the first six bytes of the local key. Security V2
    uses the full local key followed by the 16-byte cloud ``sec_key``; a
    ``sec_key`` of any other width would derive keys the device cannot match,
    so it is rejected rather than truncated.
    """
    local = local_key.encode()
    if len(local) < CLASSIC_LOCAL_KEY_LENGTH:
        raise TuyaBLEDeviceError(0)
    security_v2_key: bytes | None = None
    if sec_key is not None:
        if not is_valid_sec_key(sec_key):
            raise TuyaBLEDeviceError(0)
        security_v2_key = local + sec_key.encode()
    classic = local[:CLASSIC_LOCAL_KEY_LENGTH]
    login_key = hashlib.md5(security_v2_key or classic, usedforsecurity=False).digest()
    return classic, security_v2_key, login_key
