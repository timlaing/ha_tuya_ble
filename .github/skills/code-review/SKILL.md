---
name: code-review
description: "Use when reviewing code changes, PRs, or commits in the ha_tuya_ble integration. Covers architecture, typing, HA conventions, linting, test coverage, and SonarQube quality rules. Trigger keywords: review, pr, code review, linting, typing, coverage, sonarqube, prek."
---

# Code Review Skill — ha_tuya_ble

Systematic review checklist for the `ha_tuya_ble` Home Assistant custom integration.

## Review Standard

- Establish the review target before inspecting code: working-tree changes, staged changes, a commit or commit range, or a PR against its merge base. State the selected target and base in the result.
- Review only changes in scope. Do not report pre-existing problems unless the change makes them materially worse; mention important out-of-scope risks separately without presenting them as findings.
- Report a finding only when the changed code introduces a demonstrable functional, security, reliability, compatibility, or maintainability defect. Explain the triggering conditions and impact, and cite the smallest useful `file:line` range.
- Do not report speculative problems, personal style preferences, or issues already caught reliably by required automated tooling. Do not invent findings when none qualify.
- Classify findings as:
  - **P0**: catastrophic or release-blocking in nearly all uses.
  - **P1**: likely functional, security, or data-integrity defect.
  - **P2**: real defect under plausible conditions.
  - **P3**: worthwhile, non-blocking correctness or maintainability improvement.
- Keep suggestions separate from defects. A suggestion must not be presented as a blocker.

## 1. Architecture & Boundaries

- `tuya_ble/` is a pure BLE protocol library. It must never import from `custom_components/`, `homeassistant.*`, or cloud modules.
- All public BLE symbols (`TuyaBLEDevice`, `TuyaBLEDataPoint`, `TuyaBLEDataPointType`, `TuyaBLEDataPoints`, `BLEAK_EXCEPTIONS`, `BLE_CONNECTION_EXCEPTIONS`, `SERVICE_UUID`, `AbstractTuyaBLEDeviceManager`, `TuyaBLEDeviceCredentials`) must be re-exported from `tuya_ble/__init__.py`. Platform files import from the package, never from internal modules like `tuya_ble.datapoints` or `tuya_ble.protocol_mixin`.
- `TUYA_CLIENT_ID` and `TUYA_SCHEMA` are imported from `homeassistant.components.tuya.const` — never redefine locally.
- `devices.py`/`devices_database` is a pure registry. `coordinator.py` is the coordinator. `entity.py` is the entity base. `fingerbot.py` holds Fingerbot-specific helpers. Do not mix concerns.
- Runtime dependency: only `tuya-device-sharing-sdk`. Never add `tuya-iot-py-sdk` back.

## 2. Imports & Circular Dependencies

- Tests import via `custom_components.tuya_ble.*` (never bare `tuya_ble.*`). The `custom_components/` path is added to `sys.path` in `tests/conftest.py`.
- **Never** add `custom_components/tuya_ble` to `sys.path` — its `select.py`/`text.py` shadow stdlib modules.
- Entity platform files must import `DOMAIN` and `DEVICE_DEF_MANUFACTURER` from the top-level `const`/`entity` — not from internal modules that create circular chains.
- `from Crypto.Cipher import AES` (pycryptodome/pycryptodomex) is used in `tuya_ble/tuya_ble.py` and `tuya_ble/protocol_mixin.py`. Both packages alias to `Crypto.Cipher`; no conflict.

## 3. Type Safety & Typing

- `Optional[X]` → `X | None`. Use modern union syntax throughout.
- HA 2026.8+ `EntityDescription` subclasses use `frozen_dataclass_compat`, which does not honor class-level defaults for required `key` fields. Subclasses like `TemperatureUnitDescription`, `TuyaBLEDownPositionDescription`, `UpPositionDescription`, `HoldTimeDescription` must pass `key=` explicitly at every construction site.
- `TuyaBLEDataPoints` has no `__contains__` — `x in datapoints` loops forever. Use `datapoints[key] is None` instead.
- `bool` is a subclass of `int` in Python. `isinstance(True, int)` is `True`. If you need to distinguish bool from int, check `type(x) is bool` first.
- `TuyaBLEWaterValveInfo.weather_delay` and `.smart_weather` are typed as `int`, not `str`.
- `ColorMode` must be imported from `homeassistant.components.light.const` (not `homeassistant.components.light`).
- In production source, `# type: ignore` suppressions are acceptable only for third-party SDKs missing stubs, unavoidable HA framework typing gaps, MRO-related attribute errors on `_QRCodeLoginMixin`, and HA entity `self.hass` assignment.
- Test setup may use narrowly coded suppressions when fakes intentionally do not satisfy runtime types. Require the specific error code and keep the suppression on the affected expression.
- Never add a bare or unexplained `# type: ignore`. Prefer fixing the type when practical.

