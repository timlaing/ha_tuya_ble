# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog],
and this project adheres to [Semantic Versioning].

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
