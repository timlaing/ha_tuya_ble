# Contributing to Tuya BLE

## Adding a new device

New device support requires a **YAML device descriptor**. All device configuration lives in `custom_components/tuya_ble/device_descriptors/` — you do **not** need to edit the Python platform files or `products.py`.

### 1. Create a YAML device descriptor

Create a file named `<category>_<product_id>.yaml` under `custom_components/tuya_ble/device_descriptors/`. For example, for a device with category `sfkzq` and product ID `16wgjvck`, create `sfkzq_16wgjvck.yaml`:

```yaml
category: sfkzq
product_id: 16wgjvck
entities:
  sensor:
    - dp_id: 7
      translation_key: battery
      device_class: battery
      unit: "%"
      state_class: measurement
      entity_category: diagnostic
      kind: battery
  switch:
    - dp_id: 1
      translation_key: water_valve
      icon: mdi:valve
device_name: Aldi/Ferrex Smart Water Valve
model_name: 16wgjvck
```

### 2. Descriptor schema

#### Top-level fields

| Field         | Required | Description                                                                |
| ------------- | -------- | -------------------------------------------------------------------------- |
| `category`    | Yes      | Tuya category ID (e.g. `sfkzq`, `ggq`, `ms`)                               |
| `product_id`  | Yes      | Tuya product ID                                                            |
| `entities`    | No       | Mapping of platform name → list of entity descriptors                      |
| `device_name` | No       | Human-readable device name (informational only — not read by the registry) |
| `model_name`  | No       | Model identifier (informational only — not read by the registry)           |

#### Entity fields (common)

