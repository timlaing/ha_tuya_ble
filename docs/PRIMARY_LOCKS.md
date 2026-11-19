# Primary lock descriptors (#166)

All four products are **experimental in this integration**. Source contributors
reported real-device tests; our regression tests mock Bluetooth and cloud and are
not physical-device proof. There are no `jtmspro` category defaults to inherit.

## Evidence and DP inventory

Sources: ha-tuya-ble/ha_tuya_ble [PR315](https://github.com/ha-tuya-ble/ha_tuya_ble/pull/315)
(@steven-ahfu), [PR353](https://github.com/ha-tuya-ble/ha_tuya_ble/pull/353)
(@GeorgeBark), [PR339](https://github.com/ha-tuya-ble/ha_tuya_ble/pull/339)
(@rairulyle), [PR360](https://github.com/ha-tuya-ble/ha_tuya_ble/pull/360)
(@albertgoca93). Reviewed their patches, bodies and discussions; there were no
inline review comments. PR339 includes the cloud model. PR353 describes app HCI
capture and hardware observations; PR315 and PR360 describe owner tests.

`value` = BLE DT_VALUE (2), `bool` = DT_BOOL (1), `enum` = DT_ENUM (4),
`raw` = DT_RAW (0). Numeric coefficient is 1 throughout: no scaling conversion.
`—` means no unit; `unknown` means the source did not provide that bound.
Enum indices follow the source BLE platform mappings, not a tuya-local list.
Sensors deliberately omit the inert YAML `dp_type`: they decode the type reported
by the device. Their evidenced wire types are recorded below instead.

| Product / source         | DP                         | Cloud code                                                                                                                              | Wire type | Unit | Range / values                     | Access                       |
| ------------------------ | -------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- | --------- | ---- | ---------------------------------- | ---------------------------- |
| EL605A `2hmqh0ty` / 315  | 8                          | unknown (battery mapping; code not captured)                                                                                            | value     | %    | unknown                            | read                         |
| EL605A / 315             | 71                         | ble_unlock_check                                                                                                                        | raw       | —    | empty bytes trigger                | write                        |
| 8052F66 `dappsdq4` / 353 | 8                          | unknown (battery mapping; code not captured)                                                                                            | value     | %    | unknown                            | read                         |
| 8052F66 / 353            | 12, 13, 14, 15, 19, 55, 62 | unlock_fingerprint, unlock_password, unlock_dynamic, unlock_card, unlock_ble, unlock_temporary, unlock_phone_remote (respectively)      | value     | —    | user IDs; bounds unknown           | read                         |
| 8052F66 / 353            | 21                         | alarm_lock                                                                                                                              | enum      | —    | indices 0–13; YAML option order    | read                         |
| 8052F66 / 353            | 31                         | beep_volume                                                                                                                             | enum      | —    | 0 mute, 1 low, 2 normal, 3 high    | read/write                   |
| 8052F66 / 353            | 33                         | automatic_lock                                                                                                                          | bool      | —    | true automatic / false passage     | read/write                   |
| 8052F66 / 353            | 34                         | unlock_switch                                                                                                                           | enum      | —    | 0 single_unlock, 1 combination     | read/write                   |
| 8052F66 / 353            | 36                         | auto_lock_time                                                                                                                          | value     | s    | 5–20; step 1                       | read/write                   |
| 8052F66 / 353            | 71                         | ble_unlock_check                                                                                                                        | raw       | —    | timestamped 19-byte command        | write (response reported)    |
| C-014S `djrqe0q6` / 339  | 8                          | residual_electricity                                                                                                                    | value     | %    | 0–100                              | read                         |
| C-014S / 339             | 12, 13, 15, 19, 55, 62, 63 | unlock_fingerprint, unlock_password, unlock_card, unlock_ble, unlock_temporary, unlock_phone_remote, unlock_voice_remote (respectively) | value     | —    | user IDs; bounds unknown           | read                         |
| C-014S / 339             | 21                         | alarm_lock                                                                                                                              | enum      | —    | indices 0–14; YAML option order    | read                         |
| C-014S / 339             | 24                         | doorbell                                                                                                                                | bool      | —    | false/true                         | read; **deferred to #172**   |
| C-014S / 339             | 31                         | beep_volume                                                                                                                             | enum      | —    | 0 mute, 1 low, 2 normal, 3 high    | read/write                   |
| C-014S / 339             | 33                         | automatic_lock                                                                                                                          | bool      | —    | false/true                         | read/write                   |
| C-014S / 339             | 34                         | unlock_switch                                                                                                                           | enum      | —    | 0 single_unlock, 1 combination     | read/write                   |
| C-014S / 339             | 36                         | auto_lock_time                                                                                                                          | value     | s    | 3–600; step 1                      | read/write                   |
| C-014S / 339             | 46                         | manual_lock                                                                                                                             | bool      | —    | true locks; false does not unlock  | read/write; only true sent   |
| C-014S / 339             | 47                         | lock_motor_state                                                                                                                        | bool      | —    | true released/moving, false locked | read                         |
| C-014S / 339             | 71                         | ble_unlock_check                                                                                                                        | raw       | —    | empty bytes trigger                | read/write; only unlock sent |
| CTL20 `tkbdvrzr` / 360   | 71                         | ble_unlock_check                                                                                                                        | raw       | —    | timestamped 19-byte command        | write (response reported)    |

Other raw credential-management DPs from PR339 are not exposed. DP71 is listed
in `sensitive_dp_ids` on **every** new product, including empty-trigger products:
reports can contain sensitive data even when our outgoing trigger is empty.
Setup applies this list to the protocol logger; diagnostics withhold values and
raw bytes. Timestamped commands use the stored check's member ID and the current
time, fail before writing when the check is missing/invalid, and await the write.
The cloud check is fetched on initial setup by the inherited #195 implementation;
an older entry may need removal and re-addition. Do not post it in diagnostics.

## Identity and limitations

Source keys (`lock`, `bluetooth_unlock`, `battery`, `automatic_lock`,
`auto_lock_time`, `beep_volume`, `unlock_switch` and unlock-method sensor keys)
are unchanged, so no legacy aliases are needed. Existing registry entries keep
their IDs. Existing products and handler behavior are unchanged.

8052F66 and C-014S lock state uses only the last **device-reported** value.
An acknowledged local write is not itself a report: state stays unknown until
the first report, or keeps the last confirmed state while a command is pending.
C-014S uses the reusable `lock.state.reported_bolt` reader for its verified DP47.
Incoming DP71 data and the cloud unlock check remain withheld from diagnostics,
including #209's mobile snapshot storage/export paths inherited from #195.

- EL605A gets its verified battery and empty-trigger unlock button only. PR315
  refers to `manual_lock` (DP46) but does not establish a numeric state DP:
  **please supply redacted EL605A diagnostics containing functions/status_range
  or local_schema, plus a lock/unlock state report** before adding a lock entity.
  Do not include keys, tokens, user codes, endpoints or DP71 payload values.
- 8052F66's lock state is **automatic-lock mode**, not the physical handle state.
  Open briefly releases the handle without switching to passage mode. Local
  fingerprint/password entry does not make this lock entity temporarily unlocked.
- C-014S has no duplicate unlock button. DP24 doorbell, events, replay filtering
  and aggregate last-unlock state are pending #172.
- CTL20 has no reliable lock-state DP and therefore **only an unlock button**.
  Copied CTL20H sensors/switches are deferred pending redacted CTL20 schema and
  reports; no actuator or enum ordering is inferred from that similar product.
- Handshake quirks are not hardcoded into platforms or BLE product-ID lists.
  Existing protocol-v4 profile detection handles FD50/app-capacity and negotiated
  MTU variants. Source required `00f3` for EL605A/C-014S/CTL20, `00f4` and V2 keys
  for 8052F66, and classic keys for CTL20. The negotiated-capacity profile can
  advertise `00f4` only when the transport actually allows 244-byte writes.
  **No descriptor tests prove these handshakes on these locks**; if discovery
  cannot negotiate their profile, collect redacted handshake diagnostics rather
  than forcing transport/security settings or claiming support is proven.

## Still needs physical verification on this integration

For all four: initial setup, reconnect after sleeping/outage, key/profile
selection, incoming report ordering, live DP71 redaction, failed-command error
propagation and **no automatic replay** of an unlock after failure.

For EL605A: battery and repeated empty-trigger button presses.
For C-014S: DP46 lock, repeated DP71 unlock, DP47 state/polarity, battery, each
alarm/user-ID report, DP33 automatic-lock switch, both enum selects and DP36
minimum/maximum writes and readback.
For 8052F66: DP33 lock/unlock/passage polarity, momentary timestamped Open for
different app members, battery, each alarm/user-ID report, both selects and
DP36 minimum/maximum writes and readback; confirm Open does not change DP33.
For CTL20: timestamped button for different app members, sleep/wake and rejection.
Hardware tests should be performed with safe physical access and no risk of
locking anyone out. HTTP success or mocked writes are not evidence of movement.
