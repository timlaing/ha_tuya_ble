# Supported devices

## Fingerbots (category_id `szjqr`)

- **Fingerbot** (product_ids `ltak7e1p`, `y6kttvd6`, `yrnk7mnn`, `nvr2rocq`, `bnt7wajf`, `rvdceqjh`, `5xhbk964`): original device, powered by CR2 battery.
- **Fingerbot Plus** (product_ids `blliqpsj`, `ndvkgsrm`, `yiihr7zh`, `neq16kgd`, `6jcvqwh0`, `riecov42`, `h8kdwywx`): has sensor button for manual control and program support.
- **CubeTouch 1s** (product_id `3yqdo5yt`): built-in battery with USB type C charging.
- **CubeTouch II** (product_id `xhf790if`): built-in battery with USB type C charging.
- **Nedis SmartLife Finger Robot** (product_id `yn4x5fa7`).
- **Finger Robot CBF02V2** (product_id `bo25vaxy`, sold as KKmoon): Click/Switch mode (DP 2), switch (DP 1, in both modes), push button and hold time 0.3–10 s (DPs 1/3, Click mode), up/down position 0–30 (DPs 4/5), battery (DP 8) and an **Invert switch status** button (DP 102) that flips the on/off indication without moving the arm. The switch reads and writes DP 1 inverted, as tested by the owner; if your unit shows the wrong state, press Invert switch status once. No program, reverse-position or charge-status entities: the device has only two modes, and charge status (DP 7) was never reported by the CR2-powered test unit. Auto adjustment (DP 101) is not exposed.

Programming (series of actions) is implemented for Fingerbot Plus. Exposed entities: Program (switch), Repeat forever, Repeats count, Idle position, and Program (text). Format: `position[/time];...` where position is in percent, optional time is in seconds.

## Fingerbot Plus / Switch Robot (category_id `kg`)

- **Fingerbot Plus** (product_ids `mknd4lci`, `riecov42`, `6jcvqwh0`): uses kg DP IDs.
- **Fingerbot Touch** (product_id `bs3ubslo`): two independent capacitive touch channels, each controlled by one switch. In **Switch** mode, the switch sends ON and OFF to the device. In **Push** mode, ON sends one press and Home Assistant returns the switch to OFF after the configured touch duration. Each channel has its own mode, touch duration, double-tap interval, touch input, and switch inversion settings. OFF touch duration is available in Switch mode. The device reports battery percentage.
- **Switch Robot** (product_id `4ctjfrzq`).
- **Simart Akilli Parmak** (product_id `gnpbj0bq`): battery (DP 105), push button (DP 108), down/up position (DPs 102/106), hold time (DP 103), Push/Switch mode (DP 101), switch (DP 1), and manual control (DP 107). Uses the verified Fingerbot Touch reconnect handling to tolerate duplicate notification fragments and stale clients. DP 104 is an enum, so no reverse-position switch is exposed; no program controls or Program mode are offered because DP 109 is absent. Other undocumented datapoints remain unmapped. Based on the reporter-tested descriptor and reconnect fix in #148, with the unverified Program mode omitted.

## Temperature and humidity sensors (category_id `wsdcg`)

- Soil moisture sensor (product_id `ojzlzzsw`).
- Bluetooth Temperature Humidity Sensor (product_ids `iv7hudlj`, `jm6iasmb`, `vlzqwckk`, `tr0kabuq`).
- Soil Thermo-Hygrometer (product_id `tv6peegl`).
- **SS302 Temperature Humidity Sensor** (product_id `6lbesej0`) — experimental: temperature (DP 1, tenths of a degree), humidity (DP 2) and battery (DP 4). No calibration or temperature-unit entities, because the source mapping deliberately left them out. Not yet verified on a device through this integration.
- **TH05 / TH05F Temperature Humidity Sensor** (product_ids `vyfoip9h`, `1jvidcsf`) — experimental: temperature (DP 1, tenths of a degree), humidity (DP 2) and battery (DP 4), a disabled-by-default °C/°F select (DP 9) that the temperature sensor follows, and disabled-by-default temperature (DP 23, −2.0 to 2.0 °C) and humidity (DP 24, −10 to 10 %) calibration. DP layout from the tuya-local TH05-BLE definition; not yet verified on a device through this integration.

