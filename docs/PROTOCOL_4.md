# Tuya BLE protocol 4 connection and datapoint reports

[BLE_ADVERTISEMENT_DECODING.md](BLE_ADVERTISEMENT_DECODING.md) covers how an
FD50 advertisement identifies a protocol-4 device. This page covers what happens
after that: the GATT characteristics, the handshake, and the datapoint reports
the device pushes. The frame layouts below come from the Tuya BLE SDK source,
not from observed traffic alone, and are implemented strictly: a frame that does
not match is logged and dropped, not guessed at. The one exception is key
derivation, noted below.

Protocol 4 support is **read-only**. Datapoint writes (`0x0027`) are not
implemented, so `send_datapoints` still raises for any protocol other than 3.

## GATT characteristics

The SDK's GATT table puts protocol 4 on the FD50 service, with notify
`00000002-0000-1001-8001-00805f9b07d0` and write
`00000001-0000-1001-8001-00805f9b07d0`. Protocol 2/3 devices use the classic
`00002b10`/`00002b11` pair.

For a protocol-4 device the integration looks both FD50 characteristics up in
the connected device's GATT services and uses them only when both are present,
otherwise the classic pair. The choice is made again on every connection and is
logged at debug level. Protocol 2/3 devices skip the lookup entirely.

## Which protocol version is used

The handshake is chosen before the first byte is sent, so the version has to be
known in advance. It comes from the decoded FD50 identity advertisement, and
after a successful device-info exchange from byte 2 of the device's reply.
Home Assistant may build the device before it has seen any advertisement, for
example right after a restart. To cover that, an FD50 advertisement that has
arrived in the meantime is decoded again at the start of every connection
attempt. Classic advertisements are not re-decoded.

## Handshake

| Step                    | Request                                                                             | Device behaviour                                                                                                                           |
| ----------------------- | ----------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| Device info (`0x0000`)  | Login key (security flag 4), payload `00 14`: the app's data length, 20, as 2 bytes | Disconnects unless the payload is exactly 2 bytes. Replies with versions, flags, bound flag and the 6-byte random used for the session key |
| Pair (`0x0001`)         | Session key (flag 5), 44-byte pairing request                                       | A bound device replies `2` and switches the link to the bonded state                                                                       |
| Status query (`0x0003`) | Empty payload: query every datapoint                                                | Replies `0` and hands the query to the device application, which reports its datapoints as `0x8006`/`0x8007`                               |

Pairing is required even for a device that is already bound. Until the device
has handled a pair request on the current link, it drops every command except
the handshake ones, including the status query and report acknowledgements.

The frame header (`0x40`), CRC-16/MODBUS and zero padding are the same as in
protocol 3. So is key derivation: the login key is `MD5(local_key[:6])` and the
session key is `MD5(local_key[:6] + random)`. The SDK implements the
cryptography in a closed library, so key derivation is taken from this
integration's protocol-3 code and the reference client below, not from SDK
source.

## Datapoint reports

`0x8006` (`FUN_RECEIVE_DP_V4`) and `0x8007` (`FUN_RECEIVE_TIME_DP_V4`) start with
a 7-byte header:

| Offset | Size | Field                                                                          |
| ------ | ---- | ------------------------------------------------------------------------------ |
| 0      | 1    | Version, always 0. Any other value is rejected                                 |
| 1      | 4    | Report sequence number, big-endian                                             |
| 5      | 1    | Type: 0 active, 1 passive. Bit 7 set means the device wants no acknowledgement |
| 6      | 1    | Mode (0–3)                                                                     |

`0x8007` then carries a time block: a type byte followed by either a 13-byte
millisecond string (type 0) or a 4-byte Unix timestamp (type 1). This is the same
encoding the protocol-3 timed report uses, so the device's timestamp is stored
with the datapoints.

The datapoint list follows. Each entry is `dp_id (1)`, `dp_type (1)`, a
**2-byte** big-endian length, then the value. Protocol 3 uses a 1-byte length
here. The list must contain at least one entry and end exactly on an entry
boundary. All entries are decoded before any datapoint is stored, so a malformed
report changes nothing.

