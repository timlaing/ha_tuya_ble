# MiBoxer BS64 (`dj` / `c2twgywo`)

Experimental port of [PR 355](https://github.com/ha-tuya-ble/ha_tuya_ble/pull/355)
by @3XX0, whose author reports physical testing through an ESPHome proxy.
The new integration code has only mocked BLE tests, not physical-device proof.
PR 286's brightness-only color-mode restriction is already implemented here.
The `dj` defaults (DP20–24) are overridden, never changed.

## DP evidence

All entries below come from PR 355's implementation and owner report, not
a captured cloud schema. Cloud codes shown as unknown must not be inferred.

| DP  | Wire type | Cloud code   | Units / scale                  | Range / order                             | Access     | Evidence / implementation                                                   |
| --- | --------- | ------------ | ------------------------------ | ----------------------------------------- | ---------- | --------------------------------------------------------------------------- |
| 1   | Boolean   | switch_led   | none                           | false / true                              | read/write | Owner says device forces it on; never written                               |
| 2   | Enum      | work_mode    | none                           | white = 0; others unverified              | read/write | Owner says white must accompany brightness/temperature; writes 0 atomically |
| 3   | Value     | bright_value | brightness / scale 0           | 10–1000, step 1                           | read/write | Source descriptor; maps HA 0–255 to 10–1000                                 |
| 4   | Value     | temp_value   | relative temperature / scale 0 | 0–1000, warm to cool                      | read/write | Deferred: no calibrated kelvin bounds or curve                              |
| 103 | Value     | unknown      | seconds / divide by 10         | source explicitly assumes 0.1–10 s        | read/write | Deferred: range/step require schema                                         |
| 104 | Enum      | unknown      | none                           | 0 full, 1 gradual, 2 following, 3 flowing | read/write | Owner-tested source select order                                            |
| 105 | Value     | unknown      | seconds / scale 0              | source explicitly assumes 1–600 s         | read/write | Deferred: range/step require schema                                         |
| 114 | Enum      | unknown      | none                           | 0 auto, 1 on, 2 off                       | read/write | Owner-tested mode writes; Auto reads as off                                 |

Turning on selects mode 1 unless already on. Brightness changes include white
work mode 0 in the same BLE batch, even when white is already reported.
Turning off writes only mode 2. Neither operation writes DP1.
Failures and cancellation use the existing batch rollback and write lock.
Reported values are restored unless a newer report arrived mid-write. When
there was no report before a failed first write, newly created datapoints retain
neutral Off/white/minimum-brightness values until the controller reports.
Those fallback values do not prove the controller's physical state.

## Required evidence and physical verification

Please provide a **redacted integration diagnostics download** containing the
DP schema (`functions`, `status_range`, `local_schema`) and reports for this PID,
without keys, tokens, credentials, user codes or endpoint URLs. DP103/105 need
their min/max/step/scale and cloud codes; DP104/114 need cloud codes and enum
order confirmation. DP4 additionally needs manufacturer kelvin bounds and
measured or documented midpoint behavior: the source uses HA defaults and a
linear-kelvin conversion, not evidence of the controller's actual curve.
Existing lights keep their mired conversion and unique IDs.

On this integration, physically verify login/reconnect through the proxy,
Auto/On/Off state and mode select, all four effect selections, brightness
minimum/midpoint/maximum, atomic white-mode application, and a rejected or
interrupted write (including reconciliation after reconnect). Timing and
temperature remain unavailable until their metadata is established.