## CO2 sensors (category_id `co2bj`)

- CO2 Detector (product_id `59s19z5m`): bitmap alarm switches.
- **XZ-JCO01 CO2 Detector** (product_id `wmb2xzj6`) — experimental: the `59s19z5m` layout, which the reporter of ha-tuya-ble/ha_tuya_ble#317 uses unchanged. No cloud schema has been captured.

## Smart Locks (category_id `ms`)

- Smart Lock (product_ids `ludzroix`, `isk2p555`, `gumrixyt`, `uamrw6h3`, `sidhzylo`, `mqc2hevy`, `a6nttc41`, `okkyfgfs`, `k53ok3u9`). Supports lock/unlock, alarm events, door status, and per-device unlock tracking (BLE, fingerprint, password, card, phone remote, dynamic code).
- Unnamed lock (product_id `bvclwu9b`). Identical coverage to `gumrixyt` — lock/unlock on DP 47 from the `_category_ms.yaml` default, plus battery, door status and lock alarm status — minus the lock motor-state switch.
- TenixLox KB150A (product_id `0binhnjg`). Lock/unlock on DP 47, plus battery, alarm events and the lock motor-state switch.
- Pulido P13 (product_id `kdq0tdbz`). Lock/unlock on DP 47, plus alarm events and the lock motor-state switch.

## Smart Locks (category_id `jtmspro`)

- Raycube K7 Pro+ (product_id `xicdxood`).
- LA-01 Smart lock (product_id `oyqux5vv`).
- A1 PRO MAX (product_id `rlyxv7pe`).
- B16 (product_id `ajk32biq`).
- Smart Cylinder Lock (product_ids `z7lj676i`, `hs21i377`).
- CentralAcesso (product_id `ebd5e0uauqx0vfsp`).
- HU06 Smart Lock (product_id `stugc8dl`).

Supports lock/unlock, alarm events, fingerprint/card/password unlock tracking, and battery status.

## Climate (category_id `wk`)

- Thermostatic Radiator Valve (product_ids `drlajpqc`, `nhj2j7su`, `zmachryv`). Supports temperature set, modes, and calibration. Additional switches: window check, antifreeze, child lock, water scale proof, programming mode.

The `wkf` / `llflaywg` radiator valve is not supported yet. Its published datapoints differ from the `wk` valves (for example, DP 101 is temperature correction, not the heating switch), so the `wk` layout cannot be reused. Tracked in #198.

## Heaters (category_id `qn`)

- **MaXpeedingRods diesel parking heater** (product_id `vmdizama`, experimental): on/off (DP 1) and set point (DP 2, 0–40 °C) as a climate entity, heat exchanger temperature (DP 3), heater mode (DP 4, `smart`/`auto`), heater level (DP 5) and a work-state diagnostic (DP 11). DP 3 is the heat exchanger, reaching about 230 °C while burning, so it is a separate sensor rather than the climate's current temperature, and the climate reports heating whenever DP 1 is on. The exchanger reading is blank while the heater is off. The heater reports it about once a second while running, so after the first reading the latest value is shown when the heater reports any data point and it differs from the shown value by at least 5 °C or at least 30 seconds have passed since the last shown reading. Time passing on its own does not update it. Levels 1–5 are shown but only 1–3 can be set, matching the cloud's command range. Limitations: the meaning of `smart` and `auto` is unconfirmed and mode changes are untested on hardware; the heater ignores the level in its thermostat mode; the °C/°F setting (DP 13) is not exposed, so keep the heater in °C. Based on ha-tuya-ble/ha_tuya_ble#358.

