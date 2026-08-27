# Contributing to Tuya BLE

## Adding a new device

New device support requires edits in multiple files. All products are keyed by **category** and **product_id** from Tuya.

### 1. Register the product in `devices.py`

Add an entry to the `devices_database` dict under the correct category. The category key is the Tuya category ID (e.g. `sfkzq`, `ggq`, `ms`):

```python
"sfkzq": TuyaBLECategoryInfo(
    products={
        "fdrbxxbg": TuyaBLEProductInfo(
            name="Diivoo WT-05 dual water timer",
            manufacturer="Diivoo",
        ),
        # add yours here
    },
),
```

### 2. Add data-point mappings in each platform file

Each platform file (`sensor.py`, `switch.py`, `number.py`, `select.py`, `binary_sensor.py`, `valve.py`, `climate.py`) has a `mapping` dict keyed by category. Add entries for every data-point the device exposes:

```python
"sfkzq": TuyaBLECategorySensorMapping(
    products={
        "fdrbxxbg": [
            TuyaBLESensorMapping(
                dp_id=11,
                description=SensorEntityDescription(
                    key="battery",
                    device_class=SensorDeviceClass.BATTERY,
                    native_unit_of_measurement=PERCENTAGE,
                    state_class=SensorStateClass.MEASUREMENT,
                ),
            ),
        ],
    },
),
```

### 3. Add translations in `strings.json`

Add keys under `entity.<platform>` for any new entity keys. Use HA's `[%key:...]` references where possible to inherit standard translations.

### 4. Update `README.md`

Add the device to the supported devices list at the bottom of the README.

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

## PR process

1. Fork the repo
2. Create a branch (`git checkout -b feature/my-device`)
3. Make your changes
4. Run `prek run --all-files` and `pytest` — both must pass
5. Open a PR — include the device name, product_id, and category in the PR description
