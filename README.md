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

## Supported device categories

The integration supports a broad range of Tuya BLE devices across the following categories:

| Category                                 | Examples                                                                  |
| ---------------------------------------- | ------------------------------------------------------------------------- |
| Fingerbots (`szjqr`, `kg`)               | Fingerbot, Fingerbot Plus, CubeTouch, Nedis Finger Robot, Switch Robot    |
| Temperature & humidity sensors (`wsdcg`) | Soil moisture sensors, BLE temp/humidity sensors, soil thermo-hygrometers |
| CO2 sensors (`co2bj`)                    | CO2 detectors                                                             |
| Smart locks (`ms`, `jtmspro`)            | Smart locks, cylinder locks, Raycube K7 Pro+, CentralAcesso               |
| Climate (`wk`)                           | Thermostatic radiator valves (TRV)                                        |
| Irrigation & water (`ggq`, `sfkzq`)      | Irrigation computers, water valves, dual water timers                     |
| Smart water bottle (`znhsb`)             | Smart water bottles                                                       |
| PARKSIDE batteries (`dcb`)               | Smart batteries 4Ah / 8Ah                                                 |
| Lights (`dd`)                            | LED strip lights, floor/sunset lamps                                      |
| Blinds & curtains (`cl`)                 | Blind/curtain controllers, venetian blind motors                          |
| Plant sensors (`zwjcy`)                  | Soil moisture / plant sensors                                             |

For the full, up-to-date list of supported devices with their product IDs, see [SUPPORTED_DEVICES.md](SUPPORTED_DEVICES.md).

## Contributing

Contributions are welcome, whether it's a new device, a bug fix, or an improvement. See [CONTRIBUTING.md](CONTRIBUTING.md) for details on:

- Adding support for a new device
- Setting up a development environment
- Running the lint/format checks and unit tests
- The PR process

Please open an [issue](https://github.com/timlaing/ha_tuya_ble/issues) or [pull request](https://github.com/timlaing/ha_tuya_ble/pulls) on GitHub.

## Acknowledgements

This integration's QR code login approach was inspired by
[tuya-local-key](https://github.com/vineetchoudhary/tuya-local-key),
which demonstrates retrieving Tuya device local keys via QR code login
without requiring a Tuya IoT developer account.
