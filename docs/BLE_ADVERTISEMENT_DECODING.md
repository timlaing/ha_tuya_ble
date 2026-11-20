# Tuya BLE advertisement identity decoding

The decoder handles **classic A201 advertisements (protocol 2/3)** and the
verified **FD50 protocol-4 identity format**. This is identity discovery, not a
claim of complete protocol-4 device operation or support for every encryption
mode.

| Field               | Classic A201                            | Protocol 4, FD50                                                             |
| ------------------- | --------------------------------------- | ---------------------------------------------------------------------------- |
| Protocol version    | Manufacturer byte 1                     | High nibble of service byte 0                                                |
| Bound flag          | Manufacturer byte 0, bit 7              | Service byte 0, bit 3                                                        |
| Service record      | Product type (0 or 1) + product bytes   | Frame control (2) + product type (1) + length (1) + product/obfuscated bytes |
| Manufacturer header | 6 bytes                                 | 4 bytes                                                                      |
| AES key and IV      | MD5 of service bytes after product type | MD5 of the complete service record                                           |
| Identity            | AES-CBC decrypted UTF-8 text            | AES-CBC decrypted UTF-8 text                                                 |

FD50 frame control (service bytes 0–1) also carries flags that do not change
the identity. The Security V2 bits choose the login; the shared bit is only
reported in diagnostics:

| Byte | Bit    | Meaning                                                       |
| ---- | ------ | ------------------------------------------------------------- |
| 0    | `0x08` | Bound                                                         |
| 0    | `0x04` | Shared device                                                 |
| 1    | `0x04` | Security V2 supported (`security_v2`)                         |
| 1    | `0x08` | Security V2 enabled, keys provisioned (`security_v2_enabled`) |

See [PROTOCOL_4.md](PROTOCOL_4.md#supported-and-enabled) for their source and
effect. Classic A201 records carry none of them.

FD50 service byte 0, bit 0 must indicate an identity record. Beacon/data packets
must not be interpreted as identity advertisements. Bound advertisements contain
obfuscated product bytes; those bytes are still part of the MD5 input, but are
not exposed as a readable product ID. Type 1 product keys are not product IDs.

Both formats accept product type 0 (product ID) and type 1 (product key), with
the same key derivation for each. Classic type-1 records are used by some bound
Wi-Fi+BLE combo bulbs (#158). Other product types are rejected.

Protocol 5 is explicitly rejected: no verified protocol-5 layout or known-device
capture was available for this implementation. Changing the version nibble on a
synthetic protocol-4 packet is not evidence of protocol-5 compatibility. A
protocol-5 capture with its matching cloud UUID or firmware source is needed.

FD50 discovery is validated before requesting cloud login. An incomplete v4
advertisement triggers an active scan for a supported identity; unsupported
formats are excluded from manual discovery. Active scans ignore beacon and
invalid-identity packets rather than ending at the first block-aligned payload.
Fresh FD50 connections also require a usable identity record so their initial
handshake never uses the legacy default protocol version. Once the device has
stored credentials, the record in the advertisement is enough and the scan
response is not needed (see [PROTOCOL_4.md](PROTOCOL_4.md)). An established
session remains usable when a later advertisement is only a beacon.

Manual discovery rebuilds eligibility from the current scanner snapshot, so
vanished and configured addresses are not retained. Candidate identities resolve
concurrently rather than waiting for each incomplete record in turn. Once a
validated, unconfigured device is ready, the selector can appear; unfinished
scans are cancelled and awaited. Deferred v4 candidates remain selectable after
ready entries, so a fast device cannot starve them across retries. Selecting a
deferred address always requires a fresh, decoded identity before cloud
credential lookup or entry creation. Configured addresses are still removed
after cancellation cleanup. Cancelling the parent flow also cleans up its tasks.

Compressed 20-character identities (FD50 protocol-4 manufacturer byte 3, bit 0)
are also rejected
until their pairing representation is supported. Truncated records, invalid
lengths/types and undecodable or non-printable plaintext fail closed. There is no alternate-key
guessing or fallback from FD50 to a possibly stale classic service record.

## Classic header and bound records

The six-byte classic manufacturer header is: flags (bit 7 bound), protocol
version, encryption mode, communication ability (two bytes, big-endian) and data
type. For example `80 03 00 00 01 00` is a bound protocol-3 record with
encryption mode 0, communication ability `0x0001` (register from BLE) and data
type 0. Bytes 2-5 do not mark a compressed identity; the identity is always the
block-aligned ciphertext after the header.

Once a classic device is bound, its A201 record carries obfuscated product
bytes instead of the readable product ID, and the identity is encrypted with the
MD5 of those bytes. Home Assistant merges service and manufacturer data across
advertisements, so a snapshot can pair a stale readable A201 record with a bound
identity (#155). Its identity then does not decrypt, even for a product whose
advertisements otherwise decode.

Setup therefore keeps the active scan running until a classic pair decodes. If
the scan times out, setup uses the last well-formed classic record:

- **Bound**: the record's header supplies only the bound flag, protocol version
  and, for product type 0, a readable product ID. No UUID is looked up or stored
  as advertised. Cloud candidates of that product (all eligible candidates when
  no product ID is readable) are verified by the same authenticated
  device-info, pair and mapped-datapoint probe used for other unmatched bound
  devices. The advertisement alone never selects a cloud record.
- **Unbound**: setup still fails with `identity_not_decoded`.

Strict identity decoding is unchanged and never guesses alternate keys.

## Evidence

- [SDK mirror, pinned revision: protocol-4 advertisement construction](https://github.com/hjytry/tuya-ble-sdk/blob/815c07120e96372e8746e9d01a21381a8fdd8655/sdk/src/tuya_ble_main.c#L395-L550):
  the four-byte manufacturer header, frame-control flags and the complete
  service-record encryption input.
- [TuyaOS BLE frame-control flags, pinned revision](https://github.com/electron-01/TLSR8250/blob/8af57b976243995ae1bbdf58f77c8766e9c659a4/software/TuyaOS/components/tal_ble_protocol/src/tuya_ble_main.c#L736-L775):
  shared (byte 0 `0x04`), Security V2 supported and enabled (byte 1 `0x04`,
  `0x08`). A third-party mirror of Tuya-copyright code.
- [Official TuyaOpen advertisement construction](https://github.com/tuya/TuyaOpen/blob/master/src/tuya_cloud_service/ble/ble_mgr.c#L132-L205).
- [Official TuyaOpen encryption](https://github.com/tuya/TuyaOpen/blob/master/src/tuya_cloud_service/ble/ble_cryption.c):
  `tuya_ble_rsp_id_encrypt` uses MD5 as both AES-CBC key and IV.
- [Official TuyaOpen protocol definitions](https://github.com/tuya/TuyaOpen/blob/master/src/tuya_cloud_service/ble/ble_protocol.h):
  protocol 4, not protocol 5.

Tests include the supplied captured protocol-3 advertisement and synthetic
protocol-4 vectors constructed using the firmware format. Discovery and cloud
matching on real protocol-4 hardware have since been verified on one SRB-PM01;
see [PROTOCOL_4.md](PROTOCOL_4.md#hardware-status).

The connection handshake and datapoint reports that follow discovery are
described in [PROTOCOL_4.md](PROTOCOL_4.md).
