# AGENTS.md

## Project

Home Assistant custom integration (HACS) for Tuya BLE devices. Handles encryption and device protocol locally over Bluetooth — no cloud dependency after initial key exchange.

Forked from [PlusPlus-ua/ha_tuya_ble](https://github.com/PlusPlus-ua/ha_tuya_ble), maintained by [@timlaing](https://github.com/timlaing/ha_tuya_ble).

## Tests

A pytest unit-test suite lives in `tests/` (721 tests, 98% branch coverage of `custom_components/tuya_ble`, incl. entity platforms and config flow). CI runs it on every push/PR (`.github/workflows/test.yml`) with a `--cov-fail-under=90` gate, and `lint.yml` runs `prek run --all-files`. Run locally with coverage:

```sh
.venv/bin/python -m pytest --cov=custom_components.tuya_ble --cov-branch --cov-report=term-missing
# or just the unit tests without coverage:
.venv/bin/python -m pytest -q
```

`pyproject.toml` configures `testpaths=["tests"]`, `asyncio_mode="auto"`, and `addopts=["--disable-socket", "--allow-unix-socket", "--timeout=2"]`.

### Test conventions

- Tests import via `custom_components.tuya_ble.*` (never bare `tuya_ble.*`); `custom_components/` is added to `sys.path` in `tests/conftest.py`. **Do not** add `custom_components/tuya_ble` itself to `sys.path` — its `select.py`/`text.py` etc. shadow stdlib modules.
- Tests exercise **public** entry points. Setting device/flow internals (`_client`, `_session_key`, `_manager`, name-mangled login state) as **test setup state** is acceptable.
- Real `TuyaBLEDevice` + `TuyaBLECoordinator` are built in tests; the device's `send_datapoints` is stubbed so no BLE I/O occurs. Data points are driven via the public `TuyaBLEDataPoints.update_from_device`.
- Entity classes register their coordinator listener in `async_added_to_hass()`, not `__init__` — call `await entity.async_added_to_hass()` before asserting on coordinator-triggered updates, or call the handler directly.
- `products.py`'s `devices_database` is a pure registry: `get_product_info_by_ids`, `get_device_product_info`, `get_short_address`, `get_device_info` are unit-tested directly (via the `devices.py` shim in `test_devices.py`).
- The `hass` fixture comes from `pytest-homeassistant-custom-component`; config-flow tests build flow objects directly and drive `async_step_*`, patching `config_flow.HASSTuyaBLEDeviceManager` and the (name-mangled) `login_control`/`qr_code`/`login_result` with fakes.
- Tests that mock `asyncio.create_task` or `asyncio.sleep` should use a module-level `pytestmark = pytest.mark.filterwarnings("ignore::RuntimeWarning")` and a `_close_task()` helper that calls `coro.close()` on the mocked coroutine to prevent unawaited-coroutine warnings from leaking into other tests via `gc.collect()`.

### Coverage requirements

Every Python file under `custom_components/tuya_ble/` **must** meet these minimums:

- **Line coverage > 90 %**
- **Branch coverage ~ 100 %** (cover all `if`/`elif`/`else`/`match` arms)

Run coverage after writing tests:

```sh
.venv/bin/python -m pytest --cov=custom_components.tuya_ble --cov-branch --cov-report=term-missing
```

If a file falls below the threshold, add tests until it passes before committing.

### Test files

| File                                                                       | Covers                                                                                            |
| -------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| `tests/conftest.py`                                                        | `sys.path`, fakes (`FakeBLEManager`, `FakeBleakClient`, …), `make_device()`, `make_credentials()` |
| `tests/protocol_harness.py`                                                | packet building/encryption helpers for protocol tests                                             |
| `tests/test_datapoints.py`                                                 | `TuyaBLEDataPoint`/`TuyaBLEDataPoints`                                                            |
| `tests/test_manager.py`, `tests/test_const.py`, `tests/test_exceptions.py` | manager, consts, exceptions                                                                       |
| `tests/test_protocol.py`                                                   | `protocol_mixin` packet/AES logic                                                                 |
| `tests/test_base.py`                                                       | `base.IntegerTypeData`/`EnumTypeData`                                                             |
| `tests/test_device.py`                                                     | `TuyaBLEDevice` connection/state (mocked connect flow)                                            |
| `tests/test_connection.py`                                                 | BLE connection lifecycle, error paths, protocol edge cases                                        |
| `tests/test_cloud.py`                                                      | `cloud.py`                                                                                        |
| `tests/test_devices.py`                                                    | pure devices.py functions                                                                         |
| `tests/test_mappings.py`                                                   | per-platform `get_mapping_by_device` + pure Fingerbot/sensor helpers                              |
| `tests/test_device_registry.py`                                            | `device_registry.py` (load/validate/resolve, `EntityDescriptor`)                                  |
| `tests/test_handlers.py`                                                   | YAML descriptor handler callables (`battery`, `co2`, `rssi`, `water_valve`, Fingerbot)            |
| `tests/test_entity_binary_sensor.py`                                       | binary_sensor entity methods                                                                      |
| `tests/test_entity_button.py`                                              | button entity methods                                                                             |
| `tests/test_entity_climate.py`                                             | climate entity methods                                                                            |
| `tests/test_entity_number.py`                                              | number entity methods (incl. fingerbot number handler aliases)                                    |
| `tests/test_entity_select.py`                                              | select entity methods                                                                             |
| `tests/test_entity_sensor.py`                                              | sensor entity methods                                                                             |
| `tests/test_entity_switch.py`                                              | switch entity methods                                                                             |
| `tests/test_entity_text.py`                                                | text entity methods                                                                               |
| `tests/test_entity_valve.py`                                               | valve entity methods                                                                              |
| `tests/test_entity_lock.py`                                                | lock entity methods                                                                               |
| `tests/test_entity_cover.py`                                               | cover entity methods                                                                              |
| `tests/test_entity_light.py`                                               | light entity methods                                                                              |
| `tests/test_config_flow.py`                                                | config/options flow steps                                                                         |
| `tests/test_init.py`                                                       | integration `async_setup_entry`/`async_unload_entry`, offline manager, update listener            |
| `tests/test_setup_entries.py`                                              | per-platform `async_setup_entry` boilerplate                                                      |
| `tests/test_util.py`                                                       | `util.py` (e.g. `remap_value`)                                                                    |

### prek

Use `.venv/bin/prek run --all-files` for the full check set (trim, ruff, ruff-format, cspell, yamllint, prettier, mypy, pylint). Do **not** use `pre-commit` directly — this repo drives the same hook config through `prek` from the virtual environment. Run prek on new/changed test files too.

Pytest is also available as a manual `prek` hook, including branch coverage. Run the complete suite with:

```sh
.venv/bin/prek run --hook-stage manual pytest --all-files
```

The `mypy` and `pylint` hooks pull `additional_dependencies` from the requirements files via generated blocks (marked `# BEGIN GENERATED REQUIREMENTS` / `# END GENERATED REQUIREMENTS`) in `.pre-commit-config.yaml`. These are managed by `scripts/sync_prek_deps.py`; the `check-prek-deps` prek hook fails if they drift. After changing `requirements*.txt`, regenerate with:

```sh
.venv/bin/python scripts/sync_prek_deps.py requirements.txt requirements-dev.txt
```

`scripts/sync_prek_deps.py` preserves trailing newlines and updates every generated block, so running it is idempotent with prettier. The script lives under `scripts/` (outside `custom_components/`), so the coverage thresholds do **not** apply to it.

To modernize typing syntax (e.g. `Optional[X]` → `X | None`), run the manual hook:
`.venv/bin/prek run --hook-stage manual python-typing-update --all-files` — applies changes that need manual review before committing.

## SonarQube

Two SonarQube paths are in play — know which one a task refers to.

- **Local/IDE analysis (MCP)**: configured in `.opencode/opencode.json` via a local SonarQube MCP server. After creating or modifying files, run `sonarqube_analyze_file_list` on those files to catch issues before they reach CI. General MCP guidance lives in `.github/instructions/sonarqube_mcp.instructions.md`. Repo-specific, frequently-hit rules are in `.github/skills/code-review/SKILL.md` §9 (S8508 `dict.fromkeys`, S1172 unused param, S5778 exception-test, etc.).
- **CI SonarCloud scan**: `.github/workflows/sonarqube.yml` runs on PRs and pushes to `main`, gated on the Test workflow's `coverage.xml` artifact and a `SONAR_TOKEN` secret. Project key is `timlaing_ha_tuya_ble`.

Workflow notes (from the MCP instructions):

- Disable automatic analysis with `sonarqube_toggle_automatic_analysis` at the start of a task and re-enable it when done.
- Do **not** verify a fix via `search_sonar_issues_in_projects` — the server lags behind local analysis.
- Prefer fixing a SonarQube issue with a code change over suppressing it; only use `sonarqube_change_sonar_issue_status` (`accept`/`falsepositive`) when a fix isn't appropriate.

## Architecture

```
Tuya BLE Device <-> Home Assistant (ha_tuya_ble)
                        |
                Tuya Cloud (QR code login, key exchange only)
```

All public BLE symbols — `TuyaBLEDevice`, `TuyaBLEDataPoint`, `TuyaBLEDataPointType`, `TuyaBLEDataPoints`, `BLE_CONNECTION_EXCEPTIONS`, `BLEAK_EXCEPTIONS`, `SERVICE_UUID`, `AbstractTuyaBLEDeviceManager`, `TuyaBLEDeviceCredentials` — are re-exported from `tuya_ble/__init__.py`. Platform files and `cloud.py`/`devices.py` import them from the package (`from .tuya_ble import ...`), never from the internal defining module. After the split of `tuya_ble.py`, `TuyaBLEDataPoint` lives in `datapoints.py` and protocol logic in `protocol_mixin.py`, but you must still import them via the package.

`devices.py` is a **pure re-export shim** — all of its former contents now live in `products.py` (dataclasses, `devices_database`, helper functions), `entity.py` (`TuyaBLEEntity`, `get_device_info`), and `coordinator.py` (`TuyaBLECoordinator`). Don't add new code to `devices.py`; import these symbols from their defining modules.

## Key files

| File                                     | Purpose                                                                                                                      |
| ---------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `config_flow.py`                         | QR code login flow (user code → scan → device selection)                                                                     |
| `cloud.py`                               | `tuya_sharing.Manager` wrapper, MAC-based device credential lookup                                                           |
| `const.py`                               | Constants — imports shared names from `homeassistant.components.tuya.const`; re-exports `DPType` from `tuya_ble.const`       |
| `products.py`                            | Dataclasses (`TuyaBLECategoryInfo`, …), `devices_database` registry, product lookup helpers                                  |
| `entity.py`                              | `TuyaBLEEntity` base class, `get_device_info`                                                                                |
| `coordinator.py`                         | `TuyaBLECoordinator` (DataUpdateCoordinator subclass)                                                                        |
| `device_descriptors/handlers/fingerbot/` | Shared Fingerbot handlers (`in_program_mode`, `get/set_program`, repeat/maintenance helpers, etc.) in `mode.py`/`program.py` |
| `devices.py`                             | **Re-export shim** — imports from the split modules above; don't add code here                                               |
| `tuya_ble/`                              | Vendored BLE protocol library (encryption, pairing) — no cloud dependency                                                    |
| `tuya_ble/__init__.py`                   | **Re-exports** all public BLE symbols — import from here, not the internal modules                                           |
| `tuya_ble/manager.py`                    | `AbstractTuyaBLEDeviceManager` interface + `TuyaBLEDeviceCredentials` that `cloud.py` implements                             |
| `tuya_ble/tuya_ble.py`                   | `TuyaBLEDevice(TuyaBLEProtocol)` — BLE connection management, device state                                                   |
| `tuya_ble/protocol_mixin.py`             | `TuyaBLEProtocol` mixin — packet building/sending/parsing, AES encryption (`BLEAK_EXCEPTIONS`, `BLE_CONNECTION_EXCEPTIONS`)  |
| `tuya_ble/datapoints.py`                 | `TuyaBLEDataPoint`, `TuyaBLEDataPoints`                                                                                      |
| `tuya_ble/const.py`                      | `TuyaBLECode`, `TuyaBLEDataPointType`, `DPType`, UUIDs                                                                       |
| `strings.json`                           | UI strings for config flow and entity translations                                                                           |

## Entity platforms

Each platform file (binary_sensor, button, climate, number, select, sensor, switch, text, valve) follows the same pattern: a mapping dict keyed by Tuya category ID → product ID → list of data-point mappings. To add a new device, add entries to `devices_database` in `products.py` and add data-point mappings in the relevant platform files.

Some platforms carry bespoke per-device logic beyond the mapping dict — notably the shared Fingerbot handlers in `device_descriptors/handlers/fingerbot/`, consumed by `text.py`, `switch.py`, `number.py`, and `button.py` (e.g. `program.get_program`, `program.set_program`, `mode.in_program_mode`, `program.set_repeat_forever`), and custom getters in `sensor.py` (`battery_enum_getter`, `rssi_getter`) and `select.py` (`TemperatureUnitDescription`). Editing a device that needs special handling means touching these handlers, not just the mappings.

## Dependencies

- **`tuya-device-sharing-sdk`** — QR code login, `Manager`, `CustomerApi` (listed in manifest.json)
- **`pycryptodomex`** (dev env: **`pycryptodome`**) — both import as `Crypto.Cipher`; used for AES in `tuya_ble/tuya_ble.py` and `tuya_ble/protocol_mixin.py`. HA core provides pycryptodomex at runtime; requirements-dev.txt installs pycryptodome for local checks
- **`bleak` / `bleak_retry_connector`** — BLE communication — provided by HA core
- **`homeassistant.components.tuya.const`** — shared constants (`TUYA_CLIENT_ID`, `CONF_USER_CODE`, etc.)

Runtime deps are only `tuya-device-sharing-sdk` (manifest.json). Development tools (`ruff`, `mypy`, `pylint`, `pytest`, `prek`, `homeassistant`, `pycryptodome`) live in `requirements-dev.txt`.

Do not add `tuya-iot-py-sdk` back. The integration migrated away from it in `e5ca99f`.

## Gotchas

- `manifest.json` requires `bluetooth_adapters` as a dependency
- `TUYA_CLIENT_ID` and `TUYA_SCHEMA` are imported from HA core's Tuya integration — do not redefine locally
- `tuya_ble/` is a clean abstraction boundary — it knows nothing about cloud auth, only BLE encryption
- Diivoo timers using the **Homgar app** (not Tuya Smart Life) won't appear in Tuya cloud — no local key extractable
- `CONF_FUNCTIONS` / `CONF_STATUS_RANGE` on `TuyaBLEDeviceCredentials` carry device specs from cloud
- `tuya_ble/tuya_ble.py` and `tuya_ble/protocol_mixin.py` use `from Crypto.Cipher import AES` (pycryptodome/pycryptodomex) — not a listed runtime requirement but always available in HA
- Since HA 2026.8, entity `EntityDescription` subclasses are built through `homeassistant.util.frozen_dataclass_compat`, which does **not** honor a class-level default for the required `key` field. `TemperatureUnitDescription` (select.py) and `TuyaBLEDownPositionDescription`/`UpPositionDescription`/`HoldTimeDescription` (number.py) therefore must pass `key=` explicitly at every construction site or the module fails to import (`SelectEntityDescription.__init__() missing 1 required positional argument: 'key'`). Keep this pattern in mind when adding new description subclasses.