## Smart water bottle (category_id `znhsb`)

- Smart water bottle (product_id `cdlandip`).

## Irrigation computer (category_id `ggq`)

- Irrigation computer (product_ids `6pahkcau`, `hfgdqhho`).
- Dual-outlet irrigation computer (product_ids `fnlw6npo`, `jjqi2syk`): separate water valve and countdown entities for each outlet.
- Diivoo WT-05 dual water timer (product_id `fdrbxxbg`): separate water valve and countdown entities for each outlet.
- Insoma dual water timer (product_id `jntxv3q4`): separate water valve and countdown entities for each outlet.
- Yohgee dual water timer (product_id `qycalacn`): separate water valve and countdown entities for each outlet.

## Water valve controllers (category_id `sfkzq`)

- Aldi/Ferrex Smart Water Valve (product_id `16wgjvck`).
- SOP10 water timer (product_ids `nxquc5lb`, `c8800fd30884068f`, `so5ybnw9`).
- Valve controller (product_ids `svhikeyq`, `0axr5s0b`).
- Water valve controller (product_ids `46zia2nz`, `1fcnd8xk`).
- HCT-611 water timer (product_id `tqzkwarw`).
- ZX-7378 Smart Irrigation Controller (product_id `ldcdnigc`).
- YZD05 water valve (product_id `d4vpmigg`). Experimental. Valve and water-valve switch (DP 1), countdown (DP 11, 0–86400 s), weather-delay select (DP 10), weather select (DP 13), battery (DP 7), operation (DP 12), last and accumulated use time (DPs 15/9) and fault (DP 4), taken from the cloud schema in home-assistant-libs/tuya-device-handlers#348. The weather switch (DP 14) and raw schedules (DPs 16/17) are not exposed.
- Unistyle WT-04W dual water timer (product_id `ojrvmfkk`). Experimental. Separate valve, switch, countdown (0–86400 s), remaining time and operation entities per outlet (DPs 1/11/103/12 and 121/122/124/123), a 1–7 day weather-delay select (DP 10), a use-weather switch (DP 14) and battery (DP 101), from the tuya-local descriptor for this model. The weather select (DP 13), irrigation logs and schedules are not exposed.
- MoistenLand water timer (product_id `8t5hebn0`). Experimental. Valve and water-valve switch (DP 1), countdown (DP 11, 0–86400 s), weather-delay select (DP 10), battery and battery state (DPs 7/8), operation (DP 12), last use time (DP 15) and fault (DP 4), from the tuya-local descriptor for this model.
- The Rainpoint TTV102B (`e1poaiwa`) is not supported yet: the only source mapping shares a generic product entry and has no data-point report for this model.

Entities available across this category: valve (open/close/stop), the matching water-valve switch, battery (with battery-state and alternate-percentage diagnostics on some models), countdown/irrigation timers, accumulated use time, work state, weather forecast, weather-delay select, smart-weather switch, fault code, soak and irrigation schedules, and valve opening percentage on `16wgjvck`.

The enum order of the WT-04W and MoistenLand selects and status sensors is inferred and has not been confirmed on a device; diagnostics from either model are welcome.

Per-product coverage varies — `0axr5s0b` exposes only five entities, while the SOP10 timers expose twelve. Check the descriptor for the exact set.

## PARKSIDE Smart batteries (category_id `dcb`)

