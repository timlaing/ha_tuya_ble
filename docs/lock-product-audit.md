# Additional lock descriptor audit (#179)

All fourteen products are **experimental**. Registering a product does not prove
that it can connect, unlock, or report its physical bolt position. Metadata-only
products expose no mapped entities (the integration may still offer RSSI).
Existing products and their identifiers are unchanged.

The baseline is shared-handler PR #195, parent
`f48557f7efeb009d8e9327561920fdadc027e8e2`. Source patches/discussions were audited
in `ha-tuya-ble/ha_tuya_ble`, with source snapshot
`99d8b1bdd377e1f4bc447cb2a75e6692ff2d135e`.

Current stacked parent: `1db65b733133d313ccc377210ae7acb211909860` (#195,
including #187 reported-value semantics and #209 mobile metadata privacy).
Only this issue's commits were replayed. Lock handlers use the last
device-reported values, not optimistic local writes: sending Lock, Unlock or
Open cannot confirm the physical state. Mobile credential/snapshot code is
inherited unchanged; no unlock-check content enters mobile metadata.

## Disposition by product

| Category/product   | Source PRs / issues                                | Disposition and missing evidence                                                                                                                                                                                                                                                                      |
| ------------------ | -------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ms/0qxp5u7s`      | PR324, PR327 (@way-lo)                             | Pulido PLD_P130: inherited passage/bolt state and lock/unlock/open handlers; battery and fingerprint/Bluetooth user-number diagnostics. No manual-lock switch (off does nothing), volume (unsupported), or unverified alarm enum order.                                                               |
| `jtmspro/kholoaew` | PR155 (@Test-subj)                                 | H13: tested battery, password user number, motor diagnostic, auto-lock switch, volume/language selects, lock-only DP46 button. No lock entity or DP71 unlock: contributor explicitly could not unlock. Alarm range in diagnostics differs from the borrowed patch range, so omitted.                  |
| `ms/uyf1ewof`      | PR274, PR280 (@flo-m)                              | Pirnar Securosmart: correct category/manufacturer (not “Example Product”), read-only battery, fingerprint/Bluetooth user numbers, motor, hijack and automatic-lock diagnostics. DP6 is RAW in supplied status, but patch sends BOOL. No commands until this contradiction is resolved.                |
| `jtmspro/y2yaegze` | PR166, issue154                                    | CTL20H: schema-backed battery, card/Bluetooth/remote user numbers and read-only automatic-lock diagnostic. Owner reported settings non-functional; no writable settings or unlock. No truthful bolt state is available.                                                                               |
| `jtmspro/qcrilcpr` | PR305, PR318, issue304 (@eric1905, @SittingDuck52) | CTL20H_8018: battery only, reported working after source handshake fixes. DP71 still did not move the bolt. Other IDs must not be copied merely because codes match CTL20H. Source FD50 framing/legacy-security connection quirks are not ported here; connection can still fail in this integration. |
| `ms/7a4xvbtt`      | PR185 (credits @jpmreis)                           | Lock P1 metadata only. Patch only extends borrowed groups; need this product's BLE types/IDs, motor polarity, unlock payload and working command reports.                                                                                                                                             |
| `ms/6fibxtph`      | PR226, issue223                                    | Primebras Athenas metadata only. Mapping copied from tuya-local; need BLE schema/types, enum indices and lock/unlock evidence.                                                                                                                                                                        |
| `ms/99gv5nmz`      | PR226, issue224                                    | Foxgard metadata only; same evidence gaps as Athenas, to be verified independently.                                                                                                                                                                                                                   |
| `ms/kpn4zaf7`      | PR234, issue205                                    | BSTUOKEY metadata only. Need DP9 BLE type and enum byte order; patch assumes literal strings. Need actual DP71 payload and motor/manual-lock semantics, not generic fallback behaviour.                                                                                                               |
| `ms/wgv4haro`      | PR252, PR272, issue251                             | Guard Dog BS_PLD01 metadata only. Owner confirms momentary unlock only, no remote lock or held unlock. Cloud calls DP6 RAW whereas patch sends BOOL. Need numbered local strategy and working BLE trigger capture before offering an open button. Never invent a lock state.                          |
| `jtmspro/hc7n0urm` | PR190, issue120 (credits @khseal)                  | A1 Ultra-JM metadata only. Patch adds generic DP71 button without verified payload/type. Need working open capture and BLE volume/alarm enum indices.                                                                                                                                                 |
| `jtmspro/pyawczjj` | PR189                                              | CS-9 metadata only. Borrowed sensor/select groups and lock flag do not establish product-specific schema or successful unlocking.                                                                                                                                                                     |
| `jtmspro/yfqp0shy` | PR230 (credits @deg3n)                             | Gainsborough Liberty GGC01HA metadata only. Need numbered BLE schema, persistent versus momentary state, motor polarity, and successful lock/unlock/open payloads. Generic manual-lock off path is not proof of unlocking.                                                                            |
| `jtmspro/qicggi0m` | PR246, issue219                                    | XCase NX-4964 metadata only. tuya-local-based sensor list and lock flag are not BLE evidence. Need numbered local schema and working unlock trigger; no bolt state is established.                                                                                                                    |

PRs 185/226/234/252/272/166/305/190/189/230/246 were adapted by
@CloCkWeRX. Generated tests, successful cloud HTTP responses and PR claims are
not physical-device evidence. Only contributor reports explicitly describing
device behaviour are treated as such.

## Active DP evidence ledger

Wire types: BOOL=1, VALUE=2, ENUM=4. Scaling “1” means unchanged.
R = exposed read-only; W = exposed command; R/W = exposed setting.
“Unspecified” is intentionally not a guessed range or unit.
Sensors decode the reported wire type; they do not declare an inert `dp_type`.

| Product    | DP  | Cloud code / meaning                     | Wire  | Unit; scaling; range                                | Access / evidence                                            |
| ---------- | --- | ---------------------------------------- | ----- | --------------------------------------------------- | ------------------------------------------------------------ |
| `0qxp5u7s` | 6   | `bluetooth_unlock`                       | BOOL  | none; 1; true trigger                               | W, momentary (~3 s), PR324                                   |
| `0qxp5u7s` | 8   | `residual_electricity` / battery         | VALUE | %; 1; source battery percentage, limits unspecified | R, PR324                                                     |
| `0qxp5u7s` | 12  | `unlock_fingerprint`                     | VALUE | user number; 1; unspecified                         | R, PR324                                                     |
| `0qxp5u7s` | 19  | `unlock_ble`                             | VALUE | user number; 1; unspecified                         | R, PR324                                                     |
| `0qxp5u7s` | 33  | `automatic_lock` (actually free passage) | BOOL  | none; 1; false/true                                 | R/W, true holds open, PR324/327                              |
| `0qxp5u7s` | 46  | `manual_lock`                            | BOOL  | none; 1; true trigger only                          | W, also ends passage, PR324                                  |
| `0qxp5u7s` | 47  | `lock_motor_state`                       | BOOL  | none; 1; false/true                                 | R, true unlocked, change-only reports, PR327                 |
| `kholoaew` | 8   | `residual_electricity`                   | VALUE | %; 1; 0–100 step 1                                  | R, PR155 status + schema + tested battery                    |
| `kholoaew` | 13  | `unlock_password`                        | VALUE | user number; 1; 0–999 step 1                        | R, PR155 schema + tested password reading                    |
| `kholoaew` | 28  | `language`                               | ENUM  | none; 1; 0 Chinese simplified, 1 English            | R/W, PR155 schema + tested select                            |
| `kholoaew` | 31  | `beep_volume`                            | ENUM  | none; 1; 0 mute, 1 low, 2 normal, 3 high            | R/W, PR155 schema + tested select                            |
| `kholoaew` | 33  | `automatic_lock`                         | BOOL  | none; 1; false/true                                 | R/W, PR155 schema + tested setting                           |
| `kholoaew` | 46  | `manual_lock`                            | BOOL  | none; 1; true trigger                               | W, PR155 tested lock button                                  |
| `kholoaew` | 47  | `lock_motor_state`                       | BOOL  | none; 1; false/true                                 | R diagnostic, PR155 tested; not claimed as bolt truth        |
| `uyf1ewof` | 8   | `residual_electricity`                   | VALUE | %; 1; limits unspecified                            | R, PR274 status + battery mapping                            |
| `uyf1ewof` | 12  | `unlock_fingerprint`                     | VALUE | user number; 1; unspecified                         | R, PR274 status                                              |
| `uyf1ewof` | 19  | `unlock_ble`                             | VALUE | user number; 1; unspecified                         | R, PR274 status                                              |
| `uyf1ewof` | 22  | `hijack`                                 | BOOL  | none; 1; false/true                                 | R, PR274 status                                              |
| `uyf1ewof` | 33  | `automatic_lock`                         | BOOL  | none; 1; false/true                                 | R only, PR274 status; write semantics unconfirmed            |
| `uyf1ewof` | 47  | `lock_motor_state`                       | BOOL  | none; 1; false/true                                 | R diagnostic only, PR274 status                              |
| `y2yaegze` | 8   | `residual_electricity`                   | VALUE | %; 1; limits unspecified                            | R, issue154 numbered status                                  |
| `y2yaegze` | 15  | `unlock_card`                            | VALUE | user number; 1; unspecified                         | R, issue154 numbered status                                  |
| `y2yaegze` | 19  | `unlock_ble`                             | VALUE | user number; 1; unspecified                         | R, issue154 numbered status                                  |
| `y2yaegze` | 33  | `automatic_lock`                         | BOOL  | none; 1; false/true                                 | R only, issue154 status; owner says settings do not work     |
| `y2yaegze` | 62  | `unlock_phone_remote`                    | VALUE | user number; 1; unspecified                         | R, issue154 numbered status                                  |
| `qcrilcpr` | 8   | `residual_electricity`                   | VALUE | %; 1; 0–100 step 1                                  | R, PR305 battery mapping + issue304 live 92/88%, cloud specs |

## Deferred DP candidates (not enabled)

These are **patch proposals**, not verified BLE mappings. Cloud-code names
describe the proposal; every unconfirmed type, range or access mode remains
unconfirmed. No product inherits another product's commands.

| Products / source              | Proposed DPs and metadata                                                                                                                                                                                                                             | Missing evidence                                                                                                                     |
| ------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `7a4xvbtt` PR185               | 8 battery (%), 21 alarm_lock, 31 beep_volume, 40 door status, 47 motor state (borrowed groups); generic lock path writes 46 manual_lock                                                                                                               | All wire types, enum orders, units/scaling/ranges except proposed battery %, access and commands need this product's schema/capture. |
| `6fibxtph`, `99gv5nmz` PR226   | 8 battery %, 21 alarm, 28 language, 31 volume, 36 auto_lock_time (proposed seconds, 0–1800, step 1), 40 door status, 46 manual_lock, 47 motor, 33 automatic_lock                                                                                      | BLE IDs/types/order/access and actual delay units/range; proposed scale 1 is not proven.                                             |
| `kpn4zaf7` PR234               | 9 battery (proposed STRING high/medium/low/poweroff → 90/60/30/0%), 15 card, 19 BLE user number, 21 alarm, 28 language, 31 volume, 71 unlock                                                                                                          | STRING assumption and synthetic battery percentages unverified; need wire types/ranges/scaling/access and command payload.           |
| `wgv4haro` PR252/272, issue251 | 6 bluetooth_unlock (cloud RAW maxlen128 vs patch BOOL); 8 residual_electricity VALUE -1–100 scale0; 12/14/19/55/62 unlock user numbers VALUE 0–999 scale0; 21 alarm ENUM wrong_finger/wrong_password/low_battery; 31 volume ENUM mute/low/normal/high | Numbered schema and BLE types/enum order absent from cloud code-only list. All are non-writable here.                                |
| `hc7n0urm` PR190               | 21 alarm (proposed ENUM low_battery/power_off); 31 volume (proposed ENUM mute/normal); 71 ble_unlock_check                                                                                                                                            | No validated RAW/BOOL command or numeric enum order, ranges/access unconfirmed.                                                      |
| `pyawczjj` PR189               | Borrowed 8 battery %, 12 fingerprint, 13 password, 15 card, 21 alarm, 28 language, 31 volume, generic lock flag                                                                                                                                       | All product-specific BLE identities/types/ranges/scaling/access and command/state semantics unconfirmed.                             |
| `yfqp0shy` PR230               | 8 battery %, 12/13/14/15/16/19 user numbers; 21 alarm; 22 hijack; 33 automatic_lock; 34 verification (two proposed options); 36 delay (proposed seconds 0–1800 step1); 46 manual_lock; 47 motor                                                       | Need BLE schema/type/order/access/scaling and physical state/control confirmation.                                                   |
| `qicggi0m` PR246               | 8 battery %, 13/14/19/55/62/63 user numbers; 21 alarm; generic lock flag                                                                                                                                                                              | Product-specific wire types, numbered schema, scaling/ranges/access and actual unlock/state absent.                                  |
| `0qxp5u7s` PR324               | 21 alarm_lock ENUM; 31 beep_volume unsupported                                                                                                                                                                                                        | wrong_finger observed, full numeric alarm order not established; volume deliberately not exposed.                                    |
| `kholoaew` PR155               | 12/14/15/19/55/62/63 user numbers VALUE 0–999 scale0; 21 alarm ENUM wrong_finger/wrong_password/wrong_card/pry/low_battery/power_off; 36 delay VALUE 5–60 scale0 (unit empty); 71 unlock RAW                                                          | Extra reads not tested; alarm numeric order needs wire proof; delay unit/write semantics not tested; unlock explicitly failed.       |
| `uyf1ewof` PR274               | 6 bluetooth_unlock RAW; 21 alarm ENUM; 44 rtc_lock BOOL; 46 manual_lock BOOL; 62/63 user numbers VALUE                                                                                                                                                | No safe RAW command payload or verified lock/unlock semantics; alarm order/ranges/access unspecified.                                |
| `y2yaegze` issue154            | 21 alarm ENUM, 31 beep_volume ENUM, 44 rtc_lock BOOL, 71 ble_unlock_check RAW                                                                                                                                                                         | Need actual numeric enum order, working setting writes and successful unlock capture. No bolt DP in supplied status.                 |
| `qcrilcpr` issue304            | 21 alarm ENUM confirmed ID; card/BLE/remote numbers VALUE scale0 0–999; automatic_lock BOOL; beep_volume ENUM mute/normal; 71 proposed unlock RAW                                                                                                     | Remaining numeric IDs/enum order and actual unlock payload not established; connection success proves neither unlock nor bolt state. |

## Physical verification still required

No physical BLE device was used for this port. Tests mock BLE and network.

- Pulido: verify lock ends free passage, unlock holds passage, each Open/button
  press releases only briefly, bolt changes are reported, and reconnect/restart
  recovers passage state without claiming locked before any report. Confirm
  battery and user-number reads on this integration.
- H13: verify both volume/language indices, auto-lock on/off, repeated lock-only
  button writes, and battery/password/motor reads. Unlock remains unavailable.
- Pirnar/CTL20H/CTL20H_8018: verify every enabled read and reconnect behaviour.
  Resolve RAW versus BOOL before adding Pirnar commands. CTL20H_8018 needs a
  separate handshake fix tested on its hardware before reliable connection.
- All metadata-only products: obtain redacted integration diagnostics including
  `local_schema`, numbered local strategy (`dp_id`, code, type), functions and
  status ranges (units, scale, min/max/step, access/report modes), plus observed
  BLE type/enum bytes and before/after state for each physical operation.
  Share sanitized command structure only: never unlock-check contents, keys,
  tokens, user codes, credentials or cloud endpoint details.

The lock platform now preserves an explicit empty `lock: []` product override;
otherwise its category fallback would silently create an unsafe generic lock.
