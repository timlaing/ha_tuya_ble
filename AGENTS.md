# AGENTS.md

<!-- cspell:ignore ecode -->

## Project

Home Assistant custom integration (HACS) for Tuya BLE devices. Encryption and the device protocol run locally over Bluetooth; the Tuya cloud is used only for initial key exchange and one-time app-key recovery. Forked from PlusPlus-ua/ha_tuya_ble, maintained by @timlaing.

OpenCode-specific operating rules (plans, commits, pushes, PRs, rebases, `.opencode` backups) live in `.opencode/private-instructions.md`, referenced from `.opencode/opencode.json`. Sonar/Qube repo rules are in `.github/skills/code-review/SKILL.md` §9.

## Development commands

Set up with `python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt` (also `scripts/setup.sh` in devcontainers).

```sh
.venv/bin/python -m pytest -q                          # unit tests (3170, ~2s timeout each)
.venv/bin/python -m pytest --cov=custom_components.tuya_ble --cov-branch --cov-report=term-missing
.venv/bin/prek run --all-files                          # full check set; never use pre-commit directly
.venv/bin/prek run --hook-stage manual pytest --all-files
.venv/bin/python scripts/sync_prek_deps.py requirements.txt requirements-dev.txt
```

`pyproject.toml`: `testpaths=["tests"]`, `asyncio_mode="auto"`, `addopts` include `--disable-socket --allow-unix-socket --timeout=2`. CI (`.github/workflows/test.yml`) gates on `--cov-fail-under=90` overall; the per-file target is >90% line / ~100% branch coverage of `custom_components/tuya_ble` — add tests until each modified file meets it.

`mypy`/`pylint` hook dependencies come from generated `# BEGIN GENERATED REQUIREMENTS` blocks in `.pre-commit-config.yaml`, maintained by `scripts/sync_prek_deps.py` (idempotent, prettier-compatible); `check-prek-deps` fails on drift. `scripts/` is outside `custom_components/`, so coverage thresholds do not apply to it. Manual hook `python-typing-update` modernizes typing syntax and needs review before committing.

## Test conventions

- Import via `custom_components.tuya_ble.*`, never bare `tuya_ble.*`; `custom_components/` is on `sys.path` from `tests/conftest.py`. Do **not** add `custom_components/tuya_ble` itself to `sys.path` — `select.py`/`text.py` shadow stdlib modules.
- Exercise public entry points. Setting internals (`_client`, `_session_key`, `_manager`, protected `_qr_*`) as **setup state** is acceptable.
- Tests build a real `TuyaBLEDevice` + `TuyaBLECoordinator` with `send_datapoints` stubbed (no BLE I/O); drive reports through `TuyaBLEDataPoints.update_from_device`.
- Entities register their coordinator listener in `async_added_to_hass()`, not `__init__` — `await entity.async_added_to_hass()` before asserting on coordinator updates.
- The `hass` fixture is from `pytest-homeassistant`; config-flow tests call `async_step_*` directly with `config_flow.HASSTuyaBLEDeviceManager` and the single-underscore `_qr_*` attributes patched with fakes (no name mangling).
- Fakes/helpers (`make_device`, `make_credentials`, `add_dp`) are in `tests/conftest.py`; packet-building helpers in `tests/protocol_harness.py`. Tests mirror module names (`test_<module>.py`).
- Tests mocking `asyncio.create_task`/`sleep` need module-level `pytestmark = pytest.mark.filterwarnings("ignore::RuntimeWarning")` and a `_close_task()` helper calling `coro.close()` to avoid unawaited-coroutine warnings leaking via `gc.collect()`.
- Most modules log at `debug`; `caplog` does not capture DEBUG unless raised — use `caplog.at_level(logging.DEBUG)` for each debug assertion and `caplog.clear()` between phases of one test.
- Integration logging: module-level `_LOGGER`, lazy `%`-style formatting (no f-strings), `debug` happy path, `warning` recoverable, `error` only with a user-facing failure form. Never log `local_key`, `access_token`, `refresh_token`, `terminal_id`, `user_code`, QR tokens, `endpoint`, or cloud `ble_unlock_check`/`sec_key`.

## Quality gates

- CI: `test.yml` (pytest + coverage artifact), `lint.yml` (prek), `hass.yml`/`hacs.yml` (metadata), `codeql.yml`, `sonarqube.yml`, `checks.yml` (required statuses), `action-refs.yml` (full-SHA action pins, `scripts/check_action_refs.py`), `release-drafter.yml`/`autolabeler.yml`, `stale.yml`, `issue-autolabeler.yml`.
- SonarCloud: project key `timlaing_ha_tuya_ble`; scan gated on the coverage artifact and `SONAR_TOKEN`. Recurring rules: S3516/S3776 (split long functions), S8508 (`dict.fromkeys` with mutable values), S1172 (unused params), S5778 (exception test). Prefer code fixes over suppression. Local Sonar IDE MCP may be unavailable; `.github/instructions/sonarqube_mcp.instructions.md` covers its setup.
- Before pushing, run prek and local Sonar analysis when the MCP tools are available.

## Architecture

