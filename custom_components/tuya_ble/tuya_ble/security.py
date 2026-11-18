"""Login key derivation for the classic and FD50 Security V2 channels."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib

from .const import SECURITY_V2_KEY_LENGTH, SecurityMode
from .exceptions import TuyaBLEDeviceError

CLASSIC_LOCAL_KEY_LENGTH = 6

# Byte 54 of a long DEVICE_INFO reply mirrors the advertised Security V2 state:
# bit 1 is "supported" and bit 2 "enabled" (V2 keys provisioned).
DEVICE_INFO_SECURITY_POS = 54
DEVICE_INFO_SECURITY_V2 = 0x02
DEVICE_INFO_SECURITY_V2_ENABLED = 0x04


@dataclass(frozen=True)
class DeviceInfoSecurity:
    """Security V2 state reported in a DEVICE_INFO reply, for diagnostics only."""

    raw: int

    @property
    def security_v2(self) -> bool:
        """Return whether the firmware supports Security V2."""
        return bool(self.raw & DEVICE_INFO_SECURITY_V2)

    @property
    def security_v2_enabled(self) -> bool:
        """Return whether the firmware has Security V2 keys provisioned."""
        return bool(self.raw & DEVICE_INFO_SECURITY_V2_ENABLED)

    @classmethod
    def from_device_info(cls, data: bytes) -> DeviceInfoSecurity | None:
        """Read the flags from a reply, or None when it is too short to carry them."""
        if len(data) <= DEVICE_INFO_SECURITY_POS:
            return None
        return cls(data[DEVICE_INFO_SECURITY_POS])


def is_valid_sec_key(sec_key: str) -> bool:
    """Return whether a cloud sec_key can derive Security V2 keys."""
    return len(sec_key.encode()) == SECURITY_V2_KEY_LENGTH


def security_modes(
    supported: bool, enabled: bool, sec_key: str | None
) -> list[SecurityMode]:
    """Return the login channels to verify, most likely first.

    The FD50 advertisement announces whether Security V2 is supported and
    whether it is switched on. Switched-on firmware rejects every classic
    frame, so only Security V2 is tried. Firmware that merely supports it
    keeps the classic login until the app provisions Security V2, so the
    classic login is tried first and Security V2 second. Without a cloud
    ``sec_key``, or without the "supported" bit, only the classic login is
    possible: setup stores the key only for devices announcing support.
    """
    if sec_key is None or not supported:
        return [SecurityMode.CLASSIC]
    if enabled:
        return [SecurityMode.V2]
    return [SecurityMode.CLASSIC, SecurityMode.V2]


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
