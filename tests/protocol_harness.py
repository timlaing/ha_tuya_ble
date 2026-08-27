"""Test helpers driving TuyaBLEDevice over a fake BLE client."""

from __future__ import annotations

import hashlib
from struct import pack

from Crypto.Cipher import AES

from custom_components.tuya_ble.tuya_ble.const import (
    CHARACTERISTIC_NOTIFY,
    TuyaBLECode,
)
from tests.conftest import (
    FakeBleakClient,
    FakeBLEManager,
    make_credentials,
    make_device,
)


def pack_varint(value: int) -> bytes:
    """Encode a non-negative integer as a variable-length byte sequence."""
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value != 0:
            byte |= 0x80
        out += pack(">B", byte)
        if value == 0:
            break
    return bytes(out)


def crc16(data: bytes) -> int:
    """Compute the Tuya CRC16 checksum of the given data."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte & 255
        for _ in range(8):
            tmp = crc & 1
            crc >>= 1
            if tmp != 0:
                crc ^= 0xA001
    return crc


def encrypt_payload(
    key: bytes,
    security_flag: int,
    seq_num: int,
    response_to: int,
    code: TuyaBLECode,
    data: bytes,
) -> bytes:
    """Build and AES-encrypt a full protocol payload for the given fields."""
    iv = b"\x00" * 16
    raw = bytearray()
    raw += pack(">IIHH", seq_num, response_to, code.value, len(data))
    raw += data
    raw += pack(">H", crc16(bytes(raw)))
    while len(raw) % 16 != 0:
        raw += b"\x00"
    cipher = AES.new(key, AES.MODE_CBC, iv)
    return bytes([security_flag]) + iv + cipher.encrypt(bytes(raw))


def make_raw(
    seq_num: int,
    response_to: int,
    code: TuyaBLECode | int,
    data: bytes,
    crc_override: int | None = None,
    extra_after_crc: bytes = b"",
) -> bytes:
    """Build a decrypted protocol frame (header + data + crc [+ padding])."""
    raw = bytearray()
    raw += pack(
        ">IIHH",
        seq_num,
        response_to,
        int(code.value if isinstance(code, TuyaBLECode) else code),
        len(data),
    )
    raw += data
    new = crc_override if crc_override is not None else crc16(bytes(raw))
    raw += pack(">H", new)
    raw += extra_after_crc
    while len(raw) % 16 != 0:
        raw += b"\x00"
    return bytes(raw)


def encrypt_raw(key: bytes, security_flag: int, raw: bytes) -> bytes:
    """Encrypt an already-built raw frame with the given key and flag."""
    iv = b"\x00" * 16
    cipher = AES.new(key, AES.MODE_CBC, iv)
    return bytes([security_flag]) + iv + cipher.encrypt(raw)


def frame_packet0(encrypted: bytes, protocol_version: int = 2) -> bytes:
    """Wrap encrypted bytes in a packet-0 frame (version byte + varints)."""
    return (
        pack_varint(0)
        + pack_varint(len(encrypted))
        + pack(">B", protocol_version << 4)
        + encrypted
    )


class ProtocolHarness:
    """Wires a TuyaBLEDevice to a fake BLE client and drives it via public calls."""

    def __init__(self, protocol_version: int = 2) -> None:
        creds = make_credentials()
        self.manager = FakeBLEManager(creds)
        self.device = make_device(manager=self.manager)
        self.client = FakeBleakClient(is_connected=True)
        self.device._client = self.client  # type: ignore[assignment]
        self.device._is_paired = True
        self.device._protocol_version = protocol_version
        self.device._device_info = creds
        self.device._local_key = creds.local_key[:6].encode()
        login_key = hashlib.md5(self.device._local_key).digest()  # noqa: S324
        self.device._login_key = login_key
        self.device._session_key = self.device._login_key
        self.device._auth_key = b"\x00" * 32

    async def register_notify(self) -> None:
        """Register the device's notification handler with the fake client."""
        await self.client.start_notify(
            CHARACTERISTIC_NOTIFY,
            self.device._notification_handler,  # pylint: disable=protected-access
        )

    def notify(self, data: bytes) -> None:
        """Deliver a raw notification to the device's handler."""
        assert self.client.notify_handler is not None
        self.client.notify_handler(None, bytearray(data))  # type: ignore[arg-type]

    def writes(self) -> list[bytes]:
        """Return the bytes written to the fake client so far."""
        return self.client.writes
