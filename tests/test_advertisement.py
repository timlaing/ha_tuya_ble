"""Versioned identity advertisement decoding, not full protocol support."""

# pylint: disable=protected-access

import hashlib
from typing import cast

from bleak.backends.scanner import AdvertisementData
from Crypto.Cipher import AES
import pytest

from custom_components.tuya_ble.tuya_ble import (
    MANUFACTURER_DATA_ID,
    SERVICE_UUID,
    SERVICE_UUID_V4,
    decode_tuya_ble_advertisement,
)
from tests.conftest import FakeAdvertisementData, make_credentials, make_device


def advertisement(
    service: bytes,
    manufacturer: bytes,
    service_uuid: str = SERVICE_UUID_V4,
) -> AdvertisementData:
    """Build a scanner record with already-separated service/company IDs."""
    return cast(
        AdvertisementData,
        FakeAdvertisementData(
            service_data={service_uuid: service},
            manufacturer_data={MANUFACTURER_DATA_ID: manufacturer},
        ),
    )


def v4_advertisement(
    service: bytes = b"\x49\x00\x00\x08abcdefgh",
    plaintext: bytes = b"1234567890abcdef",
    flags: int = 0,
) -> AdvertisementData:
    """Encrypt a synthetic vector using the firmware's complete service record."""
    key = hashlib.md5(service, usedforsecurity=False).digest()
    encrypted = AES.new(key, AES.MODE_CBC, key).encrypt(plaintext)
    return advertisement(service, bytes((0, 0, 1, flags)) + encrypted)


@pytest.mark.parametrize(
    ("service", "bound", "product_id"),
    [
        (b"\x41\x00\x00\x08abcdefgh", False, "abcdefgh"),
        (b"\x49\x00\x00\x08abcdefgh", True, None),
        (b"\x43\x00\x00\x08abcdefgh", False, "abcdefgh"),
        (b"\x45\x00\x00\x08abcdefgh", False, "abcdefgh"),
        (b"\x41\x0c\x00\x08abcdefgh", False, "abcdefgh"),
        (b"\x41\x00\x01\x08abcdefgh", False, None),
        (b"\x41\x00\x00\x08\x80abcdefg", False, None),
        (b"\x41\x00\x00\x08\x00abcdefg", False, None),
        (b"\x41\x00\x00\x10abcdefghijklmnop", False, "abcdefghijklmnop"),
    ],
)
def test_v4_identity(service: bytes, bound: bool, product_id: str | None) -> None:
    """Read version/binding from frame control, not the manufacturer header."""
    info = decode_tuya_ble_advertisement(v4_advertisement(service))
    assert info is not None
    assert info.uuid == "1234567890abcdef"
    assert info.protocol_version == 4
    assert info.is_bound is bound
    assert info.product_id == product_id


def test_v3_captured_vector() -> None:
    """Keep the supplied real protocol-3 device decoding unchanged."""
    info = decode_tuya_ble_advertisement(
        advertisement(
            bytes.fromhex("003082897c084b161b"),
            bytes.fromhex("8003000001003fa8bf83e3125ec3085a623a4d80d569"),
            SERVICE_UUID,
        )
    )
    assert info is not None
    assert info.uuid == "8f88d0628f826a4a"
    assert info.product_id is None
    assert info.is_bound is True
    assert info.protocol_version == 3


@pytest.mark.parametrize(
    "service",
    [
        b"",
        b"\x41",
        b"\x41\x00\x00",
        b"\x51\x00\x00\x08abcdefgh",  # Unverified v5: fail closed.
        b"\x61\x00\x00\x08abcdefgh",  # Unknown version.
        b"\x31\x00\x00\x08abcdefgh",  # Not a v3 FD50 identity.
        b"\x48\x00\x00\x08abcdefgh",  # Beacon, not identity.
        b"\x41\x00\x02\x08abcdefgh",  # Unsupported product type.
        b"\x41\x00\x00\x00",  # Empty product record.
        b"\x41\x00\x00\x11abcdefghijklmnopq",  # Over-long record.
        b"\x41\x00\x00\x08abcdefg",  # Truncation.
        b"\x41\x00\x00\x08abcdefghi",  # Extra bytes.
    ],
)
def test_invalid_v4_service(service: bytes) -> None:
    """Never try alternate layouts or keys to obtain readable plaintext."""
    assert decode_tuya_ble_advertisement(v4_advertisement(service)) is None


@pytest.mark.parametrize("length", [0, 3, 4, 19, 21, 22, 36])
def test_invalid_v4_manufacturer_length(length: int) -> None:
    """Require the four-byte header and one encrypted 16-byte identity."""
    assert (
        decode_tuya_ble_advertisement(
            advertisement(b"\x41\x00\x00\x08abcdefgh", bytes(length))
        )
        is None
    )


@pytest.mark.parametrize("plaintext", [b"\xff" * 16, bytes(16)])
def test_v4_invalid_plaintext(plaintext: bytes) -> None:
    """Reject non-UTF-8 and empty decrypted identities."""
    assert decode_tuya_ble_advertisement(v4_advertisement(plaintext=plaintext)) is None


def test_v4_compressed_identity_rejected() -> None:
    """Do not misinterpret binary compressed identities as UTF-8."""
    assert decode_tuya_ble_advertisement(v4_advertisement(flags=1)) is None


def test_v4_null_padding() -> None:
    """Preserve the existing zero-padding handling."""
    info = decode_tuya_ble_advertisement(
        v4_advertisement(plaintext=b"identity" + bytes(8))
    )
    assert info is not None
    assert info.uuid == "identity"


@pytest.mark.parametrize("version", [0, 4, 5, 6])
def test_classic_unknown_version(version: int) -> None:
    """Classic layout must not silently accept v4/v5 manufacturer headers."""
    assert (
        decode_tuya_ble_advertisement(
            advertisement(
                b"\x00abcdefgh", bytes((0x80, version)) + bytes(20), SERVICE_UUID
            )
        )
        is None
    )


def test_classic_unaligned_identity() -> None:
    """Reject incomplete ciphertext after a valid classic header."""
    assert (
        decode_tuya_ble_advertisement(
            advertisement(b"\x00abcdefgh", b"\x80\x03" + bytes(5), SERVICE_UUID)
        )
        is None
    )


def test_no_fallback_from_fd50() -> None:
    """Reject unsupported FD50 even when stale classic data is also present."""
    adv = v4_advertisement(b"\x51\x00\x00\x08abcdefgh")
    adv.service_data[SERVICE_UUID] = b"\x00abcdefgh"
    assert decode_tuya_ble_advertisement(adv) is None


def test_runtime_uses_v4_metadata() -> None:
    """Initialize through the public device API with v4 identity and flags."""
    dev = make_device()
    adv = v4_advertisement()
    dev.set_ble_device_and_advertisement_data(dev._ble_device, adv)
    dev.initialize_with_credentials(make_credentials())
    assert dev.uuid == "1234567890abcdef"
    assert dev._protocol_version == 4
    assert dev._is_bound is True
