# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog],
and this project adheres to [Semantic Versioning].

## [Unreleased]

### Changed

- **YAML descriptors are the single source of product metadata**: `products.py` duplicated metadata the descriptors already carried, and `__init__.py` rejected any device missing from that duplicate with `ConfigEntryNotReady: Unknown device: ...` even when a valid descriptor existed. `products.py` is deleted; `TuyaBLEProductInfo` is now a frozen view over a descriptor, built by `from_descriptor()`. The Fingerbot and water valve data point ids are derived from the entities themselves via `DeviceEntities.dp_id_for(platform, translation_key)` — mode is the `fingerbot_mode` select, program is the `program_idle_position` number, and a water valve is the product with a `valve` entity — which retires the dead `lock`, `up_position`, `down_position`, `hold_time` and `reverse_positions` fields. Because every data point a shared handler reads is now declared as an entity, `get_mapped_dp_ids()` no longer needs `products` at all, and `sfkzq/16wgjvck` DPs 10 and 13 stopped being counted as mapped while no entity used them.
- **Unknown products no longer block Home Assistant startup**: a device with no descriptor now loads with no entities instead of retrying its config entry forever. This also makes `cl/qqdxfdht` and `ms/bvclwu9b` reachable — they only ever had a descriptor, so they were previously unusable.
- **Manufacturers are declared in descriptors**: products can carry a `manufacturer`, and the missing ones were added for Comfamoli, Diivoo, Ferrex, Insoma, Magiacous (two products) and Yohgee. Without it the device showed up as an unbranded Tuya device. A descriptor manufacturer takes precedence over the product-level one, and both fall back to Tuya.
- **Descriptor schema reference**: the entity-field tables in `CONTRIBUTING.md` now document `force_add`, `restore` and `legacy_keys`, and describe `dp_type` accurately as a `select`-only wire type rather than a type override. The handler list is corrected — it still listed the removed `co2.alarm_enabled` and omitted `raw.raw_hex`. Inert `dp_type` declarations are removed from the SOP10 battery sensors, which never took effect because sensors are force-added; a test now rejects `dp_type` on any sensor descriptor so they cannot reappear silently.
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
