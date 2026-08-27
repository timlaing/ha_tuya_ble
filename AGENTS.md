# AGENTS.md

## Project

Home Assistant custom integration (HACS) for Tuya BLE devices. Handles encryption and device protocol locally over Bluetooth — no cloud dependency after initial key exchange.

Forked from [PlusPlus-ua/ha_tuya_ble](https://github.com/PlusPlus-ua/ha_tuya_ble), maintained by [@timlaing](https://github.com/timlaing/ha_tuya_ble).

## Tests

There is no CI, but a pytest unit-test suite lives in `tests/` (532 tests, 99% branch coverage of `custom_components/tuya_ble`, incl. entity platforms and config flow). Run it with coverage:

```sh
.venv/bin/python -m pytest --cov=custom_components.tuya_ble --cov-branch --cov-report=term-missing
# or just the unit tests without coverage:
.venv/bin/python -m pytest -q
```

`pyproject.toml` configures `testpaths=["tests"]`, `asyncio_mode="auto"`, and `addopts=["--disable-socket", "--allow-unix-socket"]`.

### Test conventions

- Tests import via `custom_components.tuya_ble.*` (never bare `tuya_ble.*`); `custom_components/` is added to `sys.path` in `tests/conftest.py`. **Do not** add `custom_components/tuya_ble` itself to `sys.path` — its `select.py`/`text.py` etc. shadow stdlib modules.
- Tests exercise **public** entry points. Setting device/flow internals (`_client`, `_session_key`, `_manager`, name-mangled login state) as **test setup state** is acceptable.
- Real `TuyaBLEDevice` + `TuyaBLECoordinator` are built in tests; the device's `send_datapoints` is stubbed so no BLE I/O occurs. Data points are driven via the public `TuyaBLEDataPoints.update_from_device`.
- Entity classes register their coordinator listener in `async_added_to_hass()`, not `__init__` — call `await entity.async_added_to_hass()` before asserting on coordinator-triggered updates, or call the handler directly.
- `devices.py`/`devices_database` is a pure registry: `get_product_info_by_ids`, `get_device_product_info`, `get_short_address`, `get_device_info` are unit-tested directly.
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
| `tests/test_device.py`                                                     | `TuyaBLEDevice` connection/state (mocked connect flow)                                            |
| `tests/test_connection.py`                                                 | BLE connection lifecycle, error paths, protocol edge cases                                        |
| `tests/test_cloud.py`                                                      | `cloud.py`                                                                                        |
| `tests/test_devices.py`                                                    | pure devices.py functions                                                                         |
| `tests/test_mappings.py`                                                   | per-platform `get_mapping_by_device` + pure Fingerbot/sensor helpers                              |
| `tests/test_entity_binary_sensor.py`                                       | binary_sensor entity methods                                                                      |
| `tests/test_entity_button.py`                                              | button entity methods                                                                             |
| `tests/test_entity_climate.py`                                             | climate entity methods                                                                            |
| `tests/test_entity_number.py`                                              | number entity methods + fingerbot helper functions                                                |
| `tests/test_entity_select.py`                                              | select entity methods                                                                             |
| `tests/test_entity_sensor.py`                                              | sensor entity methods                                                                             |
| `tests/test_entity_switch.py`                                              | switch entity methods                                                                             |
| `tests/test_entity_text.py`                                                | text entity methods                                                                               |
| `tests/test_entity_valve.py`                                               | valve entity methods                                                                              |
| `tests/test_entity_lock.py`                                                | lock entity methods                                                                               |
| `tests/test_entity_cover.py`                                               | cover entity methods                                                                              |
| `tests/test_entity_light.py`                                               | light entity methods                                                                              |
| `tests/test_config_flow.py`                                                | config/options flow steps                                                                         |

### prek

Use `.venv/bin/prek run --all-files` for the full check set (trim, ruff, ruff-format, cspell, yamllint, prettier, mypy, pylint). Do **not** use `pre-commit` directly — this repo drives the same hook config through `prek` from the virtual environment. Run prek on new/changed test files too.

To modernize typing syntax (e.g. `Optional[X]` → `X | None`), run the manual hook:
`.venv/bin/prek run --hook-stage manual python-typing-update --all-files` — applies changes that need manual review before committing.

## Architecture

```
Tuya BLE Device <-> Home Assistant (ha_tuya_ble)
                        |
                Tuya Cloud (QR code login, key exchange only)
```

All public BLE symbols — `TuyaBLEDevice`, `TuyaBLEDataPoint`, `TuyaBLEDataPointType`, `TuyaBLEDataPoints`, `BLE_CONNECTION_EXCEPTIONS`, `BLEAK_EXCEPTIONS`, `SERVICE_UUID`, `AbstractTuyaBLEDeviceManager`, `TuyaBLEDeviceCredentials` — are re-exported from `tuya_ble/__init__.py`. Platform files and `cloud.py`/`devices.py` import them from the package (`from .tuya_ble import ...`), never from the internal defining module. After the split of `tuya_ble.py`, `TuyaBLEDataPoint` lives in `datapoints.py` and protocol logic in `protocol_mixin.py`, but you must still import them via the package.

## Key files

| File                         | Purpose                                                                                                                     |
| ---------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| `config_flow.py`             | QR code login flow (user code → scan → device selection)                                                                    |
| `cloud.py`                   | `tuya_sharing.Manager` wrapper, MAC-based device credential lookup                                                          |
| `const.py`                   | Constants — imports shared names from `homeassistant.components.tuya.const`                                                 |
| `devices.py`                 | Per-device coordinator, entity base class, and **product registry** (`devices_database`)                                    |
| `tuya_ble/`                  | Vendored BLE protocol library (encryption, pairing) — no cloud dependency                                                   |
| `tuya_ble/__init__.py`       | **Re-exports** all public BLE symbols — import from here, not the internal modules                                          |
| `tuya_ble/manager.py`        | `AbstractTuyaBLEDeviceManager` interface + `TuyaBLEDeviceCredentials` that `cloud.py` implements                            |
| `tuya_ble/tuya_ble.py`       | `TuyaBLEDevice(TuyaBLEProtocol)` — BLE connection management, device state                                                  |
| `tuya_ble/protocol_mixin.py` | `TuyaBLEProtocol` mixin — packet building/sending/parsing, AES encryption (`BLEAK_EXCEPTIONS`, `BLE_CONNECTION_EXCEPTIONS`) |
| `tuya_ble/datapoints.py`     | `TuyaBLEDataPoint`, `TuyaBLEDataPoints`                                                                                     |
| `tuya_ble/const.py`          | `TuyaBLECode`, `TuyaBLEDataPointType`, UUIDs                                                                                |
| `strings.json`               | UI strings for config flow and entity translations                                                                          |

## Entity platforms

Each platform file (binary_sensor, button, climate, number, select, sensor, switch, text, valve) follows the same pattern: a mapping dict keyed by Tuya category ID → product ID → list of data-point mappings. To add a new device, add entries to `devices_database` in `devices.py` and add data-point mappings in the relevant platform files.

Some platforms carry bespoke per-device logic beyond the mapping dict — notably Fingerbot helpers in `text.py`, `switch.py`, `number.py` (e.g. `get_fingerbot_program`, `set_fingerbot_program`, `is_fingerbot_in_program_mode`, `set_fingerbot_program_repeat_forever`) and custom getters in `sensor.py` (`battery_enum_getter`, `rssi_getter`) and `select.py` (`TemperatureUnitDescription`). Editing a device that needs special handling means touching these helpers, not just the mappings.

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
