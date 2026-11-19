# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog],
and this project adheres to [Semantic Versioning].

## [Unreleased]

### Added

- **Conditional app login for Security V2 devices**: offer a one-time Tuya or Smart Life login only when Bluetooth verification indicates a missing or unusable security key. Import the matching mobile local/security key pair, retry Bluetooth verification, and support recovery through Reconfigure without changing entity IDs. App passwords and mobile sessions are not saved.
- **Mobile cloud diagnostics**: store bounded, recursively sanitized device, product and status snapshots alongside the existing QR-cloud data. Preserve approved metadata and field shapes without credentials, private account data or raw status values; snapshots remain downloadable when Bluetooth verification fails.
- **PARKSIDE Smart battery 12Ah (`dcb`/`fay1puxy`) — experimental** (#175): a descriptor for the 12 Ah pack from the source fork's PR 283 (thanks @kleju00). It exposes the battery's read-only sensors only — battery, temperatures, charge and discharge current and voltage, the cycle and protection-event counters, fault and tool diagnostics. Battery status and the writable settings (switches, numbers, work mode, identify) are left out until a diagnostics download from this battery confirms them.
- **Vacuum platform and CHYW200.ABIR window cleaner** (#178, experimental): cleaning robots described by a device descriptor now get a Home Assistant vacuum entity that offers only the actions the robot supports. The first is the CHYW200.ABIR window cleaning robot (`ccjqr` / `pnxl0r3l`): start and stop, its activity, and the cleaning mode as fan speed, plus its mode and direction selects, status, cleaning time, fault and automatic water spray. It has not yet been tested on this integration; please report how it works. Thanks to @buliaksk for the device mapping and @CloCkWeRX for the original vacuum platform in the ha-tuya-ble fork.
- **Water timers** (#174): experimental support for the YZD05 water valve (`sfkzq` / `d4vpmigg`), the Unistyle WT-04W dual water timer (`sfkzq` / `ojrvmfkk`) and the MoistenLand water timer (`sfkzq` / `8t5hebn0`), with valves, countdowns, weather delay, battery, operation and fault entities. Ported from ha-tuya-ble/ha_tuya_ble; thanks to @hippich, @CloCkWeRX and the tuya-local contributors. The Rainpoint TTV102B (`e1poaiwa`) waits for a data-point report from that model.
- **RESTMO FML026A water meter** (#174, `slj` / `mqqna0px`): experimental flow rate, battery and water-usage counters (total, session, daily and since reset) in litres. Thanks to @phoenixxx-1 for the original mapping and device test.
- **LED BULB B509Z2** (#174, `dj` / `bpqbwf8y`): experimental light with brightness and colour temperature only, since this bulb has no RGB colour. Thanks to @CloCkWeRX.
- **Dituo DT-T2190A aroma diffuser** (#174, `jsq` / `if1nolcm`): experimental power, mist, mist mode, timer, light scene, time remaining and fault entities, and a light with brightness. Thanks to @CloCkWeRX and the tuya-local contributors.
- **Finger Robot CBF02V2 (`szjqr` / `bo25vaxy`)** (#168): add Click/Switch mode, switch, push, hold time, 0–30 up/down position, battery and an Invert switch status button that corrects the on/off indication without moving the arm. The switch keeps the owner-tested inverted on/off handling. Charge status and auto adjustment are not exposed. Thanks to @bchevreau for the device profile, Thing Model and testing.
- **GRILLMEISTER / SK-T100 cooking thermometer (`swtz` / `iv13iqhf`)** (#168): add battery and two probe temperatures, including below-zero readings. Probe presence and temperature unit are not exposed until confirmed. Thanks to @CloCkWeRX for the mapping and testing.
- **BTH08 Smart Meat Thermometer (`swtz` / `o3nnpkat`)** (#168, experimental): add battery and six probe temperatures. Which probe each reading belongs to still needs confirming on the device. Thanks to @crebain for the mapping.
- **Manual credentials** (#176): setup now offers entering a device's credentials by hand as a fallback to the app login. The credentials are saved once Home Assistant has logged in to the device over Bluetooth with them, and only for products this integration already supports. If that check fails for a Tuya BLE protocol v4 device, you can still choose to add it anyway, as with the app login, so its diagnostics can be collected. Based on ha-tuya-ble/ha_tuya_ble#300 by @SittingDuck52 and the form fix in ha-tuya-ble/ha_tuya_ble#331 by @oranav.
- **Replace device keys** (#176): the integration's options can replace a device's local key and security key together in one step, and leaving the security key empty removes it. The device ID, UUID and entities are kept, and the stored keys are never shown. Based on the transactional key replacement in ha-tuya-ble/ha_tuya_ble#312 by @jbjardine and the key clearing in ha-tuya-ble/ha_tuya_ble#331 by @oranav.
- **MaXpeedingRods diesel parking heater (`qn` / `vmdizama`)** (#169, experimental): on/off and set point (0-40 °C) as a climate entity, heat exchanger temperature, heater mode and level selects and a work-state diagnostic. The heat exchanger temperature is not the room temperature, so the climate shows no current temperature and reports heating whenever the heater is on. The exchanger reading is blank while the heater is off. While it runs, the latest reading is shown when the heater reports any value and the reading differs from the shown one by at least 5 °C or at least 30 seconds have passed since the last shown reading. Time passing on its own does not update it. Levels 4 and 5 are shown when reported but cannot be selected. The meaning of the `smart` and `auto` modes has not been confirmed, and the heater ignores the level while it runs in its thermostat mode. Ported from ha-tuya-ble/ha_tuya_ble#358; thanks to @andybell007-creator for the support and hardware testing.
- **Heater climate options**: descriptors can now report a climate's action from its on/off switch alone, mark select options as read-only, and blank a sensor while a switch is off or throttle frequent readings. Existing devices are unchanged.
- **XZ-JCO01 CO2 Detector (`co2bj` / `wmb2xzj6`)** (#173): experimental support, using the CO2 Detector (`59s19z5m`) layout the reporter already runs. Thanks to @sshmobil-a11y for the report and @CloCkWeRX for the original mapping.
- **SS302 Temperature Humidity Sensor (`wsdcg` / `6lbesej0`)** (#173): experimental temperature, humidity and battery sensors. Thanks to @Sathen and @CloCkWeRX.
- **TH05 / TH05F Temperature Humidity Sensors (`wsdcg` / `vyfoip9h`, `1jvidcsf`)** (#173): experimental temperature, humidity and battery sensors, plus a °C/°F select and temperature and humidity calibration, all disabled by default. Thanks to @CloCkWeRX.
- **SGS01B-JR temperature unit (`zwjcy` / `gpkyrocn`)** (#173): a disabled-by-default °C/°F select on DP 9, which the soil temperature follows. Thanks to @pluusla for the device schema.
- **Robot lawn mowers** (#177): a new `lawn_mower` platform, driven by the device descriptors, that shows the mower's activity and offers only the start, pause, stop and dock commands the mower supports.
- **PARKSIDE Robot Mower PAMRC 250 A1 (BT) City (`gcj` / `9hdajpiw`)** (#177), experimental: start, pause, stop and dock, battery, activity, warning, problem and cover sensors, error and work logs, firmware version, and rain delay, rain mode, hedgehog protection, backward blade stop and running time settings. Starting a paused mower resumes the job. Ported from ha-tuya-ble/ha_tuya_ble#291; thanks to @jsimonetti for the original work.
- **Diagnostics: Security V2 state** (#183): diagnostics now show whether a Tuya BLE protocol v4 device announces Security V2 as supported or as enabled, both in its Bluetooth advertisement and in its connection reply, so Security V2 login problems can be diagnosed from a report. Debug logs note when the announced state and the security key in use disagree.
- **Shared smart-lock behaviour** (#165): device descriptors can now tell passage mode, automatic locking, the bolt report and a momentary open apart, instead of treating every lock the same. A lock can hold itself open with passage mode, toggle automatic locking, unlock with an empty Bluetooth trigger, or offer an **Open** action that releases it for a few seconds; a lock that cannot report whether it is locked can get an unlock button instead. The lock state stays unknown after a restart until the lock reports it, rather than showing locked. No supported lock changes behaviour yet; products using this follow separately. Ported from the ha-tuya-ble fork; thanks to @steven-ahfu, @way-lo, @rairulyle, @GeorgeBark and @albertgoca93.
- **Bluetooth unlock check for locks** (#165): when the Tuya cloud reports a lock's `ble_unlock_check` while the device is added, it is stored so the lock can be opened with the timestamped command the Tuya app sends, for whichever app member the check was issued to. The check and the commands built from it are never logged or included in diagnostics. A lock added before this version needs to be removed and added again to fetch it. Experimental: only tested by the original contributors on their own locks.

### Fixed

- **Entity labels** (#180): English lock, alarm, Fingerbot programming and thermostat labels now use sentence case. Fingerbot Touch settings use readable channel labels instead of numeric menu prefixes. Existing entity IDs, unique IDs and user-assigned names are kept. Inspired by ha-tuya-ble/ha_tuya_ble#335 and #336; thanks to @rairulyle. No entity-ID renaming or dynamic lock-name change is included.
- **Bound classic devices with a misleading product ID** (#182): when an older Tuya BLE device's identity cannot be read, setup now tries the devices from your Tuya account whose product matches the advertisement first, then the other devices, up to four in total. Previously, if the advertised product matched a different device, only that device was tried and setup failed with `device_not_registered`. If more than four devices in your account match the advertised product, setup still stops with `too_many_candidates` without trying any.
- **Tool current on the older Parkside batteries** (#175): DP 157 on the `z5ztlw3k` (4 Ah) and `ajrhf1aj` (8 Ah) packs was declared as an electric current without a unit, which Home Assistant flags as invalid. It is now a plain measurement, as on the 2026 packs, until a diagnostics download confirms the unit. An audit against the source fork's PR 283 (thanks @kleju00) and the #103 diagnostics (thanks @bip1963) confirmed the existing status order and the charge voltage and current units on `vllfabvs` and `z5ztlw3k`; the conflicting PR 283 status order was not adopted, as it has no device evidence.
- **Battery state** (#174): the `middle` battery level reported by several water timers now shows as "Medium".
- **No repeated commands after a Bluetooth reconnect** (#162): a command whose write failed is no longer sent again once the device reconnects. The retry reused packets encrypted for the old connection, and the device may already have acted on them, for example pressing a Fingerbot twice.
- **Recovery from an unanswered command** (#162): when the device does not reply to a command, the connection is closed and opened again instead of being kept in a possibly broken state. A late reply or disconnect notice from an old connection no longer affects the new one, and commands queued for the old connection are not sent on the new one.
- **Button press errors are shown** (#162): a button press now waits for the device and shows an error in Home Assistant if the press could not be sent or was not confirmed, instead of failing silently in the background. These three fixes are ported from [ha-tuya-ble/ha_tuya_ble#343](https://github.com/ha-tuya-ble/ha_tuya_ble/pull/343); thanks to @d-g-laptev.
- **Reloading or removing an entry while its device is out of range** (#164): unloading no longer waits for a connection attempt that is still searching for the device, so a reload or options change finishes straight away instead of leaving the entry stuck. A connection that completes afterwards is closed, and the reloaded entry waits for it to close before connecting. Based on the report and fix by @sytchi; the new tests also cover the dropped-connection report by @LozhkinIvan.
- **Soil moisture reported as humidity** (#173): the SGS01B-JR, SGS01 and SRB-PM01 soil sensors now report soil moisture with the moisture device class. Existing entities keep their entity IDs.
- **Temperature briefly shown in the wrong unit** (#173): on sensors with a °C/°F select, including the existing CO2 detector, soil moisture sensor and water bottle, a reading is no longer relabelled while a unit change is still being sent. The unit shown is the one the device last reported, so it changes as soon as the device confirms the new unit, including when the unit is changed before the device has first reported it.
- **Choosing the right Bluetooth login for Security V2 devices** (#163): a Tuya BLE protocol v4 device can announce that it supports Security V2 without having it switched on. Setup now tries the classic login first for such devices, and the Security V2 login only if the device ignores the classic one. Devices that announce Security V2 as switched on use only Security V2 when the Tuya cloud provides a `sec_key`; without one, only the classic login can be tried. The login that worked is saved with the connection profile, and **Reconfigure** checks it again. Devices that do not announce Security V2 keep the classic login even when the Tuya cloud sends a `sec_key`, as some do (for example `qcrilcpr`, `mknd4lci`, `gvygg3m8`, `c2twgywo` and `cq7xquhj`). Devices added before this change keep their previous login (Security V2 whenever a `sec_key` is stored) until you run **Reconfigure**, which applies the new selection. When you replace the device keys in the options and enter a security key, the saved login is kept unless the device now announces Security V2 as switched on; removing the security key also removes the saved login, so only the classic login is used. Diagnostics show the saved login under `identity.security_mode`. Thanks to @SittingDuck52, @eric1905, @oranav, @CloCkWeRX, @3XX0 and @albertgoca93 for the upstream reports and fixes in ha-tuya-ble. The new order has not yet been tested on a Security V2 device.

## [2.10.0-beta.6] - 2026-11-18

> **Beta — for testing only.** This build supersedes beta.5 with support for Tuya BLE protocol v4 devices that use the newer "Security V2" login, lets you add a device even when the Bluetooth check fails so its diagnostics can be shared, and fixes setup of some older (protocol 3) devices whose Bluetooth identity could not be read. Security V2 has not yet been tested on a device; if a device was added with an earlier beta, remove it and add it again so the new login details are fetched.

### Added

- **Newer Bluetooth login (Security V2)** (#39): devices that announce Security V2, such as the Battery BLE Roller (`cl` / `jetlwvfm`), now log in with the extra security key from the Tuya cloud (`sec_key`), matching the approach already proven on the YZD02B irrigation timer. The key is only stored for these devices and is never logged or included in diagnostics.
- **Tuya app handshake**: setup also tries the handshake the Tuya app itself uses with newer Bluetooth firmware, after the existing ones.
- **Add a device anyway**: when the Bluetooth check for a Tuya BLE protocol v4 device fails, setup explains why (for example, the device did not reply or did not accept the login) and lets you add the device anyway, so its diagnostics can be collected. It may show as unavailable until support is added.
- **Diagnostics**: show whether the Tuya cloud sent a `sec_key`, whether it is stored and whether the device uses Security V2. `cloud_info.fields` lists every field the cloud sends for the device; only product and version fields show their values, all others show just their type and length.

### Fixed

- **Bluetooth check stopped before connecting** (#39): setup no longer gives up on Tuya BLE protocol v4 devices that do not advertise themselves as added to an account; the device's own encrypted reply now decides.
- **Missing readings from Tuya BLE protocol v4 sensors** (#39): one- and two-byte number readings are no longer thrown away, restoring SRB-PM01 humidity and battery values.
- **Clearer setup messages**: the Bluetooth check and reconfigure messages are shorter and easier to follow.
- **More useful debug logs**: each Bluetooth check logs how far it got, which login it used and the type of any error, and a reply using an unexpected security scheme is logged with its security flag.
- **Bluetooth notifications on the Pi's own adapter**: Tuya BLE protocol v4 devices now use BlueZ's StartNotify, which other integrations found necessary for these devices.
- **Classic product-key advertisements** (#158): setup now reads older Tuya BLE advertisements that carry a product key instead of a product ID, so bound Wi-Fi and Bluetooth combo bulbs no longer fail with `identity_not_decoded`. Thanks to @zdenax for the report and fix.
- **Bound classic devices that could not be identified** (#155): setup now keeps scanning until an older Tuya BLE device's advertisement can be read, and if it still cannot, finds the device by logging in to each matching device from your Tuya account instead of failing with `identity_not_decoded`. Reported for the Fingerbot Touch (`kg` / `bs3ubslo`); thanks to @njubinho for the capture.

## [2.10.0-beta.5] - 2026-11-17

> **Beta — for testing only.** This build supersedes beta.4 with quieter BLE connection failures and bounded automatic recovery. It does not fix the underlying radio or ESPHome proxy GATT errors, and fresh affected-device hardware testing remains necessary.

### Fixed

- **BLE connection retry and outage log spam** (#153): limit all devices to three connection/handshake cycles per batch while retaining the Bluetooth connector's internal retries. Log one concise warning per device outage, with individual failure tracebacks available at debug level, and reset warning suppression after a successful handshake.
- **Automatic BLE recovery** (#153): retry background connections with exponential delays from five to sixty seconds, including profile-less devices and FD50 identity recovery. Avoid duplicate retry owners during identity backoff and prevent stale reconnect tasks from overwriting the delay reset after recovery.

## [2.10.0-beta.4] - 2026-11-16

> **Beta — for testing only.** This build supersedes beta.3 with automatic protocol-4 compatibility verification and additional device support. Fresh affected-device hardware testing remains necessary; this beta does not claim to resolve every report in #39 or support every protocol-4 firmware.

### Added

- **Automatic protocol-4 compatibility verification** (#147): setup tests bounded read/authentication-only connection profiles and saves a profile only after authenticated pairing and a mapped datapoint report. Restore verified profiles offline after Home Assistant restarts, recalculate packet capacity on each connection, and support acknowledged protocol-4 control writes. Existing entries can use **Reconfigure** to verify compatibility while preserving entity IDs.
- **Maxmarr tubular blind motor (`cl` / `mnet9kgf`)** (#149, fixes #145): add a cover with position control, battery and fault sensors, a disabled-by-default work-state sensor, and a motor-direction select.
- **Simart Akilli Parmak Fingerbot (`kg` / `gnpbj0bq`)** (#150, fixes #148): add battery, push, position, hold-time, Push/Switch mode, switch and manual-control entities. Omit the reverse-position switch because DP104 is an enum, and omit unsupported program controls because DP109 is absent.

### Fixed

- **Simart Fingerbot reconnects** (#150): enable the reporter-tested Fingerbot Touch reconnect handling for this model, tolerating duplicate notification fragments and rejecting stale-client callbacks and queued writes.
- **Protocol-4 connection and control recovery** (#147): bound reconnect attempts, fail pending requests on disconnect, preserve datapoint rollback on rejected writes, and publish cover movement only after a successful command. Correct the Battery BLE Roller's ENUM controls and serialize reconfiguration with runtime ownership.

## [2.10.0-beta.3] - 2026-11-15

> **Beta — for testing only.** This build supersedes beta.2 with runtime connection recovery after setup. UUID selection and storage are unchanged, and this beta does not claim to resolve every report in #39. Fresh affected-device hardware testing remains necessary.

### Fixed

- **Runtime availability after successful setup** (#144): initialize the runtime protocol from cached advertisements, preserve the negotiated protocol during concurrent updates, and start a fresh authenticated runtime session rather than reuse setup-probe keys. Failed handshakes close their clients, late notifications from old links are ignored, and recovery resumes automatically after blocked cleanup.
- **Native and proxy teardown recovery** (#144): retain failed-link ownership until disconnection is acknowledged or an ESPHome source is explicitly confirmed lost. Recover native BlueZ sessions through a fresh bus only when needed, preserve session provenance across Bleak 3.0.2 and 3.1.0, and avoid redundant disconnects after confirmed teardown. Allow ESPHome's acknowledgement and slot-settling wait to finish. Fingerbot Touch failed handshakes use shared tracked cleanup while its notification deduplication and normal stop/write behavior are preserved.
- **LY curtain motor / Blind Controller (`cl` / `ulughw4g`) mappings** (#143): DP 105 is now auto-close temperature, not cover speed, and the work-state option order is corrected. Add temperature and fault sensors, closing-side and learning selects, and disabled-by-default reset and raw timer-schedule entities.

### Changed

- **Contributor CI labeling**: pull requests from forks now receive automatic labels.

## [2.10.0-beta.2] - 2026-11-13

> **Beta — for testing only.** This build supersedes beta.1 with protocol 4 connections and safer credential-probe retries. Test affected devices and report results on #39 before 2.10.0 ships.

### Added

- **Protocol 4 / FD50 BLE connections and datapoint reports** (#139): use the protocol 4 handshake and characteristics, and decode its status reports. This extends beta.1's identity-advertisement support to device connections; authenticated cloud-credential fallback remains limited to protocols 2 and 3.

### Fixed

- **Fingerbot Touch (`kg` / `bs3ubslo`)** (#137, fixes #132). Expose both channels and battery percentage, and clean up obsolete Fingerbot Plus controls for this product. Each channel has one switch that sends ON/OFF in Switch mode or triggers one press in Push mode. In Push mode, Home Assistant returns the switch to OFF after the configured touch duration. Per-channel settings include mode, touch duration, double-tap interval, touch input, inversion, and a separate OFF duration in Switch mode. The integration preserves existing channel switch IDs and improves BLE reconnect handling for this model.
- **Credential verification continues after failed candidates** (#141, refs #39): timeouts and silent disconnects receive one fresh-session retry before advancing through distinct UUIDs and remaining credentials. Malformed cloud metadata is isolated per record. Cleanup retains ownership of partially established connections, confirms native and proxy disconnection, and rejects proxy-session changes before connecting so retries cannot overlap unclosed links. Only authenticated expected datapoints can confirm a match; a sole cloud candidate is never trusted automatically.

## [2.10.0-beta.1] - 2026-11-12

> **Beta — for testing only.** This pre-release exists so the fix for the "unable to match cloud UUID" failures reported in #39 can be validated on real hardware before 2.10.0 ships. Install it from HACS with pre-releases enabled, test, and report back on #39.

### Added

- **Authenticated cloud-credential fallback when the BLE and cloud UUIDs differ** (#136): when a bound device's advertised UUID matches no cloud record, the config flow now ranks at most four complete, unused cloud candidates and probes them with a bounded, cancellable authenticated read — requiring an encrypted `DEVICE_INFO` response, an accepted PAIR, a status acknowledgement and a mapped datapoint decrypted with the session key, while refusing control, write, reset, unbind and OTA commands. Exact UUID matching remains the preferred path.
- **Protocol 4 / FD50 Tuya BLE identity advertisement decoding** (#134): identity advertisements are decoded and used during discovery, with unsupported or malformed records (empty or non-printable UUIDs, unverified protocol 5, compressed identities) rejected instead of falling back to an alternate key, and manual discovery completing before candidate filtering.
- **MaXpeedingRods MXR4000GT generator descriptor (`qt` / `jlblyn8z`)** (#135) and a **Battery BLE Roller descriptor (`jetlwvfm`)** (#129).

### Fixed

- **Discovery and setup validation for FD50 devices**: established sessions are preserved, vanished selector candidates are purged, concurrent candidate resolution no longer races setup, and deferred FD50 choices are kept without bypassing validation.

## [2.9.0] - 2026-10-30

### Added

- **SGS01B-JR smart soil sensor descriptor (`zwjcy` / `gpkyrocn`)** (#127, closes #123): the sensor connected and loaded but had no entities, because no descriptor existed for the product. It ships the SGS01 layout — soil temperature on DP 5 (scale 1), humidity on DP 3, battery state on DP 14 and battery percentage on DP 15 — copied verbatim from `zwjcy_gvygg3m8.yaml` as the reporter verified, with a registry test pinning both `zwjcy` descriptors to the same layout so they cannot drift apart.

### Changed

- **Development dependencies**: `homeassistant` 2026.10.2 → 2026.10.4 and `pytest-homeassistant-custom-component` 0.13.373 → 0.13.375 bumped together in one change (0.13.375 pins `homeassistant==2026.10.4`, so neither lands alone) (#126, #125), plus the SonarQube scan action 8.3.0 → 8.4.0 (#124).

## [2.8.0] - 2026-10-27

### Added

- **`dj` light category and Smart LED Wall Lamp (`joe8qqgiwhpqgwex`)**: `_category_dj.yaml` supplies the default `light` entity on the standard Tuya light data points — switch 20, work mode 21, brightness 10–1000, colour temperature 0–1000 and colour data 24 — because `dj` lights use these rather than the `dd` strip-light data points 1–5. The Smart LED Wall Lamp ships as an identity-only descriptor inheriting the category default. First contribution by @magicx78 (#120).
- **Fingerbot Plus descriptor for the `kg` product `6jcvqwh0`** (#121, fixes #104): the product existed only under `szjqr`, so a device reporting category `kg` logged "no descriptor" and loaded with no entities. The descriptor mirrors the other `kg` Fingerbot Plus layouts — switch, mode select, programs, battery, tap-to-run and custom program.

### Fixed

- **Overlapping datapoint writes can no longer restore a stale value** (#119, refs #116): `apply_with_rollback` holds a per-device lock across apply → send → rollback, so overlapping single-DP and bulk writes queue instead of snapshotting each other's unconfirmed optimistic values. A write cancelled mid-send now rolls back like any other failure, and out-of-range `DT_VALUE` writes — values that cannot serialise as a signed 32-bit int — are rejected before they are applied.

## [2.7.0] - 2026-10-27

### Added

- **Parkside smart battery descriptor for the 4 Ah PID `fab74vxu` (PAPP 204 A1)**: a third 2026-generation `dcb` battery descriptor sharing the `vllfabvs`/`r04rvmaq` data-point layout — including the DP 115 Identify switch and DP 105 work-mode select — with the corrected DP 102 status order, the corrected DP 101 unit and `PARKSIDE` as the manufacturer, catalogued in `SUPPORTED_DEVICES.md`.
- **Globo WiFi/BLE CCT ceiling light descriptor (`xdd`/`6tny2vqxktdzowt1`)**: on/off, work mode, brightness (10–1000) and colour temperature (0–1000) on the Tuya light template's data points. The light reports no `color_data` data point, so it declares colour temperature only, and `work_mode` is still driven so a light left in scene mode follows a colour-temperature write.

### Fixed

- **Battery status order and discharging-current unit on the 2026 Parkside batteries**: DP 102's `battery_status` options were shifted (Ready listed first while the devices report `Charging=0 .. Error=5`), so a charging battery showed _Ready_, a finished charge showed _Discharging_ and _Full_ never appeared; DP 101 `discharging_current` is in amperes but was declared as `mA`, so a drill load showed as 2 mA. Both descriptors also declare `manufacturer: PARKSIDE` instead of falling back to `tuya`. Refs #103.
- **Colour-temperature and colour changes reach the device again**: `TuyaBLELight.turn_on` sent the strings `white`/`colour` for the `DT_ENUM` work-mode data point, which raises `TuyaBLEDataFormatError`, so every such change rolled back without reaching the device (#113). The write now resolves through `_work_mode_write` — a `DT_STRING` work-mode data point gets the name, anything else gets the standard index (`white=0`, `colour=1`) — and `color_mode` treats index 0 as white mode. A single `turn_on` carrying both `hs_color` and `color_temp` now applies one work mode (explicit colour wins, the stray temperature write is dropped) instead of collapsing two work-mode writes in the batch. Raised by Sourcery.

## [2.6.0] - 2026-10-26

### Added

- **Parkside smart battery descriptors for the new 4 Ah and 8 Ah PIDs**: the 2026-generation Parkside smart batteries (`dcb`/`vllfabvs`, 4 Ah, and `dcb`/`r04rvmaq`, 8 Ah) now ship full YAML descriptors — battery percentage, pack and probe temperatures, charge/discharge current and voltage, charge and discharge cycle counters, the protection-event counters and fault state, alongside the tool-side sensors and configuration switches the battery reports. Both products are catalogued in `SUPPORTED_DEVICES.md`.
- **Battery work mode select**: DP 105 exposes the pack's `Performance`, `Balanced` and `Eco` work modes as a config select on both new battery descriptors, and DP 115 adds an Identify switch that makes the pack signal its location.

### Fixed

- **Local datapoint values now roll back when a BLE write fails**: `TuyaBLEDataPoint.set_value` applied its new value optimistically but only the bulk path restored it when the send raised, so every entity on the single-DP write path (switch, select, number, text, button, lock, cover, climate, valve and the Fingerbot programs) kept reporting a value the device never accepted until the next device report. The single and bulk paths now share one `apply_with_rollback` helper: it restores the applied values when the send fails _or_ a value cannot be coerced — so a failed batch is no longer left half-applied — skips data points that changed again while the write was in flight (a device report or a concurrent write wins over the stale snapshot), and treats an unacknowledged write (no response before `RESPONSE_WAIT_TIMEOUT`) as an error that rolls back, instead of silently staying optimistic. Raised as an issue by the Sourcery review of the previous release's bulk-write rollback.

## [2.5.1] - 2026-10-19

### Fixed

- **Diagnostics `cloud_info` no longer exports identifying fields**: the cloud device snapshot captured at setup copied every scalar field of the raw Tuya cloud payload, which — beyond version and product metadata — carries `ip`, `lat`, `lon`, `uid`, `owner_id`, `asset_id` and `time_zone`. Those reached the config entry and a diagnostics download (which the README asks users to attach to public issues). `capture_cloud_info` now uses an explicit allowlist of product/version fields (`category`, `product_id`, `product_name`, `product_model`, `pv`, `mcu_sw_version`, `sw_version`, `firmware_version`, `online`, `sub`, `support_local`) instead of a denylist, and stored snapshots captured under the old behaviour are filtered down on export as well — no entry rewrite needed. The same identifiers were also added to the diagnostics redaction set as defence in depth. Raised in the HACS default-submission review.

### Changed

- **`pycryptodome` is now a declared requirement**: `tuya_ble/base.py`, `tuya_ble/protocol_parse.py` and `tuya_ble/protocol_send.py` import `Crypto.Cipher`, but `pycryptodome` was only available because Home Assistant ships it for other integrations; `tuya-device-sharing-sdk` does not pull it in. It is now listed in `manifest.json` (`pycryptodome>=3.20.0`), per the HACS default-submission review.

## [2.5.0] - 2026-10-11

### Added

- **Five remaining migrated device descriptors**: the products left behind by the hardcoded-mapping → YAML descriptor migration now ship, each mirroring an existing sibling layout — HCT-611 water timer (`sfkzq`/`tqzkwarw`), LY curtain motor (`cl`/`ulughw4g`), HU06 Smart Lock (`jtmspro`/`stugc8dl`), TenixLox KB150A (`ms`/`0binhnjg`) and Pulido P13 (`ms`/`kdq0tdbz`). All five are catalogued in `SUPPORTED_DEVICES.md`.

### Changed

- **Protocol robustness** (`tuya_ble/`): decode well-formed 5-byte varints; isolate each registered callback so one failing callback can no longer abort the others; warn (with traceback) on recoverable connection/pairing errors; drop the `asyncio.sleep(0.01)` yield hack; use a byte-based local-key slice; pass `usedforsecurity=False` to every MD5 call; sort `__all__`; delete the dead `begin_update`/`end_update` machinery; and make `set_value_no_notify` type-strict (bytes-like for `DT_RAW`/`DT_BITMAP`, boolean for `DT_BOOL`).
- **Climate**: apply `temperature_unit` when either the target or the current temperature data point is present, and resolve presets through an explicit reverse map instead of relying on data-point order.
- **Cover / light / number**: gate cover features on their data points, batch light writes atomically, and round number values before sending.
- **Availability**: `available` now also requires a successful coordinator update, so entities go unavailable instead of showing stale values while disconnected.
- **Cleanup**: the dead `find_dpid`/`find_dpcode`/`_match_dpcode`/`get_dptype`/`_send_command` helpers are removed, the legacy unique-id suffix cache is evicted on unload, and `UNIT_CONVERSIONS`/`resolve_unit` move to `util.py` to be shared by `number.py` and `sensor.py`. The `wk` binary-sensor battery gets its name from `strings.json`/`translations/en.json`, and `CONTRIBUTING.md` states the Python 3.14.2+ floor.

### Fixed

- **HCT-611 water timer weather condition**: the `tqzkwarw` descriptor copied the SOP10 `no_forecast`/`sunny`/`cloudy`/`rainy` enum for DP 13, but the HCT-611 reports `sunny`/`clear`/`cloud`/`cloudy`/`rainy`/`snow`/`fog`. The options now match the device, so `clear`, `cloud`, `snow` and `fog` no longer render as unknown.
- **Descriptor schema validation**: climate `hvac_modes` and `hvac_switch_mode` are validated against `HVACMode` at load time rather than failing later at runtime.

## [2.4.0] - 2026-10-10

### Changed

- **Protocol library split**: the monolithic `protocol_mixin.py` is split into `protocol_send.py` (packet build/encrypt/send), `protocol_parse.py` (decrypt/parse/dispatch) and `protocol_notify.py` (fragment reassembly), composed by a thin `protocol_mixin.py` as a linear `Send → Parse → Notify` chain. Every public import path is preserved — `TuyaBLEProtocol`, `BLEAK_EXCEPTIONS` and `BLE_CONNECTION_EXCEPTIONS` are still re-exported from `tuya_ble/__init__.py`. No behaviour change.
- **Descriptor registry loaded off the event loop**: the YAML descriptor registry is warmed in an executor before the first synchronous lookup, so cold-start product lookups, platform imports, the coordinator and diagnostics all read an in-memory cache and no longer trip Home Assistant's blocking-call warnings during setup.
- **Two `async` methods that performed no I/O**: `TuyaBLEDevice.start()` was a no-op and `initialize_with_credentials()` only derived keys synchronously, yet both were coroutines. `start()` is removed and `initialize_with_credentials()` is now synchronous, clearing the last two SonarQube code smells and the technical debt they carried.
- **Development dependencies**: bumped to Home Assistant 2026.10.0, matching the `homeassistant` requirement in `hacs.json`.

### Added

- **Cloud device snapshot captured at setup**: setup snapshots the scalar, JSON-safe attributes of the Tuya cloud device — including firmware/version fields when the sharing payload carries them — under `CONF_CLOUD_INFO`, stamped with a `captured_at` time. Credentials and blobs captured elsewhere (`local_key`, `function`, `status_range`, `local_strategy`, `status`) are excluded, as is any key that looks credential-shaped, so the snapshot stays JSON-safe. It is exposed as a read-only `cloud_info` diagnostics section and replaced by a fresh (never persisted) snapshot when a refresh is possible. The `tuya-device-sharing-sdk` exposes no firmware/OTA endpoint, so firmware is captured only when the device payload happens to carry it.
- **Status and quality badges**: the README badge block now mirrors the sibling `modbus_local_gateway` repository — build status, stars, issues and licence, then Lines of Code, Ruff, Duplicated Lines and Coverage, then the SonarCloud quality metrics (vulnerabilities, security/maintainability/reliability ratings, quality gate, code smells, bugs and technical debt).

### Fixed

- **Diagnostics schema provenance**: `schema_source` collapsed to `missing` for any entry without a captured `local_schema` blob, even when the cloud schema was fully populated. Provenance now reports the actual source — `cloud_refresh` (re-fetched from the cloud), `stored` (a captured `local_schema` blob), `legacy` (no blob but `CONF_FUNCTIONS`/`CONF_STATUS_RANGE` present) or `missing` — and `schema_captured_at` stays `null` rather than inventing a timestamp when the capture time is unknown.
- **Notification reassembly resynchronisation**: a fresh `packet 0` arriving below the expected packet number now restarts reassembly instead of being logged as an unexpected packet and discarding the buffer, so a device that restarts a fragmented message mid-stream is decoded again rather than going quiet for the rest of the session. Genuinely stale packet numbers are still discarded.
- **Send-response timeout diagnostics**: the timeout log now includes the sequence number and response code alongside the address and RSSI, so repeated timeouts at weak signal can be diagnosed.

## [2.3.0] - 2026-10-01

### Changed

- **YAML descriptors are the single source of product metadata**: `products.py` duplicated metadata the descriptors already carried, and `__init__.py` rejected any device missing from that duplicate with `ConfigEntryNotReady: Unknown device: ...` even when a valid descriptor existed. `products.py` is deleted; `TuyaBLEProductInfo` is now a frozen view over a descriptor, built by `from_descriptor()`. The Fingerbot and water valve data point ids are derived from the entities themselves via `DeviceEntities.dp_id_for(platform, translation_key)` — mode is the `fingerbot_mode` select, program is the `program_idle_position` number, and a water valve is the product with a `valve` entity — which retires the dead `lock`, `up_position`, `down_position`, `hold_time` and `reverse_positions` fields. Because every data point a shared handler reads is now declared as an entity, `get_mapped_dp_ids()` no longer needs `products` at all, and `sfkzq/16wgjvck` DPs 10 and 13 stopped being counted as mapped while no entity used them.
- **Unknown products no longer block Home Assistant startup**: a device with no descriptor now loads with no entities instead of retrying its config entry forever. This also makes `cl/qqdxfdht` and `ms/bvclwu9b` reachable — they only ever had a descriptor, so they were previously unusable.
- **Manufacturers are declared in descriptors**: products can carry a `manufacturer`, and the missing ones were added for Comfamoli, Diivoo, Ferrex, Insoma, Magiacous (two products) and Yohgee. Without it the device showed up as an unbranded Tuya device. A descriptor manufacturer takes precedence over the product-level one, and both fall back to Tuya.
- **Descriptor schema reference**: the entity-field tables in `CONTRIBUTING.md` now document `force_add`, `restore` and `legacy_keys`, along with the `min_length`/`max_length` and `unit_dp_id` fields added below, and describe `dp_type` accurately as a `select`-only wire type rather than a type override. The handler list is corrected — it still listed the removed `co2.alarm_enabled` and omitted `raw.raw_hex`. Inert `dp_type` declarations are removed from the SOP10 battery sensors, which never took effect because sensors are force-added; a test now rejects `dp_type` on any sensor descriptor so they cannot reappear silently.
- **CO2 status sensor**: the alarm state of the CO2 detector (`co2bj`, DP 1) is now always available and reports `alarm` or `normal` as the device sends it. It was previously hidden whenever the alarm switch (DP 13) was off, although the device reports the status independently of that switch. The sensor is also renamed to `CO2 status` to match the function name in the device's own definition; existing entities are adopted under their previous unique id, so nothing is recreated.
- **`kind` removed from device descriptors**: the `kind` field and the eight mapping classes it selected are gone. They never had any effect: each platform builder always passed an explicit entity description, so a kind class's defaults could not apply, and the availability defaults were always superseded by the descriptor's own `when` handler. The 153 `kind:` lines are removed from the descriptors, and a descriptor that still declares one is now rejected with an error rather than silently ignored. The `sfkzq` valve switch's availability gate, the only one that was doing real work, is preserved as an explicit `when` handler. Behaviour is otherwise unchanged: a before/after comparison of all 443 built entities is identical apart from the `name` field of the 13 fingerbot mode selects, which Home Assistant normalises either way.
- **Battery and temperature sensor descriptors**: these are now asserted to carry the fields Home Assistant needs — `device_class: battery` requires `unit: "%"`, `state_class: measurement` and `entity_category: diagnostic`; `device_class: temperature` requires `unit: "°C"` and `state_class: measurement`. The one descriptor that fell short, the battery sensor of the `sfkzq` `ldcdnigc` irrigation controller, is now marked as a diagnostic like every other battery sensor, which moves it into the diagnostics section of the device.

### Added

- **Device diagnostics**: downloading diagnostics now reports the device, its live data points, the resolved descriptor entities, the cloud schema and the Home Assistant side of the config entry. The unfiltered local strategy is persisted under `CONF_LOCAL_SCHEMA`, keeping `statusFormat`, `enumMappingMap` and `valueConvert` — which is what the DP value handlers actually read — plus the full status range with the SDK-resolved `report_type` and a `captured_at` timestamp, so a download can be matched to a report and a mis-scaled value explained. Credentials are redacted: `local_key`, `terminal_id`, `user_code`, `endpoint` and the token blob are removed, while `uuid` and `device_id` stay readable for exactly that matching. The schema is refreshed from the cloud only when a re-auth left tokens in `entry.options`, and diagnostics fall back to the stored snapshot otherwise, so they never fail and never persist new credentials. It replaces the raw/unmapped data point debug logs added in 2.0.0.
- **Tuya category reference**: `docs/TUYA_CATEGORIES.md` documents the 14 categories this integration supports, what each one is, the log level emitted for an unsupported category, and the default `ms` lock descriptor, so a device from an unlisted category can be recognised from its logs.
- **A new smart cylinder lock** (`jtmspro/ebd5e0uauqx0vfsp`, CentralAcesso Smart Cylinder Lock) with its alarm status sensor, an enum covering the thirteen outcomes the lock reports — wrong finger, wrong password, wrong card, wrong face, tongue bad, too hot, unclosed time, tongue not out, pry attempt, key in use, low battery, power off and shock.
- **Restart persistence**: entities can opt into restoring their last known value across a Home Assistant restart with the new `restore: true` descriptor flag. A restored value is only applied while the device has not reported the data point, and availability still follows the connection state. Used by the Diivoo dual water timer for battery, last use time, zone operation status, irrigation schedules and weather delay.
- **Status re-query on connect**: the coordinator now asks the device for its full status once per connection, so read-only data points no longer keep the values of the previous session until the device next pushes them. A request that fails on a transient BLE drop is now consumed instead of being reported a second time as an unretrieved task exception.
- **Dual water timer**: new diagnostic entities for the previously unmapped BLE-only data points of product `fdrbxxbg` — zone operation status (DP 112/113, reported as `manual`/`auto`/`idle`), the fault code (DP 19, problem binary sensor) and the two irrigation schedules (DP 101/102, raw payload as hex, disabled by default).
- **`min_length` / `max_length` on text entities**: a descriptor can now bound the length of a text entity through the new `min_length` and `max_length` fields, wired to `TextEntityDescription.native_min`/`native_max`. Applied to the Fingerbot program text (0–255 characters).

### Fixed

- **Incomplete Fingerbot Plus descriptors**: three descriptors (`szjqr_6jcvqwh0`, `szjqr_h8kdwywx`, `szjqr_riecov42`) shipped without the push button, the mode select or the program text, and fourteen set a `down_position` floor of 51, making the halfway position unreachable by hand. They now match the complete `szjqr_blliqpsj` descriptor these products share hardware with, the mode select was added to `szjqr_yn4x5fa7` whose mode-dependent entities were gated on a mode no entity exposed, and the floor is 50. Tests assert the invariants: a descriptor gating on `fingerbot.mode.*` must declare a `fingerbot_mode` select on the data point declared as its mode, the three options keep their wire order, and a `down_position` reaching 50% must not start above it.
- **Missing entities on the Ferrex Smart Water Valve** (`sfkzq/16wgjvck`): weather (DP 13, enum sensor) and weather delay (DP 10, select) were absent although all three sibling `sfkzq` products define them. Added, with the enum options for the weather condition taken from the siblings.
- **Unknown products are logged at warning**: since a descriptor-less product now loads with zero entities instead of failing, a device whose descriptor is renamed or dropped appears in the UI with no entities and nothing above debug. The message is now a warning; it still fires once per config entry and never for a device that has a descriptor.
- **`tuya-device-sharing-sdk` is no longer pinned to an exact version**: the manifest required `==0.2.15`, so a bug fix in the SDK could not be picked up without a release of this integration. It now requires `>=0.2.15`.
- **SOP10 weather delay select**: selecting a weather delay on the SOP10 water timers (`nxquc5lb`, `c8800fd30884068f`, `so5ybnw9`, DP 10) failed with a data-format error instead of writing the option. The category definition types `weather_delay` as an `Enum` over `cancel`/`24h`/`48h`/`72h`, but the descriptor declared it as a string table with a parallel `values` list, so the option name was fed to a data point the device had already reported as an enum. The option index is now written as the enum code, matching the `ggq` family. Reading was unaffected, which is why the state displayed correctly while writing never worked.
- **Operation status labels**: the zone operation status of the Diivoo dual water timer (DP 112/113) and the operation sensor of the SOP10 water timer (DP 12) now report the device's own state names. The `ggq` and `sfkzq` families order the same three states differently — `manual`/`auto`/`idle` against `auto`/`manual`/`idle` — as confirmed from the category definitions, and both are declared in the device descriptors instead of a Python handler. The previous mapping reported code `0` as `watering`, which the device never sends.
- **Enum data points outside their options**: an enum sensor no longer reports a code that is not in its declared options, which Home Assistant rejects as an invalid entity state and which left the entity frozen until it was reloaded. The value is now reported as unknown and the raw code is logged at debug level. A value restored by a previous run is likewise discarded when it is no longer one of the offered options.
- **Weather delay select**: the Diivoo dual water timer option table is replaced with the codes captured from the live devices (`cancel`, `24h`, `48h`, `72h` as enum codes `0`–`3`) for products `fdrbxxbg`, `jntxv3q4` and `qycalacn`. The previous `dp_type: 3` string table with an `OFF` value sent no packet at all when `Off` was selected, because the non-numeric value cannot be serialized as an enum. The codes were captured from `fdrbxxbg`; the two sibling products share the table because the old one could not send any option, but their numbering still needs confirming on the device.
- **Boolean data points read as bitmaps**: a raw or bitmap payload is now interpreted as a set of flags instead of `bool(bytes)`, which reported every payload, including a zeroed one, as "on". This affects the fault code of `fdrbxxbg` and any other boolean entity whose data point arrives as a bitmap.
- **A fragmented BLE message could wedge the receive buffer**: a device that announced a length in packet 0 and then stopped sending left its partial buffer in place indefinitely, so every later notification was appended to it and no data point was parsed again for the rest of the session. The announced length is now bounded by `MAX_INPUT_LENGTH` (255 fragments of 17 bytes, the most the MTU allows) and a partially reassembled message is dropped after `INPUT_REASSEMBLY_TIMEOUT` (5 s), so the stream resynchronises instead of going permanently quiet. A header or frame too short to hold its declared length is rejected as a data-length error rather than unpacked, and the two signing handlers and the input decryption no longer index into a short or empty payload — they raise a Tuya BLE error instead of an `IndexError` inside the Bleak callback.
- **CRC verification was silently disabled after repeated errors**: a device whose frames failed the CRC check three times had verification switched off, after which every subsequent frame was accepted unverified. The counter is reset instead, and the two-byte CRC is now always verified on a complete reassembled frame — it is the only integrity check this protocol has, since the security flag and the IV sit outside the payload it covers.
- **Duplicate reconnect and resend tasks**: a failed GATT write reaches both the disconnect callback and the send error handler, and each rescheduled reconnect spawned another, so the task population doubled on every event until it saturated the event loop. Reconnects and resends now go through a single helper that keeps at most one pending, skips the work during an intentional stop, and logs an exception escaping a background task instead of leaving it to asyncio's unretrieved-exception report at garbage-collection time.
- **Stopping a device could block and hang its callers**: `stop()` set the expected-disconnect flag only after taking the connect lock, so it queued behind an in-flight connect's whole retry loop, and any sender awaiting a response was left waiting out its full timeout. The flag is now set before the locks are taken, the background tasks are snapshotted safely, and every pending response future is failed immediately.
- **`AttributeError` was treated as a transient BLE failure**: `BLEAK_EXCEPTIONS` included it, so a programming error — a typo'd attribute or a missing protocol member — was retried silently and presented as a flaky device. It is now excluded from the tuple, while reconnect catches the wider `BLE_CONNECTION_EXCEPTIONS`, so a device with no session key or no device info keeps retrying instead of being left permanently disconnected.
- **A failed multi-value write was reported as a success**: `set_multiple_values` applied the new values locally before sending and left them in place when the send failed, so Home Assistant displayed a value the device had never accepted. It now snapshots and reverts the local values on any failure, checks the protocol version before writing, warns about ids it had to skip, and returns early when nothing is sendable.
- **An out-of-range data point raised a bare `struct.error`**: the v3 envelope carries the dp id and the serialised length in single-byte fields, so a dp id above 255 or an oversized value escaped as `struct.error` into the entity layer. Both are validated before the packet is built and reported as `TuyaBLEOutgoingDataLengthError`, and the pairing frame is length-checked where a padding loop was a no-op, so oversized credentials are rejected instead of sending a malformed frame.
- **HVAC mode was written to the humidity data point**: setting a climate entity's mode wrote the mode index to `target_humidity_dp_id`, so the mode never reached the device and the target humidity was overwritten instead. It is now written to `hvac_mode_dp_id`.
- **Temperature sensors ignored the unit the device is set to**: a device with a `temperature_unit` select reports in whichever unit is selected, but the sensor always declared `°C`, so a device set to °F was recorded as if it were °C. A new `unit_dp_id` on the sensor mapping follows the select, wired for all three descriptors that expose one (`co2bj_59s19z5m`, `wsdcg_ojzlzzsw` and `znhsb_cdlandip`).
- **Water intake was not recorded as a cumulative total**: the `znhsb_cdlandip` water intake sensor combined `state_class: measurement` with `device_class: water` and a unit Home Assistant does not accept for that class, so a rising counter was stored as an instantaneous reading. It is now `total_increasing` with an icon carrying the meaning.
- **A bitmap write could clear a sibling entity's bit**: when a data point is wider than an entity's `bitmap_mask`, the width mismatch used to raise, and once it was tolerated the write built the new payload from the paired bytes alone — truncating it to the mask width and silently resetting any sibling entity sharing those bits. The untransformed suffix is now preserved, and a mismatch warns rather than raising.
- **Fingerbot program text was over-validated**: the pattern required at least two `position/duration` steps and capped the hold time at two digits, so a single-step or 20-step program was rejected by Home Assistant although the device accepts it. The pattern now accepts an empty, single-step or 20-step program, its length is bounded by the new `min_length`/`max_length` fields, and the program handler bounds its payload access by length with the step loop clamped to the bytes actually present, so a short payload raises a Tuya error instead of an `IndexError`.
- **A scalar function value was JSON-encoded**: `TuyaBLEDeviceFunction.__setattr__` ran `json.dumps` on every value, so a plain string was stored wrapped in quotes and an `int` or `bool` from the cloud could reach the `DPType` lookups that expect a string or container. Only dict and list values are unwrapped now; scalars are kept as strings.

## [2.2.0] - 2026-09-04

### Changed

- **Descriptor-driven device and entity names**: the YAML descriptors became the single source of truth for how devices and entities are named. The device **model** now falls back to the descriptor `model_name`, which previously left the model empty in the device registry for every YAML-driven device. The device **name** is resolved as cloud name, then descriptor `device_name`, then the advertised BLE name, then the address — `TuyaBLEDevice` gained `cloud_name` and `advertised_name` properties so the cloud-set name can be told apart from the advertised one. Entity descriptors accept an optional literal `name` override, which Home Assistant only uses when no translation exists for the `translation_key`, so an entity without a `strings.json` entry is no longer silently named after the device. All 50 descriptor files gained a `name` on each of the 259 entities that lacked one, sourced from tuya-local device configs where a counterpart exists and sensible English defaults elsewhere. `device_registry.py` now rejects a non-string `device_name`/`model_name` instead of passing it through, and the registry lookup short-circuits when the device has no category or product id rather than force-loading every descriptor.

### Removed

- The dead `kind: hold_time` keys from ten `szjqr` number entities, and the unused `TuyaBLEHoldTimeMapping` class with them.

## [2.1.0] - 2026-08-31

### Changed

- **Data-driven device configuration**: replaced the hardcoded per-platform Python `mapping` dicts across all 12 platforms (binary_sensor, button, climate, number, select, sensor, switch, text, valve, lock, cover, light) with YAML device descriptors (`device_descriptors/`), loaded and validated by the new `device_registry.py`. Device-specific and category-level defaults are expressed declaratively, with shared handler callables (`device_descriptors/handlers/`) replacing inline Fingerbot/sensor helpers. Adds a new device by editing a single YAML file instead of touching multiple platform files.
- **Fingerbot consolidation**: `fingerbot.py` removed; its helpers moved to `device_descriptors/handlers/fingerbot/` (`mode.py`, `program.py`). `devices.py` is now a pure re-export shim and no longer re-exports the old convenience helpers.
- **SOP10 config**: re-aligned the water timer device config (product IDs `nxquc5lb`, `c8800fd30884068f`, `so5ybnw9`) with the reference, moving the countdown to DP 11 (was DP 8) and last use time to DP 15; weather-delay select now uses the `cancel`/`24h`/`48h`/`72h` string values; work state is surfaced as a plain string.
- **Dual water timer config**: align the Diivoo WT-05 family device config (product IDs `fdrbxxbg`, `jntxv3q4`, `qycalacn`) with the reference. Fix the zone 1/zone 2 last-use-time DP swap (111/110), set operation-mode DPs 112/113 as plain string sensors (`manual`/`auto`/`idle`), and make the weather-delay selects (117/114) write the device's string day values (`OFF`/`1`–`7`, shown as `Off`/`1 day`..`7 days`). The countdown range now starts at 0 min.
- **Dual water timer devices**: add product ID `jntxv3q4` (Insoma) and extend `qycalacn` (Yohgee) so the whole family shares the dual water timer mapping across valve, switch, number, select and sensor platforms.

### Added

- New YAML descriptors for NM2-style curtain controller (`cl_dy4dh1q0`) and LED strip light (`dd_nvfrtxlq`).
- New test files `tests/test_device_registry.py` and `tests/test_handlers.py`.
- **SOP10**: weather forecast (DP 13), last use time (DP 15), soak schedule (DP 16) and irrigation schedule (DP 17) sensors, plus a fault-code problem binary sensor (DP 4), for water timer products `nxquc5lb`, `c8800fd30884068f` and `so5ybnw9`.

## [2.0.4] - 2026-08-30

### Changed

- **Device identity**: match Tuya devices by their stable Bluetooth UUID rather than requiring an ASCII-decodable product ID, enabling support for devices (e.g. the Diivoo) whose product ID cannot be decoded from the advertisement.
- **Cloud metadata**: skip device function/status metadata that lacks a DP ID instead of failing setup, and refactor `append_functions` into a shared helper.
- **Device config**: re-categorize Diivoo/SOP10 water timer config from `sfkzq` to `ggq` with updated DP re-maps.

### Fixed

- **BLE notifications**: broaden `_safe_notification_handler` so malformed frames (`struct.error`, `ValueError` from AES) are caught and logged instead of leaking into the Bleak callback.

## [2.0.3] - 2026-08-29

### Fixed

- **Tuya cloud setup**: decode the product ID and UUID from BLE advertisements and match the UUID directly against the QR-authenticated cloud device cache, removing the unsupported factory-information API lookup.

## [2.0.2] - 2026-08-29

### Changed

- **Test tooling**: added a manual `prek` pytest hook that runs the complete suite with branch coverage.

### Fixed

- **Tuya cloud setup**: use the supported user-permission factory-information endpoint and handle rejected API requests without crashing the Home Assistant config flow.

## [2.0.1] - 2026-08-29

### Changed

- **Test refactor**: converted all 88 class-based pytest test classes across 10 test files to module-level functions, aligning with the project's code-review standard. Shared `self._` helpers were lifted to module-level helper functions. No runtime behavior changed; suite remains at 98% branch coverage.

## [2.0.0] - 2026-08-28

### Added

- **New entity platforms**: lock, cover, and light platforms.
  - Lock: smart locks (`ms`, `jtmspro`) with lock/unlock, alarm events, door status, fingerprint/card/password/BLE unlock tracking, and battery status.
  - Cover: blind and curtain controllers (`cl`) with open/close/stop and battery, work state, speed entities.
  - Light: LED strip lights and lamps (`dd`) with on/off, brightness, color temperature, and RGB color.
- **New device categories**:
  - `dcb`: PARKSIDE Smart batteries (4Ah, 8Ah) with battery, temperature, charge/discharge current/voltage, tool diagnostics, fault counters, and configuration switches.
  - `co2bj`: CO2 Detector with bitmap alarm switches.
- **Water valve enhancements**: `sfkzq` category expanded with 8+ products; valve entities with weather delay, smart weather, countdown, and use time.
- **Dual-outlet irrigation** (`ggq`): separate water valve and countdown entities for each outlet.
- **Thermostatic Radiator Valve** (`wk`): expanded with window check, antifreeze, child lock, water scale proof, and programming mode switches.
- **`TuyaBLEDeviceFunction`** dataclass for cloud-spec DP definitions with JSON-parsed values.
- **`function` / `status_range` properties** on `TuyaBLEDevice` populated from stored credentials — enables offline DP lookups.
- **`append_functions()` method** on `TuyaBLEDevice` to parse credential lists at init.
- **Fully offline after config flow**: credentials (uuid, local_key, device_id, category, product_id, functions, status_range) persisted in the config entry data. `OfflineTuyaBLEDeviceManager` replaces the cloud manager at runtime — no cloud connection needed after initial setup.
- **`set_multiple_values()`** on `TuyaBLEDevice` for atomic multi-DP BLE writes.
- **`send_multiple_dp_values()`** on `TuyaBLEEntity` for entity-level atomic writes.
- **`find_dpid()`, `find_dpcode()`, `get_dptype()`** helper methods on `TuyaBLEEntity` for dynamic DP resolution using cloud-spec definitions.
- **`IntegerTypeData` and `EnumTypeData`** dataclasses in `base.py` for parsing cloud device specs (min/max/scale/range).
- **`DPType` and `DPCode` enums** in `const.py` — cloud-spec DP types and ~250 standard DP code names.
- **`remap_value()`** utility function in `util.py`.
- **Comprehensive test suite**: 690 tests covering all entity platforms, config flow, device protocol, datapoints, and product registry with 98% branch coverage.
- **Dev tooling**: pyproject.toml, prek hooks (ruff, ruff-format, pylint, mypy, cspell, yamllint, prettier), pytest config, devcontainer support.
- **Project docs**: AGENTS.md, CONTRIBUTING.md, SECURITY.md, GitHub issue templates.

### Changed

- **Major refactor**: split `tuya_ble.py` (1200 lines) into `base.py`, `datapoints.py`, `protocol_mixin.py`. All public symbols re-exported from `tuya_ble/__init__.py`.
- **Authentication**: replaced deprecated `tuya-iot-py-sdk` with `tuya-device-sharing-sdk` (Manager / CustomerApi). Tokens refreshed through a `SharingTokenListener`.
- **SonarQube fixes**: resolved high-signal findings — S8508 mutable default arguments, S1192 duplicated string literals, S3776 cognitive complexity (extracted helpers), S8519 `list()[0]` -> `next(iter(...))`.
- **Valve entities**: moved irrigation computer valve from `switch` to `valve` platform.
- **`set_16wgjvck_water_valve()`** now uses `send_multiple_dp_values()` for atomic multi-DP writes instead of sequential calls.

### Fixed

- `TuyaBLEDataPoints.update_from_device` public API for driving data point updates in tests.
- Entity coordinator listener registration moved to `async_added_to_hass()` for proper HA lifecycle.

## [0.1.9] - 2026-08-25

### Added

- Added support for water valve controllers (category 'sfkzq'): Diivoo WT-05 dual water timer and SOP10 water timer.
- Added `valve` platform for native HA valve entities (open/close/stop with water device class).
- Moved existing irrigation computer valve from `switch` to `valve` platform.

## [0.1.8] - 2023-07-09

### Added

- Added support of 'Irrigation computer', thanks to @SanMiggel.
- Added new product_ids for Smart locks, thanks to @drewpo28.

### Changed

- Connection to the device is postponed now. Previously some out of range device might prevents HA from fully booting.
- Improved connection stability.

## [0.1.7] - 2023-06-01

### Added

- Added new product_ids.
- Added full support of BLE TRV provided by @forabi
- Added support of programming mode for Fingerbot Plus, thanks @redphx for information.

### Changed

- Improved connection stability.

## [0.1.6] - 2023-06-01

### Added

- Added new product_ids for Fingerbot and Fingerbot Plus.

### Changed

- Updated sources to conform Python 3.11

## [0.1.5] - 2023-06-01

### Added

- Added new product_ids for Fingerbot.
- Added event "fingerbot_button_pressed" which is fired on Fingerbot Plus touch button press.
- First attempt to add support of climate entity.

## [0.1.4] - 2023-04-30

### Added

- Added support of CUBETOUCH 1s, thanks @damiano75
- Added new product_ids for Fingerbot.
- Added new product_ids for Fingerbot Plus.
- First attempt to support Smart Lock device.

### Fixed

- Fixed possible disconnect of BLE device.

## [0.1.2] - 2023-04-26

### Changed

- Changed a way to obtain device credentials from Tuya IOT cloud, possible fix to (#2)

## [0.1.1] - 2023-04-26

### Added

- Added new product_id for Fingerbot Plus (#1)

### Fixed

- Fixed problem in options flow.

### Changed

- Updated strings.json

## [0.1.0] - 2023-04-22

- Initial release
