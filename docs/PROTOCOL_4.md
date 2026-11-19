# Tuya BLE protocol 4 connection and datapoint reports

<!-- cspell:ignore Raykube frankhommers -->

[BLE_ADVERTISEMENT_DECODING.md](BLE_ADVERTISEMENT_DECODING.md) covers how an
FD50 advertisement identifies a protocol-4 device. This page covers what happens
after that: the GATT characteristics, the handshake, and the datapoint reports
the device pushes. The frame layouts below come from the Tuya BLE SDK source,
not from observed traffic alone, and are implemented strictly: a frame that does
not match is logged and dropped, not guessed at. The one exception is key
derivation, noted below.

Protocol 4 supports datapoint reports and acknowledged control writes (`0x0027`).
Setup automatically verifies connection compatibility before creating a new v4
entry. This does not claim support for every firmware or encryption scheme.

## Automatic setup and reboot recovery

After the QR login and device selection, the existing verification progress
screen tests a bounded set of read/authentication-only profiles. There is no
opt-in setting and setup never sends a motor, valve, reset or unbind command.
Each attempt uses a fresh connection and ephemeral session keys, with a
12-second overall deadline and bounded, confirmed cleanup before the next one.

| Saved profile | Initial device-info marker | ATT fragment capacity                                     |
| ------------- | -------------------------- | --------------------------------------------------------- |
| `v4_20`       | `0x40`                     | 20 bytes (existing sensor behaviour)                      |
| `v2_20`       | `0x20`                     | 20 bytes                                                  |
| `v4_mtu`      | `0x40`                     | Negotiated write-without-response capacity, capped at 244 |
| `v2_mtu`      | `0x20`                     | Negotiated write-without-response capacity, capped at 244 |
| `v2_app`      | `0x20`                     | 20 bytes, announcing the Tuya app's `00 f3` capacity      |

