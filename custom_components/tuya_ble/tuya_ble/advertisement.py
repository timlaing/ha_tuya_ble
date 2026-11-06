"""Decode classic and protocol-4 Tuya BLE identity advertisements.

Protocol 4 uses FD50 service data as the complete MD5 input and a four-byte
manufacturer header. See docs/BLE_ADVERTISEMENT_DECODING.md for source evidence
and unsupported formats. Unknown versions are deliberately not guessed.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib

from bleak.backends.scanner import AdvertisementData
from Crypto.Cipher import AES

from .const import MANUFACTURER_DATA_ID, SERVICE_UUID, SERVICE_UUID_V4


@dataclass(frozen=True)
class TuyaBLEAdvertisementInfo:
    """Identity data decoded from a Tuya BLE advertisement."""

    product_id: str | None
    uuid: str
    is_bound: bool
    protocol_version: int


def _decode_uuid(key_material: bytes, encrypted_uuid: bytes) -> str | None:
    """Decode the uncompressed identity without trying alternate keys."""
    if len(encrypted_uuid) % AES.block_size:
        return None
    key = hashlib.md5(key_material, usedforsecurity=False).digest()
    cipher = AES.new(key, AES.MODE_CBC, key)  # noqa: S5542
    try:
        uuid = cipher.decrypt(encrypted_uuid).rstrip(b"\x00").decode("utf-8")
    except ValueError:
        return None
    return uuid or None


def _product_id(raw: bytes) -> str | None:
    """Only expose readable product IDs, not bound-device obfuscation bytes."""
    return (
        raw.decode("ascii")
        if raw.isascii() and raw.decode("ascii").isprintable()
        else None
    )


def _decode_classic(
    service: bytes, manufacturer: bytes
) -> TuyaBLEAdvertisementInfo | None:
    """Preserve the A201 product-record key derivation for classic devices."""
    if len(service) <= 1 or service[0] != 0 or len(manufacturer) <= 6:
        return None
    if manufacturer[1] not in (2, 3):
        return None
    uuid = _decode_uuid(service[1:], manufacturer[6:])
    if uuid is None:
        return None
    return TuyaBLEAdvertisementInfo(
        product_id=_product_id(service[1:]),
        uuid=uuid,
        is_bound=bool(manufacturer[0] & 0x80),
        protocol_version=manufacturer[1],
    )


def _decode_v4(service: bytes, manufacturer: bytes) -> TuyaBLEAdvertisementInfo | None:
    """Decode only the verified FD50 identity layout, never beacon messages."""
    # Flag bit 0 indicates a compressed 20-character identity. The current
    # pairing implementation supports only uncompressed identities.
    if len(service) < 4 or len(manufacturer) != 20 or manufacturer[3] & 0x01:
        return None
    # High nibble is the protocol version; bit 0 marks an identity record.
    if service[0] >> 4 != 4 or not service[0] & 0x01:
        return None
    product_type, product_length = service[2:4]
    if product_type not in (0, 1) or not 1 <= product_length <= 16:
        return None
    if len(service) != 4 + product_length:
        return None
    uuid = _decode_uuid(service, manufacturer[4:])
    if uuid is None:
        return None
    bound = bool(service[0] & 0x08)
    return TuyaBLEAdvertisementInfo(
        product_id=_product_id(service[4:])
        if product_type == 0 and not bound
        else None,
        uuid=uuid,
        is_bound=bound,
        protocol_version=4,
    )


def decode_tuya_ble_advertisement(
    advertisement_data: AdvertisementData,
) -> TuyaBLEAdvertisementInfo | None:
    """Select one versioned layout and decode its device identity.

    FD50 takes precedence when present: do not fall back to a stale classic
    record after rejecting an unsupported version such as protocol 5.
    """
    service_data = advertisement_data.service_data or {}
    manufacturer = (advertisement_data.manufacturer_data or {}).get(
        MANUFACTURER_DATA_ID
    )
    if not manufacturer:
        return None
    if SERVICE_UUID_V4 in service_data:
        return _decode_v4(service_data[SERVICE_UUID_V4], manufacturer)
    return _decode_classic(service_data.get(SERVICE_UUID, b""), manufacturer)
