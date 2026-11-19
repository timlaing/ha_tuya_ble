# Bluetooth Smart TRV (`wkf` / `llflaywg`)

**Experimental — not hardware-verified on this integration.** Writes follow
the device owner's implementation and schema, not the incompatible `wk` copy
in [source PR 261](https://github.com/ha-tuya-ble/ha_tuya_ble/pull/261).
Thanks to [@tomaz1](https://github.com/tomaz1/ha_tuya_ble) for the MIT-licensed
implementation, schema comments and actual BLE reports in `climate.py`.
Evidence baseline: [owner commit `dc8a095`](https://github.com/tomaz1/ha_tuya_ble/tree/dc8a095101858a309f5acb73e509fdd854f83e93/custom_components/tuya_ble)
(`climate.py`, `sensor.py`, `number.py`, `select.py`, `switch.py`,
`binary_sensor.py` and enum constants in `const.py`).
See [issue 198](https://github.com/timlaing/ha_tuya_ble/issues/198).

## Controls and reports

| DP        | Wire type      | Entity / behaviour                                                                                                |
| --------- | -------------- | ----------------------------------------------------------------------------------------------------------------- |
| 1         | ENUM           | Auto 0 (device schedule), Manual 1 (HA Heat), Off 2; **Full open 3** through the Valve operating mode select only |
| 2         | VALUE          | Target 5–35 °C, 0.5 °C steps, divide wire value by 10                                                             |
| 3         | VALUE          | Current temperature, divide by 10; signed −30–100 °C                                                              |
| 6         | ENUM           | Closed 0 / opened 1 work state; climate idle / heating, off when mode is Off                                      |
| 7         | ENUM           | Closed 0 / opened 1 window state                                                                                  |
| 8 / 12    | BOOL           | Window detection / child lock; lock state diagnostic also shown                                                   |
| 13        | VALUE          | Battery percentage                                                                                                |
| 15 / 16   | VALUE          | Read-only lower / upper temperature limit diagnostics, divide by 10                                               |
| 101       | VALUE          | Temperature correction −10–10 °C, 0.1 °C steps                                                                    |
| 108       | VALUE          | Read-only opening percentage, divide by 10 (0–1000 → 0–100%)                                                      |
| 109 / 112 | STRING / VALUE | Manufacturer model / firmware integer diagnostics                                                                 |
| 110       | ENUM           | Read-only motor thrust: strong 0 / middle 1 / weak 2                                                              |
| 111       | ENUM           | LED brightness: high 0 / mid 1 / low 2                                                                            |
| 113       | ENUM           | Screen orientation: up 0 / down 2 only                                                                            |
| 114       | ENUM           | Climate comfort 0 / eco 1 preset                                                                                  |
| 115       | VALUE          | Eco temperature deviation 0.5–5 °C, 0.1 °C steps; unavailable until eco is reported                               |

Full open bypasses temperature regulation and forces the valve fully open.
It is **not** Heat, Dry or Fan-only: the climate HVAC mode is unknown while
the select reports Full open. Choose Manual/Heat, Auto or Off to leave it.
The climate's action comes from DP6 rather than guessing from temperatures.
The select intentionally exposes this safety-sensitive action explicitly.

## Evidence and parity limits

The owner's BLE log reports DP1 ENUM 1, DP2 VALUE 70, DP3 VALUE 255,
DP6/7 ENUM 0, DP8/12 BOOL false, DP13 VALUE 67, DP101 VALUE 0,
DP110 ENUM 1 and DP111 ENUM 0. It also reports DP15/16, DP108,
DP109/112, DP113 ENUM 0, DP114 ENUM 0 and DP115 VALUE 5.
The owner's select writes orientation 0/2; only up/down reportedly work.

**Target writes are unverified:** the owner writes DP2, but their schema
comment marks it read-only. Software tests prove the command encoding, not
that hardware accepts it. Other writable settings are schema-backed, not
newly tested here. DP15/16 access is uncertain and DP108/110 are read-only:
none is exposed as a writable number or select.

No schedule editor is supplied. DP17–23 are RAW reports without a documented,
tested layout. Existing schedules may run in Auto mode; they cannot be edited
here. DP14 fault bits and DP116 motor-data text remain unmapped because their
semantics are incomplete. No low-battery binary sensor is invented from DP105.

Source PR261 uses a BOOL DP101 heating switch, DP102 current temperature,
DP103 target, DP106 away, DP27 calibration, DP40 child lock, DP105 battery
alarm and DP10/107/108/130 switches. Those assignments are wrong for this
product: DP101 is numeric correction and DP108 is numeric, read-only opening.
There is no evidence for antifreeze, away, water-scale-proof or those programming
switches on this TRV. Functionality parity means using the owner-correct
equivalents, not issuing unsupported commands.

Existing `wk` descriptors and unique IDs are unchanged. Genuine owner entity
keys (including `temp_correction`, `eco_temp_deviation`, `led_brightness` and
`valve_open_degree`) are retained. The source-main battery alarm ID is not
repurposed as a battery percentage.
An existing `temperature_calibration` number ID is retained for temperature
correction (the same semantic control), while its DP is corrected to 101.

## Hardware acceptance before removing experimental status

1. Export redacted diagnostics/local schema to confirm category, DP types,
   access modes, enum order and bounds.
2. Check Auto/Manual/Off, then Full open with safe supervision; confirm actual
   valve behaviour and readback. Restore a regulated mode afterwards.
3. Confirm DP2 writes at 5, 20.5 and 35 °C, readback and actual regulation;
   confirm negative room temperatures decode correctly where practical.
4. Verify comfort/eco, eco deviation, correction, window detection, child lock,
   brightness and orientation; confirm 0/2 orientation readback.
5. Compare battery, opening, work/window state and model/firmware diagnostics
   with the app. Check bounds and motor thrust are never written.
6. Test reload, offline restart, out-of-range recovery and adapter/proxy use.
   Passing unit tests is not a hardware acceptance result.