- PARKSIDE Smart battery 4Ah (product_id `z5ztlw3k`).
- PARKSIDE Smart battery 8Ah (product_id `ajrhf1aj`).
- PARKSIDE Smart battery 4Ah (2026 revision, product_id `vllfabvs`).
- PARKSIDE Smart battery 8Ah (2026 revision, product_id `r04rvmaq`).
- PARKSIDE Smart battery 4Ah (2026 revision, PAPP 204 A1, product_id `fab74vxu`).
- PARKSIDE Smart battery 12Ah (product_id `fay1puxy`) — **experimental**: read-only sensors only (battery, temperatures, charge/discharge current and voltage, cycle and protection-event counters, fault and tool diagnostics). The layout comes from the source fork's PR 283 and has not been checked against diagnostics from this battery, so battery status (DP 102), the configuration switches and numbers, the work-mode select and the identify switch are not exposed yet. A diagnostics download from a 12Ah pack — ideally one taken while charging, one idle and one under load — would let them be added.

Entities: battery, temperature, charge/discharge current and voltage, tool diagnostics (rotation speed, torque, runtime), fault counters, configuration switches (upper temp, security, kickback, lamp, laser), identify switch (DP 115) and battery work mode select (DP 105, Performance/Balanced/Eco/Expert) — all three on the 2026 revisions. Tool current (DP 157) is a plain measurement without a unit until diagnostics confirm it. Battery status on `z5ztlw3k`/`ajrhf1aj` and their discharging-current unit (mA) are unconfirmed against device diagnostics.

## LED strip lights and lamps (category_id `dd`)

- Magiacous LGB102 Magic Strip Lights (product_id `nvfrtxlq`).
- Magiacous Floor Lamp (product_id `umzu0c2y`).
- Comfamoli Sunset Lamp (product_id `6jxcdae1`).
- RGB Strip Light (product_id `0qgrjxum`).

Entities: on/off, brightness, color temperature, RGB color.

## Ceiling lights (category_id `xdd`)

- Globo CCT Ceiling Light (product_id `6tny2vqxktdzowt1`): WiFi/BLE ceiling light from Globo Lighting.

Entities: on/off, brightness, color temperature. `work_mode` is driven internally for color-temperature writes but not exposed as an entity; no `color_data` or `scene_data` data points are reported.

## Lights (category_id `dj`)

- MiBoxer BS64 Stair Light Controller (product_id `c2twgywo`). Experimental. Mode-based on/off, atomic white-mode brightness writes and mode/auto-effect selects. Auto reads as off. Temperature and timing controls are deferred pending schema/calibration; see [DP evidence and physical verification](docs/devices/miboxer-bs64.md). The source author tested their implementation on hardware, not this port.
- Tuya Smart LED Wall Lamp (product_id `joe8qqgiwhpqgwex`).
- LED BULB B509Z2 (product_id `bpqbwf8y`). Experimental. On/off, brightness and colour temperature only (no RGB), on the standard `dj` DPs 20–23 because the cloud report has no DP IDs. The countdown (`countdown_1`) is not exposed until its DP ID is known; diagnostics are welcome.

Entities: on/off, brightness, color temperature, RGB color (where the light has it).

## Water meters (category_id `slj`)

- RESTMO FML026A water meter (product_id `mqqna0px`). Experimental. Flow rate (DP 5, L/min), battery (DP 4) and water counters in litres: total (DP 1), current session (DP 2), today (DP 101), and total and today since the last reset (DPs 107/108). Every reading is reported in 0.1 units. The counters are `total_increasing`, so a daily, session or manual reset back to zero is recorded as a new cycle rather than negative usage. Resetting the counters from Home Assistant is not supported.

## Aroma diffusers (category_id `jsq`)

- Dituo DT-T2190A aroma diffuser (product_id `if1nolcm`). Experimental. Power and mist switches (DPs 1/2), mist mode, timer and light scene selects (DPs 3/5/101), time remaining (DP 6, minutes), fault (DP 12) and a light with on/off and brightness (DPs 7/9). Light colour and work mode (DPs 8/10) are not exposed because this model uses a non-standard work-mode list. The select option order is inferred from the tuya-local descriptor and has not been confirmed on a device.

## Blind / curtain controllers (category_id `cl`)

