# Tuya BLE advertisement identity decoding

The decoder handles **classic A201 advertisements (protocol 2/3)** and the
verified **FD50 protocol-4 identity format**. This is identity discovery, not a
claim of complete protocol-4 device operation or support for every encryption
mode.

| Field               | Classic A201                            | Protocol 4, FD50                                                             |
| ------------------- | --------------------------------------- | ---------------------------------------------------------------------------- |
| Protocol version    | Manufacturer byte 1                     | High nibble of service byte 0                                                |
| Bound flag          | Manufacturer byte 0, bit 7              | Service byte 0, bit 3                                                        |
| Service record      | Product type + product bytes            | Frame control (2) + product type (1) + length (1) + product/obfuscated bytes |
| Manufacturer header | 6 bytes                                 | 4 bytes                                                                      |
| AES key and IV      | MD5 of service bytes after product type | MD5 of the complete service record                                           |
| Identity            | AES-CBC decrypted UTF-8 text            | AES-CBC decrypted UTF-8 text                                                 |

FD50 service byte 0, bit 0 must indicate an identity record. Beacon/data packets
must not be interpreted as identity advertisements. Bound advertisements contain
obfuscated product bytes; those bytes are still part of the MD5 input, but are
not exposed as a readable product ID. Type 1 product keys are not product IDs.

Protocol 5 is explicitly rejected: no verified protocol-5 layout or known-device
capture was available for this implementation. Changing the version nibble on a
synthetic protocol-4 packet is not evidence of protocol-5 compatibility. A
protocol-5 capture with its matching cloud UUID or firmware source is needed.

Compressed 20-character identities (manufacturer byte 3, bit 0) are also rejected
until their pairing representation is supported. Truncated records, invalid
lengths/types and undecodable plaintext fail closed. There is no alternate-key
guessing or fallback from FD50 to a possibly stale classic service record.

## Evidence

- [SDK mirror, pinned revision: protocol-4 advertisement construction](https://github.com/hjytry/tuya-ble-sdk/blob/815c07120e96372e8746e9d01a21381a8fdd8655/sdk/src/tuya_ble_main.c#L395-L550):
  the four-byte manufacturer header, frame-control flags and the complete
  service-record encryption input.
- [Official TuyaOpen advertisement construction](https://github.com/tuya/TuyaOpen/blob/master/src/tuya_cloud_service/ble/ble_mgr.c#L132-L205).
- [Official TuyaOpen encryption](https://github.com/tuya/TuyaOpen/blob/master/src/tuya_cloud_service/ble/ble_cryption.c):
  `tuya_ble_rsp_id_encrypt` uses MD5 as both AES-CBC key and IV.
- [Official TuyaOpen protocol definitions](https://github.com/tuya/TuyaOpen/blob/master/src/tuya_cloud_service/ble/ble_protocol.h):
  protocol 4, not protocol 5.

Tests include the supplied captured protocol-3 advertisement and synthetic
protocol-4 vectors constructed using the firmware format. Real protocol-4
hardware/cloud matching remains to be verified.
