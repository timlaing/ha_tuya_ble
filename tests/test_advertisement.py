"""Versioned identity advertisement decoding, not full protocol support."""

# pylint: disable=protected-access

import hashlib
from typing import cast
from unittest.mock import AsyncMock, patch

from bleak.backends.scanner import AdvertisementData
from Crypto.Cipher import AES
import pytest

from custom_components.tuya_ble.tuya_ble import (
    MANUFACTURER_DATA_ID,
    SERVICE_UUID,
    SERVICE_UUID_V4,
    decode_tuya_ble_advertisement,
)
from custom_components.tuya_ble.tuya_ble.advertisement import (
    TuyaBLEIdentityRecord,
    decode_fd50_identity_record,
)
from custom_components.tuya_ble.tuya_ble.const import TuyaBLECode
from custom_components.tuya_ble.tuya_ble.exceptions import TuyaBLEDeviceError
from tests.conftest import (
    FakeAdvertisementData,
    FakeBleakClient,
    make_credentials,
    make_device,
)


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


@pytest.mark.parametrize("version", [3, 4])
@pytest.mark.parametrize(
    "plaintext",
    [
        b"abc\x00" + b"d" * 12,
        b"\n" + b"d" * 15,
        b"\x7f" + b"d" * 15,
        "\u200b".encode() + b"d" * 13,
    ],
)
def test_non_printable_identity(version: int, plaintext: bytes) -> None:
    """Reject valid UTF-8 containing embedded NUL or other control characters."""
    if version == 4:
        adv = v4_advertisement(plaintext=plaintext)
    else:
        key = hashlib.md5(b"abcdefgh", usedforsecurity=False).digest()
        encrypted = AES.new(key, AES.MODE_CBC, key).encrypt(plaintext)
        adv = advertisement(
            b"\x00abcdefgh", b"\x80\x03" + bytes(4) + encrypted, SERVICE_UUID
        )
    assert decode_tuya_ble_advertisement(adv) is None


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


def service_only(
    service: bytes, service_uuid: str = SERVICE_UUID_V4
) -> AdvertisementData:
    """Build what a passive scanner stores: service data, no scan response."""
    return cast(
        AdvertisementData, FakeAdvertisementData(service_data={service_uuid: service})
    )


@pytest.mark.parametrize(
    ("service", "bound"),
    [
        (b"\x49\x00\x00\x08abcdefgh", True),
        (b"\x41\x00\x00\x08abcdefgh", False),
        (b"\x41\x00\x01\x08abcdefgh", False),
        (b"\x41\x00\x00\x10abcdefghijklmnop", False),
    ],
)
def test_fd50_identity_record_without_scan_response(
    service: bytes, bound: bool
) -> None:
    """The advertisement alone gives the protocol version and bound flag."""
    adv = service_only(service)
    assert decode_tuya_ble_advertisement(adv) is None
    assert decode_fd50_identity_record(adv) == TuyaBLEIdentityRecord(
        is_bound=bound, protocol_version=4
    )


@pytest.mark.parametrize(
    "adv",
    [
        service_only(b"\x41"),
        service_only(b"\x48\x00\x00\x08abcdefgh"),  # beacon
        service_only(b"\x59\x00\x00\x08abcdefgh"),  # protocol 5
        service_only(b"\x41\x00\x02\x08abcdefgh"),  # unknown product type
        service_only(b"\x41\x00\x00\x14" + bytes(20)),  # 20-character product id
        service_only(b"\x41\x00\x00\x08abcdefg"),  # length mismatch
        service_only(b"\x00abcdefgh", SERVICE_UUID),  # classic record
        cast(AdvertisementData, FakeAdvertisementData()),
    ],
)
def test_fd50_identity_record_rejects_like_the_full_decode(
    adv: AdvertisementData,
) -> None:
    """Beacons, other versions and malformed records give no record."""
    assert decode_fd50_identity_record(adv) is None


def test_runtime_uses_identity_record_with_stored_credentials() -> None:
    """Stored credentials make the advertisement alone enough at runtime."""
    credentials = make_credentials()
    dev = make_device()
    dev.set_ble_device_and_advertisement_data(
        dev._ble_device, service_only(b"\x49\x00\x00\x08abcdefgh")
    )
    dev.initialize_with_credentials(credentials)
    assert dev._protocol_version == 4
    assert dev._is_bound is True
    assert dev.uuid == credentials.uuid


@pytest.mark.parametrize(
    "service",
    [
        b"\x41",
        b"\x48\x00\x00\x08abcdefgh",
        b"\x51\x00\x00\x08abcdefgh",
    ],
)
async def test_rejected_fd50_does_not_connect(service: bytes) -> None:
    """Stored credentials must not start a handshake using the default version."""
    dev = make_device()
    dev.set_ble_device_and_advertisement_data(
        dev._ble_device, v4_advertisement(service)
    )
    dev.initialize_with_credentials(make_credentials())
    with (
        patch(
            "custom_components.tuya_ble.tuya_ble.base.establish_connection",
            new_callable=AsyncMock,
        ) as connect,
        patch.object(
            dev, "_send_packet_while_connected", new_callable=AsyncMock
        ) as send,
    ):
        with pytest.raises(TuyaBLEDeviceError):
            await dev.update()
        connect.assert_not_awaited()
        send.assert_not_awaited()


async def test_fd50_recovery_uses_v4_packet_header() -> None:
    """A later complete identity allows real packet construction with version 4."""
    dev = make_device()
    dev.set_ble_device_and_advertisement_data(
        dev._ble_device, v4_advertisement(b"\x41")
    )
    dev.initialize_with_credentials(make_credentials())
    with pytest.raises(TuyaBLEDeviceError):
        await dev.update()
    dev.set_ble_device_and_advertisement_data(dev._ble_device, v4_advertisement())
    codes: list[TuyaBLECode] = []
    packets: list[bytes] = []

    async def send(
        code: TuyaBLECode, data: bytes, response_to: int, wait: bool
    ) -> bool:
        codes.append(code)
        packets.append(dev._build_packets(1, code, data, response_to)[0])
        # Simulate successful handshake replies without any BLE I/O.
        dev._session_key = bytes(16)
        if code == TuyaBLECode.FUN_SENDER_PAIR:
            dev._is_paired = True
        return True

    with (
        patch(
            "custom_components.tuya_ble.tuya_ble.base.establish_connection",
            new_callable=AsyncMock,
            return_value=FakeBleakClient(),
        ),
        patch.object(dev, "_send_packet_while_connected", side_effect=send),
    ):
        await dev.update()
    assert codes == [
        TuyaBLECode.FUN_SENDER_DEVICE_INFO,
        TuyaBLECode.FUN_SENDER_PAIR,
        TuyaBLECode.FUN_SENDER_DEVICE_STATUS,
    ]
    assert all(packet[2] == 0x40 for packet in packets)


async def test_paired_fd50_connection_survives_beacon() -> None:
    """Do not block an established session when the latest scan is a beacon."""
    dev = make_device()
    dev.set_ble_device_and_advertisement_data(dev._ble_device, v4_advertisement())
    dev.initialize_with_credentials(make_credentials())
    dev._client = FakeBleakClient()  # type: ignore[assignment]
    dev._is_paired = True
    dev.set_ble_device_and_advertisement_data(
        dev._ble_device, v4_advertisement(b"\x48\x00\x00\x08abcdefgh")
    )
    with patch.object(
        dev, "_send_packet_while_connected", new_callable=AsyncMock, return_value=True
    ) as send:
        await dev.update()
    send.assert_awaited_once()
