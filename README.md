# Home Assistant support for Tuya BLE devices

[![GitHub stars](https://img.shields.io/github/stars/timlaing/ha_tuya_ble.svg)](https://github.com/timlaing/ha_tuya_ble/stargazers)
[![GitHub issues](https://img.shields.io/github/issues/timlaing/ha_tuya_ble.svg)](https://github.com/timlaing/ha_tuya_ble/issues)
[![GitHub license](https://img.shields.io/github/license/timlaing/ha_tuya_ble.svg)](LICENSE)

[![Vulnerabilities](https://sonarcloud.io/api/project_badges/measure?project=timlaing_ha_tuya_ble&metric=vulnerabilities)](https://sonarcloud.io/summary/new_code?id=timlaing_ha_tuya_ble)
[![Security Rating](https://sonarcloud.io/api/project_badges/measure?project=timlaing_ha_tuya_ble&metric=security_rating)](https://sonarcloud.io/summary/new_code?id=timlaing_ha_tuya_ble)
[![Maintainability Rating](https://sonarcloud.io/api/project_badges/measure?project=timlaing_ha_tuya_ble&metric=sqale_rating)](https://sonarcloud.io/summary/new_code?id=timlaing_ha_tuya_ble)
[![Code Smells](https://sonarcloud.io/api/project_badges/measure?project=timlaing_ha_tuya_ble&metric=code_smells)](https://sonarcloud.io/summary/new_code?id=timlaing_ha_tuya_ble)
[![Bugs](https://sonarcloud.io/api/project_badges/measure?project=timlaing_ha_tuya_ble&metric=bugs)](https://sonarcloud.io/summary/new_code?id=timlaing_ha_tuya_ble)
[![Lines of Code](https://sonarcloud.io/api/project_badges/measure?project=timlaing_ha_tuya_ble&metric=ncloc)](https://sonarcloud.io/summary/new_code?id=timlaing_ha_tuya_ble)
[![Code style: Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)

[![HACS badge](https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge)](https://github.com/hacs/integration)

## Overview

This integration supports Tuya devices connected via BLE. After initial setup via QR code login, the integration operates **fully offline** — all device communication happens locally over Bluetooth with no cloud dependency.

_Inspired by code of [@redphx](https://github.com/redphx/poc-tuya-ble-fingerbot) & https://github.com/ProfessorQuantumUniverse/ha_tuya_ble_irrigation_

_Original HASS component forked from https://github.com/PlusPlus-ua/ha_tuya_ble_

## Installation

Place the `custom_components` folder in your configuration directory (or add its contents to an existing `custom_components` folder). Alternatively install via [HACS](https://hacs.xyz/).

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=timlaing&repository=ha_tuya_ble&category=integration)

## Usage

After adding to Home Assistant, the integration will discover all supported Bluetooth devices, or you can add discoverable devices manually.

The integration works locally, but connecting to a Tuya BLE device requires a device ID and encryption key from the Tuya cloud. This is obtained via QR code login using your Smart Life / Tuya Smart app — no Tuya IoT developer account required. After initial setup, credentials are stored locally and no further cloud connection is needed.

**Setup steps:**

1. Add the integration and enter your **User Code** (found in Smart Life app: Me > Settings > Account and Security > User Code)
2. Scan the displayed QR code in your Smart Life / Tuya Smart app
3. Select your BLE device from the discovered list

## Supported device platforms

| Platform        | Description                                                             |
| --------------- | ----------------------------------------------------------------------- |
| `binary_sensor` | Door/window state, smoke/CO alarms, button events                       |
| `button`        | Reset, program triggers                                                 |
| `climate`       | Thermostatic radiator valves (TRV)                                      |
| `cover`         | Blind and curtain controllers (open/close/stop)                         |
| `light`         | LED strip lights, lamps (on/off, brightness, color)                     |
| `lock`          | Smart locks (lock/unlock, unlock tracking)                              |
| `number`        | Countdown timers, temperature calibration, Fingerbot program parameters |
| `select`        | Mode selection, Fingerbot program mode                                  |
| `sensor`        | Battery, temperature, humidity, usage time, RSSI, work state            |
| `switch`        | On/off, child lock, window check, antifreeze, Fingerbot switches        |
| `text`          | Fingerbot program text                                                  |
| `valve`         | Water valve controllers (open/close/stop)                               |

## Supported devices

### Fingerbots (category_id `szjqr`)

- **Fingerbot** (product_ids `ltak7e1p`, `y6kttvd6`, `yrnk7mnn`, `nvr2rocq`, `bnt7wajf`, `rvdceqjh`, `5xhbk964`): original device, powered by CR2 battery.
- **Fingerbot Plus** (product_ids `blliqpsj`, `ndvkgsrm`, `yiihr7zh`, `neq16kgd`, `6jcvqwh0`, `riecov42`, `h8kdwywx`): has sensor button for manual control and program support.
- **CubeTouch 1s** (product_id `3yqdo5yt`): built-in battery with USB type C charging.
- **CubeTouch II** (product_id `xhf790if`): built-in battery with USB type C charging.
- **Nedis SmartLife Finger Robot** (product_id `yn4x5fa7`).

Programming (series of actions) is implemented for Fingerbot Plus. Exposed entities: Program (switch), Repeat forever, Repeats count, Idle position, and Program (text). Format: `position[/time];...` where position is in percent, optional time is in seconds.

### Fingerbot Plus / Switch Robot (category_id `kg`)

- **Fingerbot Plus** (product_ids `mknd4lci`, `riecov42`, `bs3ubslo`): uses kg DP IDs.
- **Switch Robot** (product_id `4ctjfrzq`).

### Temperature and humidity sensors (category_id `wsdcg`)

- Soil moisture sensor (product_id `ojzlzzsw`).
- Bluetooth Temperature Humidity Sensor (product_ids `iv7hudlj`, `jm6iasmb`, `vlzqwckk`, `tr0kabuq`).
- Soil Thermo-Hygrometer (product_id `tv6peegl`).

### CO2 sensors (category_id `co2bj`)

- CO2 Detector (product_id `59s19z5m`): bitmap alarm switches.

### Smart Locks (category_id `ms`)

- Smart Lock (product_ids `ludzroix`, `isk2p555`, `gumrixyt`, `uamrw6h3`, `sidhzylo`, `mqc2hevy`, `a6nttc41`, `okkyfgfs`, `k53ok3u9`). Supports lock/unlock, alarm events, door status, and per-device unlock tracking (BLE, fingerprint, password, card, phone remote, dynamic code).

### Smart Locks (category_id `jtmspro`)

- Raycube K7 Pro+ (product_id `xicdxood`).
- LA-01 Smart lock (product_id `oyqux5vv`).
- A1 PRO MAX (product_id `rlyxv7pe`).
- B16 (product_id `ajk32biq`).
- Smart Cylinder Lock (product_ids `z7lj676i`, `hs21i377`).
- CentralAcesso (product_id `ebd5e0uauqx0vfsp`).

Supports lock/unlock, alarm events, fingerprint/card/password unlock tracking, and battery status.

### Climate (category_id `wk`)

- Thermostatic Radiator Valve (product_ids `drlajpqc`, `nhj2j7su`, `zmachryv`). Supports temperature set, modes, and calibration. Additional switches: window check, antifreeze, child lock, water scale proof, programming mode.

### Smart water bottle (category_id `znhsb`)

- Smart water bottle (product_id `cdlandip`).

### Irrigation computer (category_id `ggq`)

- Irrigation computer (product_ids `6pahkcau`, `hfgdqhho`).
- Dual-outlet irrigation computer (product_ids `qycalacn`, `fnlw6npo`, `jjqi2syk`): separate water valve and countdown entities for each outlet.

### Water valve controllers (category_id `sfkzq`)

- Aldi/Ferrex Smart Water Valve (product_id `16wgjvck`).
- Diivoo WT-05 dual water timer (product_id `fdrbxxbg`).
- SOP10 water timer (product_id `nxquc5lb`).
- Valve controller (product_ids `svhikeyq`, `0axr5s0b`).
- Water valve controller (product_ids `46zia2nz`, `1fcnd8xk`).
- ZX-7378 Smart Irrigation Controller (product_id `ldcdnigc`).

Entities: valve (open/close/stop), battery, countdown timer, weather delay, smart weather, work state, use time.

### PARKSIDE Smart batteries (category_id `dcb`)

- PARKSIDE Smart battery 4Ah (product_id `z5ztlw3k`).
- PARKSIDE Smart battery 8Ah (product_id `ajrhf1aj`).

Entities: battery, temperature, charge/discharge current and voltage, tool diagnostics (rotation speed, torque, runtime), fault counters, configuration switches (upper temp, security, kickback, lamp, laser).

### LED strip lights and lamps (category_id `dd`)

- LGB102 Magic Strip Lights (product_id `nvfrtxlq`).
- Floor Lamp (product_id `umzu0c2y`).
- Sunset Lamp (product_id `6jxcdae1`).
- RGB Strip Light (product_id `0qgrjxum`).

Entities: on/off, brightness, color temperature, RGB color.

### Blind / curtain controllers (category_id `cl`)

- Blind Controller (product_ids `4pbr8eig`, `vlwf3ud6`).
- Curtain Controller (product_id `kcy0x4pi`).
- AOK AM24 Venetian Blinds Motor (product_id `dy4dh1q0`).

Entities: open/close/stop, battery, work state, cover speed.

### Plant sensors (category_id `zwjcy`)

- SRB-PM01 Soil Moisture Sensor (product_id `jabotj1z`).
- Smartlife Plant Sensor SGS01 (product_id `gvygg3m8`).

Entities: temperature, humidity, battery state, battery percentage.

## Acknowledgements

This integration's QR code login approach was inspired by
[tuya-local-key](https://github.com/vineetchoudhary/tuya-local-key),
which demonstrates retrieving Tuya device local keys via QR code login
without requiring a Tuya IoT developer account.
