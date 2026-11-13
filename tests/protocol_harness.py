"""Test helpers driving TuyaBLEDevice over a fake BLE client."""

from __future__ import annotations

import hashlib
from struct import pack, unpack
from typing import cast

from bleak.backends.scanner import AdvertisementData
from Crypto.Cipher import AES

from custom_components.tuya_ble.tuya_ble.const import (
    CHARACTERISTIC_NOTIFY,
    GATT_MTU,
    MANUFACTURER_DATA_ID,
    SERVICE_UUID_V4,
    TuyaBLECode,
)
from tests.conftest import (
    FakeAdvertisementData,
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


def unpack_varint(data: bytes, pos: int) -> tuple[int, int]:
    """Decode a variable-length integer, returning it and the next position."""
    result = 0
    shift = 0
    while True:
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return result, pos


def fragment(
    encrypted: bytes, protocol_version: int, mtu: int = GATT_MTU
) -> list[bytes]:
    """Split an encrypted frame into notifications of at most ``mtu`` bytes."""
    packets: list[bytes] = []
    pos = 0
    packet_num = 0
    while pos < len(encrypted):
        header = pack_varint(packet_num)
        if packet_num == 0:
            header += pack_varint(len(encrypted)) + pack(">B", protocol_version << 4)
        part = encrypted[pos : pos + mtu - len(header)]
        packets.append(header + part)
        pos += len(part)
        packet_num += 1
    return packets


def reassemble(packets: list[bytes]) -> bytes:
    """Join the fragments of one frame back into its encrypted bytes."""
    encrypted = bytearray()
    total = 0
    for expected_num, packet in enumerate(packets):
        packet_num, pos = unpack_varint(packet, 0)
        assert packet_num == expected_num
        if packet_num == 0:
            total, pos = unpack_varint(packet, pos)
            pos += 1  # protocol version byte
        encrypted += packet[pos:]
    assert len(encrypted) == total
    return bytes(encrypted)


def decode_frame(
    keys: dict[int, bytes], packets: list[bytes]
) -> tuple[int, int, int, bytes, int]:
    """Reassemble, decrypt and CRC-check one frame written by the device.

    Returns ``(seq_num, response_to, code, data, security_flag)``.
    """
    encrypted = reassemble(packets)
    security_flag = encrypted[0]
    cipher = AES.new(keys[security_flag], AES.MODE_CBC, encrypted[1:17])
    raw = cipher.decrypt(encrypted[17:])
    seq_num, response_to, code, length = unpack(">IIHH", raw[:12])
    end = 12 + length
    assert unpack(">H", raw[end : end + 2])[0] == crc16(raw[:end])
    return seq_num, response_to, code, raw[12:end], security_flag


def v4_advertisement(
    uuid: bytes = b"1234567890abcdef",
    service: bytes = b"\x49\x00\x00\x08abcdefgh",
) -> AdvertisementData:
    """Build a synthetic bound protocol-4 FD50 identity advertisement."""
    key = hashlib.md5(service, usedforsecurity=False).digest()
    encrypted = AES.new(key, AES.MODE_CBC, key).encrypt(uuid)
    return cast(
        AdvertisementData,
        FakeAdvertisementData(
            service_data={SERVICE_UUID_V4: service},
            manufacturer_data={MANUFACTURER_DATA_ID: b"\x00\x00\x01\x00" + encrypted},
        ),
    )


def device_info_data() -> bytes:
    """Build a well-formed FUN_SENDER_DEVICE_INFO payload."""
    data = bytearray(46)
    data[0] = 1  # device version
    data[1] = 2  # protocol version
    data[2] = 3  # bound protocol version
    data[3] = 0
    data[4] = 5  # flags
    data[5] = 1  # bound
    data[6:12] = b"abcdef"  # random srand
    data[12] = 9  # hardware version
    data[13] = 8
    data[14:46] = b"B" * 32  # auth key
    return bytes(data)


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