- Blind Controller (product_ids `4pbr8eig`, `vlwf3ud6`).
- LY curtain motor (product_id `ulughw4g`). Open/close/stop and position (DPs 1–3; the device only reports 0, 50 and 100 %), battery (DP 13), work state (DP 7), temperature (DP 103), fault (DP 12), auto-close temperature (DP 105, 10–60 °C in steps of 10), closing side (DP 101), learning start/end (DP 102), a disabled-by-default reset (DP 107) and a disabled-by-default timer schedules payload (DP 22).
- Curtain Controller (product_id `kcy0x4pi`).
- Rechargeable Curtain Robot (product_id `cq7xquhj`). Open/close/stop and target/current position (DPs 1–3), with commands, movement reports and percentage reversed together, plus battery (DP 13). Hardware-tested in ha-tuya-ble/ha_tuya_ble#359. Uses the generic Bluetooth key selection, not a product-specific login override.
- AM25 Plus L Tubular Motor (product_id `y0dtvgqf`). Open/close/stop and target/current position (DPs 1–3), hardware-tested in ha-tuya-ble/ha_tuya_ble#320. Mains-powered, with no battery entity. Direction (DP 5), fault (DP 12) and limit controls (DP 105) are not exposed; work state (DP 7) awaits numeric BLE reports.
- Roller Blind Robot RSH-MC27 (product_id `ousymtkt`). Open/close/stop (DP 1), target/current position (DPs 9/8) and battery (DP 13), hardware-tested in ha-tuya-ble/ha_tuya_ble#332.
- AOK AM25 Roller Blinds Motor (product_id `v3fzfd2y`, experimental). Open/close/stop and target/current position (DPs 1–3), plus battery (DP 13), from ha-tuya-ble/ha_tuya_ble#126. Hardware testing and numeric BLE metadata are still needed; work state (DP 7), reset (DP 102), direction and limit controls are not exposed.
- AOK AM24 Venetian Blinds Motor (product_id `dy4dh1q0`).
- Battery BLE Roller (product_id `jetlwvfm`). Cloud-schema-based mapping for open/close/stop and position (DPs 1–3), battery percentage (DP 13), and last operation duration (DP 10). Hardware operation is not yet verified; this descriptor does not resolve the cloud identity matching error in #39.
- Maxmarr Tubular Motor (product_id `mnet9kgf`). Open/close/stop (DP 1), target/current position (DPs 2/3, inverted because the device reports 0 = fully open), battery percentage (DP 13), motor fault (DP 12), motor direction (DP 5, Forward/Back), and a disabled-by-default work-state sensor (DP 7, opening/closing). Based on the device report in #145; hardware testing is pending. Limit calibration (DP 16) and undocumented DPs 101, 103 and 105 are not exposed.
- Unnamed curtain motor (product_id `qqdxfdht`). Identical coverage to `kcy0x4pi`: open/close/stop via the `_category_cl.yaml` cover default (state DP 1, position set DP 2, position DP 3), plus battery, work state and speed (DP 105).

Entities: open/close/stop, battery, work state and cover speed.

## Plant sensors (category_id `zwjcy`)

- SRB-PM01 Soil Moisture Sensor (product_id `jabotj1z`).
- Smartlife Plant Sensor SGS01 (product_id `gvygg3m8`).
- SGS01B-JR Soil Sensor (product_id `gpkyrocn`). Also has a disabled-by-default °C/°F select (DP 9) that the soil temperature sensor follows, from the cloud schema in ha-tuya-ble/ha_tuya_ble#328. Whether the device converts its reported temperature when set to °F is not yet verified on a device.

Entities: soil temperature, soil moisture, battery state, battery percentage. Soil moisture (DP 3) uses the moisture device class; existing installs keep its entity ID.

## Cooking thermometers (category_id `swtz`)