| Field                | Required | Description                                                                                     |
| -------------------- | -------- | ----------------------------------------------------------------------------------------------- |
| `dp_id`              | Yes*     | Data-point ID on the device (*not required for `climate`, `cover`, `light`)                     |
| `translation_key`    | No       | Translation key / entity ID (also accepts `key`)                                                |
| `name`               | No       | Display name override                                                                           |
| `icon`               | No       | MDI icon override (e.g. `mdi:valve`)                                                            |
| `device_class`       | No       | HA device class (e.g. `battery`, `temperature`, `carbon_dioxide`)                               |
| `unit`               | No       | Unit of measurement (e.g. `%`, `°C`, `ppm`, `s`)                                                |
| `state_class`        | No       | HA state class (`measurement`, `total_increasing`, `total`)                                     |
| `dp_type`            | No       | Data-point type override (used by all platforms except `climate`, `cover`, `light`)             |
| `entity_category`    | No       | `config` or `diagnostic`                                                                        |
| `enabled_by_default` | No       | Set to `false` to hide the entity by default                                                    |
| `kind`               | No       | Selects a built-in mapping class for the platform (e.g. `battery` or `temperature` in `sensor`) |
| `handlers`           | No       | Mapping of role → handler path (see [Handlers](#handlers))                                      |

> **Translations**: `translation_key` values are looked up against `entity.<platform>.<translation_key>` in `strings.json` / `translations/en.json`. When you introduce a new `translation_key`, add the corresponding `name` entry there too — otherwise use a literal `name:` instead.

#### Entity fields (by platform)

**number**: `min_value`, `max_value`, `step`, `mode` (`box` or `slider`)

**select**: `options` (display values), `values` (raw DP values)

**text**: `pattern` (regex validation pattern)

**switch**: `bitmap_mask` (binary mask for splitting one bitmap DP into multiple switches — must be YAML `!!binary` bytes, e.g. `AQ==` for `0b00000001`)

**climate**: `hvac_mode_dp_id`, `hvac_switch_dp_id`, `hvac_switch_mode`, `hvac_modes`, `current_temperature_dp_id`, `current_temperature_coefficient`, `target_temperature_dp_id`, `target_temperature_coefficient`, `target_temperature_step`, `target_temperature_min`, `target_temperature_max`, `temperature_unit`, `current_humidity_dp_id`, `current_humidity_coefficient`, `target_humidity_dp_id`, `target_humidity_coefficient`, `target_humidity_min`, `target_humidity_max`, `preset_mode_dp_ids`

**cover**: `state_dp_id`, `position_set_dp_id`, `position_dp_id`, `tilt_dp_id`

**light**: `switch_dp_id`, `color_mode_dp_id`, `brightness_dp_id`, `brightness_min`, `brightness_max`, `color_temp_dp_id`, `color_temp_min`, `color_temp_max`, `color_data_dp_id`

**lock**: `door_dp_id` (DP ID for door status sensor)

### 3. Handlers

Handlers are callables referenced by dotted path under `device_descriptors/handlers/`. They customise read, write, or availability (`when`) behaviour for an entity:

```yaml
entities:
  switch:
    - dp_id: 1
      translation_key: water_valve
      handlers:
        write: water_valve.set_16wgjvck_water_valve
```

Three roles are supported. Note that handler signatures vary by platform:

| Role    | Sensor / binary_sensor             | Other platforms                   | Purpose                            |
| ------- | ---------------------------------- | --------------------------------- | ---------------------------------- |
| `read`  | `(entity)` — sets the entity value | `(entity, product)` → value       | Custom DP value reader             |
| `write` | `(entity, product, value)` → None  | `(entity, product, value)` → None | Custom DP value writer             |
| `when`  | `(entity, product)` → bool         | `(entity, product)` → bool        | Whether the entity should be added |

`read` handlers on `sensor` and `binary_sensor` platforms are called with only the entity and must set its value directly (returning `None`); on `switch`, `number`, `text`, `valve`, and `lock` platforms they receive `(entity, product)` and return the value. `write` and `when` handlers use the same `(entity, product)` / `(entity, product, value)` signatures on every platform.

Available handler modules:

- `battery.battery_enum` — convert battery enum DP to percentage
- `co2.alarm_enabled` — check if CO2 alarm is enabled
- `rssi.rssi` — read RSSI signal strength
- `water_valve.is_water_valve_in_switch_mode` — when-role for water valve switches
- `water_valve.set_16wgjvck_water_valve` — Aldi/Ferrex water valve write handler
- `fingerbot.mode.*` — Fingerbot mode/availability helpers
- `fingerbot.program.*` — Fingerbot program read/write helpers

Invalid handler paths or unknown roles are **rejected at load time** — typos fail fast.

### 4. Category defaults

Create a `_category_<category>.yaml` file (e.g. `_category_cl.yaml`) to define entities shared by all products in a category. A product inherits a platform's category defaults, but **only if it defines no entities for that platform** — if a product lists any entities for a platform, those replace the category defaults for that platform entirely (there is no per-entity merge):

```yaml
# _category_cl.yaml — shared by all blind/curtain controllers
entities:
  cover:
    - translation_key: ble_cover
      state_dp_id: 1
      position_set_dp_id: 2
      position_dp_id: 3
```

### 5. Update `SUPPORTED_DEVICES.md`

Add the device to the supported devices list in `SUPPORTED_DEVICES.md` (and, if it's a new category, add a row to the category summary table in `README.md`).

## Getting the data-point list

The data-point list tells you which dp_ids the device uses and their types. You can get this from:

1. **Smart Life / Tuya Smart app** — device info page (some apps show dp codes)
2. **Tuya IoT platform** — if you have developer access, Device Debug > Data Points
3. **Tuya BLE debug logs** — enable debug logging for `custom_components.tuya_ble` and look for "Received datapoint update" messages

## Development setup

1. Clone the repo
2. Create and activate a virtual environment: `python3 -m venv .venv && . .venv/bin/activate`
3. Install dev dependencies: `pip install -r requirements-dev.txt`
4. Place `custom_components/tuya_ble` inside your Home Assistant `config/` directory
5. Restart Home Assistant
6. Enable debug logging:
   ```yaml
   logger:
     logs:
       custom_components.tuya_ble: debug
   ```

## Running checks

Before submitting a PR, run the full check suite:

```sh
# Lint/format/mypy/pylint (all hooks)
.venv/bin/prek run --all-files

# Unit tests
.venv/bin/python -m pytest -q

# Tests with coverage report
.venv/bin/python -m pytest --cov=custom_components.tuya_ble --cov-branch --cov-report=term-missing
```

All prek hooks and tests must pass before submitting.

### Keeping prek dependencies in sync

The `mypy` and `pylint` hooks in `.pre-commit-config.yaml` use `additional_dependencies` generated from the requirements files. These blocks are marked with `# BEGIN GENERATED REQUIREMENTS` / `# END GENERATED REQUIREMENTS` and are managed by `scripts/sync_prek_deps.py`:

```sh
# Regenerate the dependency blocks
.venv/bin/python scripts/sync_prek_deps.py requirements.txt requirements-dev.txt

# Check without modifying (run automatically by the `check-prek-deps` prek hook)
.venv/bin/python scripts/sync_prek_deps.py --check requirements.txt requirements-dev.txt
```

After changing `requirements*.txt`, re-run the sync so the generated blocks stay up to date — the `check-prek-deps` hook fails otherwise.

## PR process

1. Fork the repo
2. Create a branch (`git checkout -b feature/my-device`)
3. Make your changes
4. Run `prek run --all-files` and `pytest` — both must pass
5. Open a PR — include the device name, product_id, and category in the PR description