## 4. Dead Code & Redundancy

- `get_or_create()` always returns non-None. Any `if datapoint:` guard after it is dead code — remove the guard or handle the `None` case explicitly at the call site.
- `dict.fromkeys(keys, [mutable])` shares the same list object across all keys. Use `{k: [list] for k in keys}` (dict comprehension) to avoid SonarQube S8508.
- Unreachable branches (`if x is None` after a function that guarantees `x is not None`) should be removed.
- Duplicate function definitions (same logic in two files) should be consolidated into a shared helper in `fingerbot.py` or similar.

## 5. HA Entity Conventions

- Entity classes register their coordinator listener in `async_added_to_hass()`, not `__init__`. Call `await entity.async_added_to_hass()` before asserting on coordinator-triggered updates in tests.
- `config_flow.py` uses `_QRCodeLoginMixin` with plain protected attributes (not name-mangled). HA config flow steps (`async_step_*`) always receive `user_input: dict[str, Any] | None` even when unused — this is required by the interface.
- `manifest.json` must include `bluetooth_adapters` as a dependency and an `issue_tracker` URL.
- Entity description subclasses must be frozen dataclasses. Do not add mutable fields.
- Fingerbot helpers (`get_fingerbot_program`, `set_fingerbot_program`, `is_fingerbot_in_program_mode`, `set_fingerbot_program_repeat_forever`) live in `fingerbot.py`. Platform files import from there.

## 6. Test Conventions

- Tests exercise **public** entry points. Setting device/flow internals (`_client`, `_session_key`, `_manager`, protected `_qr_*` login state) as **test setup state** is acceptable.
- Real `TuyaBLEDevice` + `TuyaBLECoordinator` are built in tests; `send_datapoints` is stubbed so no BLE I/O occurs. Data points are driven via `TuyaBLEDataPoints.update_from_device`.
- Config-flow tests build flow objects directly, drive `async_step_*`, and patch `config_flow.HASSTuyaBLEDeviceManager` and protected `_qr_*`/manager setup attributes with fakes. Use `return_value=fake` instead of `lambda hass, data: fake`.
- Tests that mock `asyncio.create_task` or `asyncio.sleep` must use `pytestmark = pytest.mark.filterwarnings("ignore::RuntimeWarning")` and a `_close_task()` helper calling `coro.close()`.
- Tests must not use `assert True` as a placeholder. If the test verifies no exception, add a comment explaining the intent or make a meaningful assertion.
- Do not use class-based tests. Write module-level pytest test functions and share setup through fixtures or narrowly scoped helper functions.
- Fingerbot helpers are imported from `fingerbot.py` directly, not through platform modules.

## 7. Coverage Requirements

Every Python file under `custom_components/tuya_ble/` must meet:

- **Line coverage > 90%**
- Exercise every reachable branch (`if`/`elif`/`else`/`match` arms). Any uncovered branch requires a test or a documented reason it is unreachable.

Run after writing tests:

```sh
.venv/bin/python -m pytest --cov=custom_components.tuya_ble --cov-branch --cov-report=term-missing
```

This command reports coverage but does not enforce per-file thresholds. Inspect the per-file table before claiming compliance. Aggregate `--cov-fail-under=90` results do not prove that every file exceeds 90%.

## 8. Linting & Formatting