- **GRILLMEISTER Bluetooth Cooking Thermometer** (Lidl, SK-T100, product_id `iv13iqhf`): battery (DP 1) and two probe temperatures (DPs 2/3, 0.1 °C, negative readings confirmed). Probe presence (DP 16) and temperature unit (DP 17) are not yet confirmed and are not exposed.
- **BTH08 Smart Meat Thermometer** (product_id `o3nnpkat`) — _experimental_: battery (DP 1) and six probe temperatures (DPs 2–5, 28 and 29, 0.1 °C). Connection was confirmed, but which physical probe each DP reports is not. Target temperatures, timers, temperature unit and alarms are not exposed.

## Category notes

Category codes come from Tuya and several of them do not mean what their name suggests. The full
reference, including every category this integration does not use, is in
[docs/TUYA_CATEGORIES.md](docs/TUYA_CATEGORIES.md).

- **`kg` is officially "Switch."** That is why Fingerbot Plus and Switch Robot sit under it rather
  than under `szjqr`. `szjqr` is itself undocumented — it is what Tuya uses for the plain
  Fingerbot. Both use the same `mode.*` handlers, so their descriptors look nearly identical,
  which makes the category easy to mistake.
- **`jtmspro` is "Residential Lock PRO"** and **`ms` is "Residential Lock."** The two have the same
  entity layout, so they are listed separately here only because the category ids differ.
- **`ms_category` is "DoorLock Accessories."** It is a separate category from `ms`, reported by a
  lock's accessory sub-devices (key modules, fingerprint readers). No descriptor covers it.
- **`cljqr` and `jdcljqr` are curtain robots, not curtain motors.** `cl` covers motors, which
  report a controllable position. A robot only reports the two end states, so the curtain robot
  must not reuse `_category_cl.yaml` — it would produce a cover entity with a position slider
  that can never move. No robot is supported yet.
- **`znhsb` officially means "Smart glass,"** although the `cdlandip` product listed above is a
  smart water bottle. There is no glass device to compare it to, so the name is not a guide.

## Window cleaning robots (category_id `ccjqr`)

- **CHYW200.ABIR Window Cleaner Robot** (product_id `pnxl0r3l`) — **experimental**. A vacuum entity starts and stops cleaning (DP 1), reports its activity from the status (DP 4) and offers the cleaning mode (DP 2: smart, Z pattern, N pattern, edge, spot) as its fan speed. Also exposed: cleaning mode and direction (DP 3) selects, a status sensor, cleaning time in minutes (DP 6), a fault problem sensor (DP 11), an automatic water spray switch (DP 103) and a hidden-by-default cleaning switch on DP 1. Tested by the reporter in the ha-tuya-ble fork, not yet on this integration. Limitations: no pause or return-to-base (the device has no data point for them); choosing a cleaning mode does not start cleaning; the status and mode orders come from the reporter's mapping, not the Tuya cloud schema.

## Generators (category_id `qt`)

- **MaXpeedingRods MXR4000GT** (product_id `jlblyn8z`): engine start/stop, eco mode, output voltage, frequency and power, fuel level, current and total run time.

## Robot mowers (category_id `gcj`)

- **PARKSIDE Robot Mower PAMRC 250 A1 (BT) City** (product_id `9hdajpiw`), experimental: lawn mower entity (start, pause, stop and dock, from the machine status on DP 101 and the command DP 115), battery, activity, warning, problem and (disabled by default) cover sensors, error and work logs, firmware version, rain delay switch and time, rain mode, hedgehog protection, backward blade stop and running time. Start resumes with ContinueWork while the mower is paused, because the mower rejects StartMowing in that state; dock cancels the job before returning. Ported from ha-tuya-ble/ha_tuya_ble#291 and verified by its contributor on one mower; not yet tested with this integration. The schedule (DP 110/140) and zone (DP 113) data points, the schedule/zone clear and refresh triggers, and the generic vacuum DPs 2 and 3 are not exposed: their layouts are unverified or superseded by DP 115. Edge mowing (command EDGE) is not exposed yet.