Each report is acknowledged with the same code: its 7-byte header followed by a
status byte `0`. The SDK ignores an acknowledgement of any other length. Reports
that set bit 7 of the type byte are not acknowledged.

At debug level, the raw payload of every report is logged before it is parsed,
followed by the usual `Received DP` line for each datapoint.

## Not supported

- Datapoint writes (`0x0027`).
- Advanced-encryption authentication (`0x0015`–`0x0017`). Device-info flag
  bits 1 and 3 announce it. The flags appear in the `Device info` debug line.
- `0x8013` app local-time requests. They are logged as an unknown message and
  dropped.

## Evidence

All links point at the SDK mirror revision that
[BLE_ADVERTISEMENT_DECODING.md](BLE_ADVERTISEMENT_DECODING.md) also cites
(protocol version 4.2).

- [GATT table](https://github.com/hjytry/tuya-ble-sdk/blob/815c07120e96372e8746e9d01a21381a8fdd8655/README.md#L60-L67)
- [Device-info request length check](https://github.com/hjytry/tuya-ble-sdk/blob/815c07120e96372e8746e9d01a21381a8fdd8655/sdk/src/tuya_ble_data_handler.c#L482-L510)
- [Pair handler: bound device replies 2 and becomes bonded](https://github.com/hjytry/tuya-ble-sdk/blob/815c07120e96372e8746e9d01a21381a8fdd8655/sdk/src/tuya_ble_data_handler.c#L945-L950)
- [Commands dropped before bonding](https://github.com/hjytry/tuya-ble-sdk/blob/815c07120e96372e8746e9d01a21381a8fdd8655/sdk/src/tuya_ble_data_handler.c#L430-L437)
- [Status query reply](https://github.com/hjytry/tuya-ble-sdk/blob/815c07120e96372e8746e9d01a21381a8fdd8655/sdk/src/tuya_ble_data_handler.c#L1986-L2003)
- [`0x8006` report envelope](https://github.com/hjytry/tuya-ble-sdk/blob/815c07120e96372e8746e9d01a21381a8fdd8655/sdk/src/tuya_ble_api.c#L594-L666)
- [`0x8007` report envelope](https://github.com/hjytry/tuya-ble-sdk/blob/815c07120e96372e8746e9d01a21381a8fdd8655/sdk/src/tuya_ble_api.c#L676-L761)
- [2-byte-length datapoint list](https://github.com/hjytry/tuya-ble-sdk/blob/815c07120e96372e8746e9d01a21381a8fdd8655/sdk/src/tuya_ble_mutli_tsf_protocol.c#L677-L770)
- [Report acknowledgement handlers](https://github.com/hjytry/tuya-ble-sdk/blob/815c07120e96372e8746e9d01a21381a8fdd8655/sdk/src/tuya_ble_data_handler.c#L2306-L2394)
- [Report version constant](https://github.com/hjytry/tuya-ble-sdk/blob/815c07120e96372e8746e9d01a21381a8fdd8655/sdk/include/tuya_ble_internal_config.h#L41)
- [CRC-16](https://github.com/hjytry/tuya-ble-sdk/blob/815c07120e96372e8746e9d01a21381a8fdd8655/sdk/src/tuya_ble_utils.c#L347-L364)

[svnmsc/tuya-ble-v4](https://github.com/svnmsc/tuya-ble-v4), a standalone client
tested on a protocol-4.2 thermo-hygrometer, follows the same handshake. It sends
an empty report acknowledgement, which the SDK ignores, and it scans for
datapoint offsets heuristically. This integration does neither.

## Hardware status

Verified on one device: an SRB-PM01 soil moisture sensor (`zwjcy` /
`jabotj1z`), bound in the Smart Life app and reached through an ESPHome
Bluetooth proxy. Home Assistant discovered it from its FD50 advertisement, the
normal QR-code login matched it to its cloud credentials, and once connected it
reported plausible datapoint values. The 8-byte report acknowledgement described
above was verified on this device. The long-term battery impact has not been
assessed, and other protocol-4 firmware may differ, for example in which GATT
characteristics it offers.

Synthetic vectors and an SDK-shaped firmware double cover the handshake and
report parsing in `tests/test_protocol_v4.py`.