```
Tuya BLE device <-> Home Assistant integration
                        |
                Tuya cloud (QR login, key exchange, one-time app login only)
```

- Public BLE symbols (`TuyaBLEDevice`, `TuyaBLEDataPoint`, `TuyaBLEDataPointType`, `TuyaBLEDataPoints`, `BLE_CONNECTION_EXCEPTIONS`, `BLEAK_EXCEPTIONS`, `SERVICE_UUID`, `AbstractTuyaBLEDeviceManager`, `TuyaBLEDeviceCredentials`, `is_valid_ble_unlock_check`, `TuyaBLEReport`) are re-exported from `tuya_ble/__init__.py`. Import from the package (`from .tuya_ble import ...`), never internal modules; `TuyaBLEDataPoint` lives in `datapoints.py` and protocol logic in split `protocol_*` modules.
- `devices.py` is a pure re-export shim — do not add code there. Owners: `device_registry.py` (`EntityDescriptor`, descriptor loading/validation, `get_registry`, `get_mapped_dp_ids`), `entity.py` (`TuyaBLEEntity`, `get_device_info`, `get_product_info_by_ids`, `get_device_product_info`, `get_short_address`), `coordinator.py` (`TuyaBLECoordinator`).
- `device_descriptors/` is the source of truth for product metadata: one `<category>_<product_id>.yaml` per product, `_category_*.yaml` defaults and `handlers/`. Adding a device normally means one descriptor file, no platform code.
- 15 platform files (binary_sensor, button, climate, cover, event, lawn_mower, light, lock, number, select, sensor, switch, text, vacuum, valve): each `_build_mapping()` walks `get_registry().products.values()` and returns category → product → mappings. Per-device logic lives in descriptor `handlers:` (sensor `battery`/`rssi`/`measurement`, valve `water_valve`, mower `parkside_mower`, shared `handlers/fingerbot/`, `handlers/lock/`), not in platform files.

## Key files

| File                                                                                         | Purpose                                                                                                 |
| -------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `config_flow.py`, `config_flow_options.py`, `config_flow_manual.py`, `config_flow_mobile.py` | QR login, options, manual credentials, conditional app login                                            |
| `mobile_cloud.py`, `mobile_snapshot.py`                                                      | Read-only mobile API and bounded sanitization of its metadata                                           |
| `cloud.py`, `const.py`                                                                       | `tuya_sharing.Manager` wrapper / credential lookup; constants (shared Tuya names imported from HA core) |
| `device_registry.py`, `device_descriptors/`                                                  | Product registry and YAML descriptors                                                                   |
| `entity.py`, `devices.py`, `coordinator.py`                                                  | Base entity/helpers, re-export shim, update/connection coordinator                                      |
| `light.py`, `lock.py`, `lawn_mower.py`, `event.py`                                           | Platforms with bespoke descriptor/handler behaviour                                                     |
| `diagnostics.py`                                                                             | Config-entry/device diagnostics and redaction                                                           |
| `strings.json`                                                                               | Config-flow and entity translations                                                                     |

## Dependencies

Runtime: `tuya-device-sharing-sdk` and pinned `tuya-mobile` (manifest.json). `pycryptodomex` at runtime (HA core) / `pycryptodome` locally (`requirements-dev.txt`), both imported as `Crypto.Cipher`; `bleak`/`bleak_retry_connector` from HA core. Dev tools in `requirements-dev.txt`. Do not reintroduce `tuya-iot-py-sdk`.

## Gotchas

- `manifest.json` requires `bluetooth_adapters`; `TUYA_CLIENT_ID`/`TUYA_SCHEMA` come from HA core's Tuya integration — do not redefine.
- `tuya_ble/` knows nothing about cloud auth, only BLE encryption.
- `ble_unlock_check` and `sec_key` on `TuyaBLEDeviceCredentials` are secrets: validated/persisted under `CONF_BLE_UNLOCK_CHECK`/`CONF_SEC_KEY`, excluded from `repr`, masked in `__str__`, withheld in diagnostics and never logged. `sensitive_dp_ids` in descriptors drives `device.datapoints.sensitive_ids` (log redaction).
- Mobile login is only offered for eligible missing/unusable-key failures; never persist or log the password, username, SID, ecode or tokens; do not reuse the probe's session cache. Persist sanitized metadata under `CONF_MOBILE_SNAPSHOT`.
- `local_schema` (`CONF_LOCAL_SCHEMA`) carries captured DP metadata only, never credentials.
- Connection policy lives in entry options (`CONF_KEEP_CONNECTION`, `CONF_IDLE_DISCONNECT_DELAY`, `CONF_STATUS_POLL_INTERVAL`); missing options keep the link open. On-demand mode disconnects after an idle delay and does not self-reconnect.
- Diivoo timers using the Homgar app do not appear in Tuya cloud — no local key extractable.
- `select.py` subclasses `SelectEntityDescription` as `TemperatureUnitDescription` to supply icon/entity-category defaults, not `key` (descriptions already include `key`).
- `tuya_ble/tuya_ble.py` and `tuya_ble/protocol_mixin.py` use `from Crypto.Cipher import AES` — not a stated runtime requirement but always available in HA.