- Use `.venv/bin/prek run --all-files` for the full default-stage check set. Do not use `pre-commit` directly.
- All applicable hooks configured in `.pre-commit-config.yaml` must pass. Manual and `commit-msg` hooks are not part of the default all-files run.
- Some hooks apply fixes. A review-only request does not authorize worktree edits: record `git status --short` first and use non-mutating tool modes where practical. If a required check modifies files, disclose exactly what changed and do not silently include those edits in the review.
- cspell words in `.vscode/cspell.json` must be **lowercase and sorted alphabetically**.
- `# noqa` suppressions in source: only acceptable for S7503 (HA platform setup), S4790 (MD5 protocol), S5542 (AES-CBC protocol), S2583 (async flag false positive).
- `# pylint: disable` in source: only acceptable for `too-many-lines` (sensor.py), `abstract-method` (number.py), `unexpected-keyword-arg` (valve.py).
- Do not suppress SonarQube issues that can be fixed with code changes.

## 9. SonarQube Rules

| Rule  | Meaning                            | Fix                                                     |
| ----- | ---------------------------------- | ------------------------------------------------------- |
| S8508 | Mutable default in `dict.fromkeys` | Use dict comprehension                                  |
| S1172 | Unused function parameter          | Remove or accept if HA interface requires it            |
| S5914 | Constant boolean expression        | Remove or replace with meaningful assertion             |
| S9081 | Lambda should use `return_value`   | Use `patch(..., return_value=x)` instead of `lambda: x` |
| S7502 | Untracked asyncio task             | Save `create_task()` return to prevent GC               |
| S3776 | Cognitive complexity               | Refactor into smaller functions                         |

## 10. CI & Workflows

- `lint.yml`: installs its dependencies and runs `.venv/bin/prek run --all-files`.
- `test.yml`: runs tests with coverage and uploads `coverage.xml` as the `coverage-report` artifact.
- `sonarqube.yml`: downloads `coverage-report` and runs SonarQube Cloud analysis.
- All workflows must create `.venv` **before** upgrading pip inside it:
  ```yaml
  python -m venv .venv
  .venv/bin/pip install --upgrade pip
  .venv/bin/pip install -r requirements-dev.txt
  ```
- Do not upgrade system pip before creating the venv — the upgrade is ineffective.

## 11. Commit & PR Conventions

- Conventional commits: `fix:`, `chore:`, `feat:`, `refactor:`, `test:`, `docs:`.
- PRs require approval before merge.
- A review-only request does not authorize edits, commits, pushes, or merges. A fix-review request may include those actions only when the user explicitly requests them.
- Commit messages should be concise and describe the change, not the process.
- Run `prek run --all-files` and all tests before pushing.

## 12. Common Pitfalls

- `DT_ENUM` accepts both integer indices and string enum values — required by light platform.
- `DPType` (cloud spec StrEnum) is distinct from `TuyaBLEDataPointType` (BLE wire format enum).
- `protected-access` on `TuyaBLEDataPoint` is fixed by using `set_value_no_notify()` — never access `_value` directly outside the class.
- `set_multiple_values()` in `protocol_mixin.py` must use `dp.set_value_no_notify(value)`, not `dp._value = value`.
- Diivoo timers using the **Homgar app** (not Tuya Smart Life) won't appear in Tuya cloud — no local key extractable.
- `CONF_FUNCTIONS` / `CONF_STATUS_RANGE` on `TuyaBLEDeviceCredentials` carry device specs from cloud.

## 13. Review Workflow

1. Resolve and state the review target and base. Read that diff and identify all changed files and their categories (source, tests, config, CI).
2. Check each source file against architecture rules (Section 1) and import rules (Section 2).
3. Check typing against Section 3 rules. Verify no new `# type: ignore` without justification.
4. Check for dead code patterns (Section 4).
5. Verify HA entity conventions (Section 5) for any entity platform changes.
6. Check test conventions (Section 6) and coverage (Section 7) for test changes.
7. Record the initial worktree status. Run non-mutating checks where practical; if running `prek run --all-files`, detect and disclose any files it changes.
8. Run the coverage command and inspect every file's line and branch results. Do not infer per-file compliance from the aggregate percentage.
9. Check for SonarQube-tractable issues (Section 9).
10. Report findings in priority order with tight `file:line` references, triggering conditions, impact, and a concrete fix. Separate suggestions from defects. If there are no actionable findings, say so explicitly.
11. Add a verification summary listing each command run and its pass, fail, or not-run status. Include relevant failures and environmental limitations; never imply a check ran when it did not.
