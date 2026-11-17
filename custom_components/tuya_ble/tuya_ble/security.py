"""Login key derivation for the classic and FD50 Security V2 channels."""

from __future__ import annotations

import hashlib

from .const import SECURITY_V2_KEY_LENGTH
from .exceptions import TuyaBLEDeviceError

CLASSIC_LOCAL_KEY_LENGTH = 6


def derive_login_keys(
    local_key: str, sec_key: str | None
) -> tuple[bytes, bytes | None, bytes]:
    """Return the pairing local key, Security V2 key material and login key.

    The classic channel uses the first six bytes of the local key. Security V2
    uses the full 16-byte local key followed by the 16-byte cloud ``sec_key``;
    keys of any other width would derive keys the device cannot match, so they
    are rejected rather than truncated.
    """
    local = local_key.encode()
    if len(local) < CLASSIC_LOCAL_KEY_LENGTH:
        raise TuyaBLEDeviceError(0)
    security_v2_key: bytes | None = None
    if sec_key is not None:
        sec = sec_key.encode()
        if len(local) != SECURITY_V2_KEY_LENGTH or len(sec) != SECURITY_V2_KEY_LENGTH:
            raise TuyaBLEDeviceError(0)
        security_v2_key = local + sec
    classic = local[:CLASSIC_LOCAL_KEY_LENGTH]
    login_key = hashlib.md5(security_v2_key or classic, usedforsecurity=False).digest()
    return classic, security_v2_key, login_key