The two-byte device-info payload advertises the selected safe data capacity.
Only `v2_app`, tried last, sends the Tuya app's hardcoded `00 f3`: the
[ha-tuya-ble YZD02B support](https://github.com/ha-tuya-ble/ha_tuya_ble/pull/256)
and the Raykube capture show TuyaOS FD50 firmware expecting it. Larger profiles are rejected without sending packets
if the backend cannot provide a capacity above 20 bytes within a bounded
two-second negotiation wait. Capacity is re-read every 50 ms; disconnect,
shutdown and cancellation stop the wait without sending a handshake. After device-info,
the firmware's reported protocol version controls subsequent frame markers.

A profile is saved only after encrypted device-info, accepted pairing, and a
status report containing a mapped datapoint. A cloud UUID match, GATT write
success, or pairing reply alone is not enough. When no profile verifies, setup
shows the error and offers to add the device anyway. Such an entry has no saved
profile, uses the default handshake and may stay unavailable; it exists so its
diagnostics can be collected. Unbound devices are never provisioned: the bound
flag in the encrypted device-info reply is checked before any pair request,
because FD50 firmware does not reliably advertise its bound state. Bind devices
in Smart Life first and close the app before verification. Devices with no known
datapoint mapping cannot be verified this way.

The saved profile lives with the credentials in config-entry data. Runtime
restores the v4 handshake even before an advertisement callback arrives after
a reboot, without logging into the cloud. Capacity is recalculated and session
keys are regenerated for every connection. Disconnects immediately fail pending
requests; verified profiles use three connection attempts per batch followed by
reconnect backoff, capped at 60 seconds. Failed writes retain datapoint rollback.

For an existing entry created before profile detection, use the integration's
**Reconfigure** menu. It tests automatically, preserves credentials/schema and
entity IDs, and restores runtime ownership after success, failure or cancellation.
Existing entries without a saved profile retain their previous handshake defaults.

The compatibility variants have real-device precedent in
[Raykube FD50 app-traffic notes](https://github.com/ShonP40/Tuya-BLE/blob/master/docs/raykube-a1-ultra-fd50.md).
That is not evidence that a particular variant works on the `jetlwvfm` shade.

## GATT characteristics

The SDK's GATT table puts protocol 4 on the FD50 service, with notify
`00000002-0000-1001-8001-00805f9b07d0` and write
`00000001-0000-1001-8001-00805f9b07d0`. Protocol 2/3 devices use the classic
`00002b10`/`00002b11` pair.

For a protocol-4 device the integration looks both FD50 characteristics up in
the connected device's GATT services and uses them only when both are present,
otherwise the classic pair. The choice is made again on every connection and is
logged at debug level. Protocol 2/3 devices skip the lookup entirely.

Notifications on the FD50 characteristic use BlueZ's `StartNotify` rather than
`AcquireNotify`, as the YZD02B and Raykube integrations found necessary. Other
backends, such as ESPHome proxies, ignore the option.

## Which protocol version is used

The handshake is chosen before the first byte is sent, so the version has to be
known in advance. It comes from the FD50 advertisement, and after a successful
device-info exchange from byte 2 of the device's reply.

The firmware splits its identity over two packets
([`tuya_ble_main.c:395-431`](https://github.com/hjytry/tuya-ble-sdk/blob/815c07120e96372e8746e9d01a21381a8fdd8655/sdk/src/tuya_ble_main.c#L395-L431)):

- The **advertisement** carries the flags, the FD50 service UUID and the FD50
  service data. The service data starts with the identity record: the protocol
  version in the high nibble of byte 0, the identity-record bit (`0x01`), the
  bound flag (`0x08`), then the product type, length and id.
- The **scan response** carries the manufacturer data (`0x07D0`) with the
  encrypted device identity.

Only an active scan receives scan responses. Home Assistant scans passively by
default and merges the two packets once both have been seen. On an SRB-PM01
behind an ESPHome proxy, no scan response arrived for more than 30 minutes
after a restart, across two of Home Assistant's own active-scan windows.

Discovery and setup still require the full identity. It is what matches the
device to its cloud credentials. After setup, a device with stored credentials
chooses its handshake from the identity record alone: the record checks are
the same as for the full decode, and the device's identity comes from the
credentials. A device without stored credentials still needs the scan
response.

The integration never runs a handshake while the stored FD50 advertisement is
unusable: a beacon frame, another protocol version, or malformed data.

- Before connecting, it does not connect at all.
- If such an advertisement arrives while connecting, it closes that link before
  sending anything. Setup builds the device before any advertisement may have
  been seen, so the first connection can start without one.
- Either way it waits, and decodes every new advertisement. The first usable one
  starts the connection, and the status is requested as usual once connected.
  Failed identity connections retry with backoff even when advertisements are
  unchanged. Stopping the device cancels these retries and retries cleanup of any
  unused link whose earlier disconnect failed.

The advertisement callback alone does not deliver every advertisement. Home
Assistant skips it for packets whose data has not changed, and while another
scanner is the device's preferred source, it stores a connectable packet without
passing it on. After a restart the integration once connected without ever
having been given an advertisement. So before connecting, and again once a
connection is established, the device reads the newest connectable advertisement
Home Assistant has stored (`bluetooth.async_last_service_info`).

Classic advertisements never wait.

## Handshake

| Step                    | Request                                                                             | Device behaviour                                                                                                                           |
| ----------------------- | ----------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| Device info (`0x0000`)  | Login key (security flag 4), payload `00 14`: the app's data length, 20, as 2 bytes | Disconnects unless the payload is exactly 2 bytes. Replies with versions, flags, bound flag and the 6-byte random used for the session key |
| Pair (`0x0001`)         | Session key (flag 5), 44-byte pairing request                                       | A bound device replies `2` and switches the link to the bonded state                                                                       |
| Status query (`0x0003`) | Empty payload: query every datapoint                                                | Replies `0` and hands the query to the device application, which reports its datapoints as `0x8006`/`0x8007`                               |

Some observed firmware reports datapoints without acknowledging the status
query. Verification and runtime with a saved profile do not require that ACK;
verification still requires authenticated datapoints after pairing.

Pairing is required even for a device that is already bound. Until the device
has handled a pair request on the current link, it drops every command except
the handshake ones, including the status query and report acknowledgements.

The frame header (`0x40`), CRC-16/MODBUS and zero padding are the same as in
protocol 3. So is key derivation: the login key is `MD5(local_key[:6])` and the
session key is `MD5(local_key[:6] + random)`. The SDK implements the
cryptography in a closed library, so key derivation is taken from this
integration's protocol-3 code and the reference client below, not from SDK
source.

## Security V2

When verification indicates that a Security V2 key is missing or unusable,
setup offers a one-time mobile-app login before the unverified add step.
It uses `tuya-mobile==1.2.0` to sign into the explicitly selected Tuya Smart
or Smart Life app, then reads `thing.m.device.get` v4.1 for the exact device.
Returned device ID must match; UUID and product ID must also match if present.
The mobile `localKey` and `secKey` replace the QR key as one pair, never mixed
across APIs. The pair must be usable before retrying the normal BLE probe.

The region from the QR gateway determines a fixed mobile endpoint. For email
login the numeric country code is optional and overrides the regional default:
Europe 44, Americas 1, China 86, India 91, Singapore 65. Phone-number login
requires its real dial code. Only the European email default is verified by
our live probe; other regions and shade hardware still need acceptance testing.

Read-only enrichment also requests product metadata
(`thing.m.device.ref.info.list` v5.4) and status (`s.m.dev.dp.get` v1.0).
Those requests are individually bounded and optional failures do not discard
the main record or its key pair. Data is sanitized before persistence and
rechecked on diagnostics export, with depth, collection, node and byte limits.
No reset, binding, key-creation or device-control endpoints are used.

Reconfigure can recover existing entries. A failed recovery retains the old
keys/profile and only stores sanitized metadata; a successful recovery updates
the pair, profile and metadata together. Mobile account passwords/session
tokens are ephemeral. This does not use the standalone research probe's
session-file cache, and diagnostics never initiate mobile logins.
An imported pair remains staged in the flow after a transient BLE failure,
so an explicit Reconfigure retry verifies it without another app login. It is
not persisted until verification succeeds. Optional enrichment shares the
remaining overall deadline but cannot discard a pair already retrieved.

Snapshot schema strings that exceed decoder recursion limits fall back to a
redacted shape with truncation marked. Only recognized structural mapping keys
are retained (including numeric DP identifiers); opaque keys are anonymized
regardless of their length, and the same policy is rechecked on export.

Bit 2 of the FD50 frame control's low byte (service-data byte 1) announces the
"Security V2" channel
([TuyaOpen `ble_mgr.c`](https://github.com/tuya/TuyaOpen/blob/master/src/tuya_cloud_service/ble/ble_mgr.c)).
Such firmware silently drops frames encrypted with the classic flags 4 and 5,
then disconnects about 30 seconds later. It needs the cloud `sec_key`, a 16-byte
key stored beside the local key
([TuyaOpen `ble_cryption.c`](https://github.com/tuya/TuyaOpen/blob/master/src/tuya_cloud_service/ble/ble_cryption.c)):

| Flag | Used for    | Key                                |
| ---- | ----------- | ---------------------------------- |
| 14   | Device info | `MD5(local_key + sec_key)`         |
| 15   | Session     | `MD5(local_key + sec_key + srand)` |

`local_key` is the complete local key and `srand` is the 6-byte random of the
device-info reply. The pair request keeps the classic 44-byte frame: TuyaOpen's
pair handler compares only the leading 16-byte UUID. Framing, reports and writes
are unchanged. A reply with any other security flag is logged at debug level
with the flag it carried, so an unsupported scheme (for example activation key 11) is identifiable.

### Choosing the login

The FD50 advertisement carries two Security V2 bits in frame-control byte 1
([TuyaOS `tuya_ble_main.c`](https://github.com/electron-01/TLSR8250/blob/8af57b976243995ae1bbdf58f77c8766e9c659a4/software/TuyaOS/components/tal_ble_protocol/src/tuya_ble_main.c#L768-L775)):
`0x04` means the firmware supports Security V2 and `0x08` that it is switched
on. Once it is on, the firmware accepts only flags 14, 15 and 16
([`tuya_ble_cryption_v2_command_check`](https://github.com/electron-01/TLSR8250/blob/8af57b976243995ae1bbdf58f77c8766e9c659a4/software/TuyaOS/components/tal_ble_protocol/src/tuya_ble_data_handler.c#L330-L410)).
Firmware that only supports it keeps the classic login until the app
provisions Security V2. Several products, for example `qcrilcpr`, `mknd4lci`,
`gvygg3m8`, `c2twgywo` and `cq7xquhj`, use the classic login although the
cloud returns a `sec_key`
([ha-tuya-ble #318](https://github.com/ha-tuya-ble/ha_tuya_ble/pull/318),
[#331](https://github.com/ha-tuya-ble/ha_tuya_ble/pull/331),
[#341](https://github.com/ha-tuya-ble/ha_tuya_ble/pull/341),
[#355](https://github.com/ha-tuya-ble/ha_tuya_ble/pull/355),
[#359](https://github.com/ha-tuya-ble/ha_tuya_ble/pull/359)). Transport, frame
marker, device-info payload and login key are therefore chosen independently.

Setup verification tries every handshake profile with each login the
advertisement allows, in this order:

| Byte 1 bits             | `sec_key` in cloud | Logins tried     |
| ----------------------- | ------------------ | ---------------- |
| `0x04` and `0x08` (on)  | yes                | Security V2      |
| `0x04` only (supported) | yes                | Classic, then V2 |
| `0x04` clear, or no key | any                | Classic          |

The next login is tried only when the device answered no device-info request
on the previous one. A reply proves the device accepted that key, so a later
failure (for example an unbound device) never leads to another login. A login
is saved only after the same authenticated status read as a profile, together
with the profile (`security_mode`: `classic` or `v2`). A saved classic login
leaves the stored `sec_key` unused; **Reconfigure** tests both again. Entries
saved without a `security_mode` keep using Security V2 exactly when a `sec_key`
is stored, until **Reconfigure** saves a verified login. Unverified entries keep that rule too. Replacing the keys in the
integration options keeps the saved login unless the latest advertisement
rules it out: a classic login on firmware that has switched Security V2 on
becomes `v2`, and without a security key the saved login is removed.

Setup stores the key only for a device announcing Security V2 support with a
`sec_key` of exactly 16 bytes. Diagnostics report whether the cloud record had
one (`cloud_info.sec_key_present`), whether it is stored
(`identity.sec_key_stored`), the saved login (`identity.security_mode`) and
whether the runtime uses Security V2 (`device.security_v2`), never the key
itself.
`cloud_info.fields` names every field of the raw cloud record captured at setup
or refreshed with re-authentication tokens: allowlisted product and version
fields keep their value, every other field shows only its type and size, for
example `<redacted str, 16 chars>`.
The key derivation and the classic pair frame match
[ha-tuya-ble SecKey support](https://github.com/ha-tuya-ble/ha_tuya_ble/pull/255),
validated on a YZD02B irrigation timer. Whether the Home Assistant sharing API
returns the `sec_key` (`sec_key` or `secKey`) has not been confirmed on a
Security V2 shade; the mobile-app API used by
[frankhommers/ha-tuya-ble-access](https://github.com/frankhommers/ha-tuya-ble-access)
returns it.

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

VALUE accepts one, two or four bytes and remains signed, as in protocol 3: the
firmware chooses the width, and the SRB-PM01 reports one- and two-byte values.
BOOL and ENUM use one byte, and ENUM is unsigned. BITMAP accepts one, two or four
bytes. Invalid widths reject the whole report without updates or
acknowledgement. Protocol-3 decoding is unchanged.

Each report is acknowledged with the same code: its 7-byte header followed by a
status byte `0`. The SDK ignores an acknowledgement of any other length. Reports
that set bit 7 of the type byte are not acknowledged.

At debug level, the raw payload of every report is logged before it is parsed,
followed by the usual `Received DP` line for each datapoint.

## Not supported

- Advanced-encryption authentication (`0x0015`–`0x0017`). Device-info flag
  bits 1 and 3 announce it. The flags appear in the `Device info` debug line.
- `0x8013` app local-time requests. They are logged as an unknown message and
  dropped.
- Security V2 provisioning flags 11 and 12, and the flag-16 anomaly unbind.
  They need the device's factory `auth_key`, which the cloud does not provide.
- Any other security mode. A profile does not invent missing credentials.
  When a valid `sec_key` is available, the classic login is never tried on
  firmware announcing Security V2 as on; without one, the classic login is the
  only one tried, and a silent device is reported as missing its key.

## Control writes

The SDK v4 write command `0x0027` carries version `0`, a four-byte big-endian
datapoint sequence, then records with `id (1)`, `type (1)`, `length (2)` and
value bytes. Its six-byte response echoes the five-byte header and adds a
result byte. Both the command/response correlation and echoed header are checked;
non-zero results and timeouts fail the write and restore unconfirmed values.

The `jetlwvfm` cover descriptor marks its control DP as ENUM, including when no
control report has arrived yet. Open/close/stop await the command result before
publishing optimistic movement state. Position control retains its existing
Home Assistant/Tuya percentage conversion.

## Hardware acceptance still required

Synthetic firmware tests are not physical-device validation. For the shade:

1. Bind in Smart Life, close the app, and complete setup or Reconfigure nearby.
2. Confirm a saved profile in diagnostics and live battery/position reports.
3. Test open, close, stop, and target position; check actual motor behaviour.
4. Reload the integration, then reboot HA with the app closed; confirm automatic
   reconnection and the same entity IDs, without a new cloud login.
5. Move the device out of range and back, or power-cycle it; confirm recovery.
6. Stop HA during a connection attempt; confirm the BLE link is released.

For the login choice, on a device announcing Security V2 (`41 04` or `49 0c`
in diagnostics or the debug log):

1. Set up the device with debug logging and note which login verified
   (`Verifying connection profile ... with the classic/v2 login`).
2. Confirm `identity.security_mode` and `device.security_v2` in diagnostics
   match it, and that readings arrive.
3. Reboot HA with the app closed; confirm the same login reconnects.
4. For `41 04`, record whether the classic login was answered; this decides
   whether the classic-first order is correct for that firmware.

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
- [Advertisement and scan response templates](https://github.com/hjytry/tuya-ble-sdk/blob/815c07120e96372e8746e9d01a21381a8fdd8655/sdk/src/tuya_ble_main.c#L395-L431),
  [identity copied into the scan response](https://github.com/hjytry/tuya-ble-sdk/blob/815c07120e96372e8746e9d01a21381a8fdd8655/sdk/src/tuya_ble_main.c#L517-L533)

[svnmsc/tuya-ble-v4](https://github.com/svnmsc/tuya-ble-v4), a standalone client
tested on a protocol-4.2 thermo-hygrometer, follows the same handshake. It sends
an empty report acknowledgement, which the SDK ignores, and it scans for
datapoint offsets heuristically. This integration does neither.

## Hardware status

Verified on two units of one product: the SRB-PM01 soil moisture sensor
(`zwjcy` / `jabotj1z`, firmware 1.2), bound in the Smart Life app and reached
through an ESPHome Bluetooth proxy. Home Assistant discovered both from their
FD50 advertisements, the normal QR-code login matched them to their cloud
credentials, and once connected they reported their datapoints. The first unit
then reported every hour.

Both also connected over protocol 4 after a Home Assistant restart and after an
entry reload, and again after the link dropped. For the first unit no scan
response arrived after a restart. In one restart its connection was established
before it had been given any advertisement, and the stored advertisement
supplied the protocol version. Over sessions of several hours its report
sequence numbers kept increasing and no report was sent twice. The device does
not confirm acknowledgements, so this is the only sign that they are accepted.

The integration keeps the connection open, and the long-term battery impact has
not been assessed. Other protocol-4 firmware may differ, for example in which
GATT characteristics it offers.

Synthetic vectors and an SDK-shaped firmware double cover the handshake and
report parsing in `tests/test_protocol_v4.py`, and the protocol choice from the
advertisement in `tests/test_protocol_v4_identity.py`.
