# Experimental read-only research subsets

Part of [#181](https://github.com/timlaing/ha_tuya_ble/issues/181) /
[#157](https://github.com/timlaing/ha_tuya_ble/issues/157).
These are **schema-backed subsets, not physically tested support in this
integration**. Gateway schemas establish candidate IDs, types and scales, not
successful direct BLE authentication or transport. No writable entities are
offered, except for the AQUA CREATIV TITAN PRO 300 below, which has since been
tested on hardware. Product sensors are created only if the corresponding wire type is
present during platform setup; collect diagnostics and reload after reports
arrive if initially only RSSI appears. A later type mismatch makes a reading
unknown rather than coercing it.

## Yieryi BLE-YL01 — `sh/v1jqz5cy`

Source: [tuya-local gateway schema](https://github.com/make-all/tuya-local/blob/1260db9d65ec5f101ac9086823d99b57b688f671/custom_components/tuya_local/devices/ble_yl01_watertester.yaml),
linked by @CloCkWeRX in [source issue 220](https://github.com/ha-tuya-ble/ha_tuya_ble/issues/220).
[Source PR 271](https://github.com/ha-tuya-ble/ha_tuya_ble/pull/271) is
AI-generated, unmerged and has no hardware confirmation; its write mappings
are not evidence of safe commands.

| Read-only measurement | DP  | Expected BLE type | Divisor | Unit  |
| --------------------- | --- | ----------------- | ------- | ----- |
| TDS                   | 1   | VALUE             | 1       | ppm   |
| Temperature           | 2   | VALUE             | 10      | °C    |
| Battery               | 7   | VALUE             | 1       | %     |
| pH                    | 10  | VALUE             | 10      | —     |
| EC                    | 11  | VALUE             | 1       | µS/cm |
| ORP                   | 101 | VALUE             | 1       | mV    |
| Chlorine              | 102 | VALUE             | 10      | mg/L  |
| Salinity              | 117 | VALUE             | 1       | ppm   |

Omitted: buffer-standard DP103 (gateway strings do not establish BLE enum
order); backlight DP104; update action DP105; thresholds DP106–113;
calibration DP114–116; reset DP118. No calibration, actions, selects, numbers
or backlight writes. The direct BLE service (A201/FD50), version, wire types
and signed ORP/temperature reports still need device confirmation.

## HCT-626 — `sfkzq/smd9kj1n`

Source: [tuya-local gateway schema](https://github.com/make-all/tuya-local/blob/1260db9d65ec5f101ac9086823d99b57b688f671/custom_components/tuya_local/devices/hct626_dual_watertimer.yaml),
linked by @CloCkWeRX in [source issue 208](https://github.com/ha-tuya-ble/ha_tuya_ble/issues/208).
[Source PR 250](https://github.com/ha-tuya-ble/ha_tuya_ble/pull/250) is
AI-generated, unmerged and not hardware-tested.

Read-only BOOL state reports: valve 1 DP1, valve 2 DP2, smart weather delay
DP43. These are binary sensors, **not valves or switches**. Numeric VALUE
reports: weather-delay remaining DP42 in hours; last runtimes DP101/102
and spray times DP103/104 in seconds, all unscaled.

Omitted: all writes; timers DP17/18 (BLE units/countdown behaviour unconfirmed);
weather DP44 and delay DP45 (enum order unknown); battery DP47 (gateway divisor
0.05 is not a confirmed BLE scale/raw range); low-battery DP53 (nonzero
interpretation needs confirmation); schedules DP38/108; valve-state enums
DP105/106. No guessed irrigation controls are inherited. Direct BLE transport,
all units and actual valve-report semantics remain untested here.

## AQUA CREATIV TITAN PRO 300 — `dj/e2kdgdsu`

Source: @vrnoleg's [issue 357](https://github.com/ha-tuya-ble/ha_tuya_ble/issues/357),
which reported classic GATT service 1910 with 2B11 write / 2B10 notify, DP101
BOOL true after Manual and DP102 ENUM 1 after Auto, followed by hardware testing
in [issue 222](https://github.com/timlaing/ha_tuya_ble/issues/222). The profile
started as a report-only research subset and now has tested controls.

Exposed: light on DP101 with total brightness on DP3 (writing DP3 sets all four
channels to the same value), channel numbers DP105–108 (0–100 %), light-mode
select DP102 (0 manual, 1 auto, 2 simple setup), effect-demo select DP103 (0 none,
1 auto, 2 simple setup, 3 moon, 4 thunderstorm, 5 cloudy), 24-hour clock switch
DP104, and the DP101 BOOL and disabled-by-default DP102 numeric diagnostics. The
generic `dj` DP20–24 defaults do nothing on this lamp and stay blocked.

Not exposed: the raw programs DP109–113 and accessory data points DP114–120. The
programs have a documented layout (an enable byte followed by times and levels)
but need a raw writer. The Moon option of DP103 showed no effect over BLE even
inside the program's time window; the Moon effect itself is started by the
enable flag of DP111.

## KC8B105 — `gcj/7yr5iwga`: deferred

Sources: [348](https://github.com/ha-tuya-ble/ha_tuya_ble/issues/348),
[349](https://github.com/ha-tuya-ble/ha_tuya_ble/issues/349), thanks
@valeriovernacchia-source and @rgerr. Cloud codes list DP2 switch_go, DP3 mode,
DP5 status and DP13 battery_percentage **without types, scales, ranges or enum
order**. No descriptor or sibling `9hdajpiw` profile is added. A metadata-only
descriptor would not make the unsupported product authenticate.

Modes 11/12/16 provisioning and factory auth_key retrieval/storage are out of
scope. Firmware protocol definitions do not establish this product's schema.
Diagnostics/security selection remain owned by #183/#163; mower platform work
is #177.

## Bounded additional leads: no equally strong new evidence

Disposition retained from the [#181 research comment](https://github.com/timlaing/ha_tuya_ble/issues/181#issuecomment-6983060638):

- `kcy0x4pi`, `xhf790if`: existing classic security handling; hardware
  confirmation requested, no duplicate mapping.
- `gnpbj0bq`: existing descriptor; reconnect work belongs to #162/#148.
- `jabotj1z`, `bs3ubslo`, SGS01B: existing support/follow-up #173; no duplicates.
- `fr2ztrki`, `bjoxnbwa`: missing transport evidence and full BLE schema;
  generic light DPs are not proof of support.
- `sjznmemn`, `bt1raf68`, `ix6bgswz`, `odswlbcu`: fork/gateway leads only;
  transport and schema needed.
- Locks from source issues 285/268/267/213/211/183/179/178/128/109: verified
  schema and command/state semantics needed; route through #165/#179.
- `boagb65r`, `0envtxjyq7wn7h6t`, `d8anh8og`, `agwu93lr`, `9ayszy9m`:
  fork links alone insufficient; defer.
- `mnet9kgf`: existing descriptor.
- `ms0lqqtu`: types/codes without ranges, no reported connection; defer.
- `klpcpgrh`: GATT evidence without DPs; defer.
- Source issues 200/197/136 and PRs 199/290: background diagnostics/schema
  knowledge, no additional product verification.

## Hardware acceptance still required

For each implemented profile, capture redacted diagnostics with `local_schema`
and live DP types/raw bytes; confirm each measurement, unit and scale against
the app or a reference reading (including negative ORP/temperature and unknown
mode reports). For HCT-626, change each valve in the app and confirm only its
read-only state changes. For the aquarium, exercise Manual/Auto in the app and
record DP101/102 without assuming enum order.

Confirm authentication, offline reload/reboot, out-of-range recovery and
operation through the intended adapter/proxy. No write support should be added
until exact DP/type/range/access evidence and a successful device command/report
test are available. Share no keys, tokens, passwords or raw credential dumps.

The setup probe's existing minimum is **one expected DP ID after authenticated
pairing and a status request**, not all mapped IDs or type/scale verification.
These descriptors provide real expected report IDs, not dummy placeholders;
an authenticated setup result does not upgrade the hardware support claim.
