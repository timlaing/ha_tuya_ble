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

## Debug logging

The integration is silent at the `info` level. To capture a detailed trace — useful when a device is not discovered, an entity never appears, or a value looks wrong — raise the relevant loggers to `debug` in `configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    custom_components.tuya_ble: debug
    custom_components.tuya_ble.config_flow: debug
    custom_components.tuya_ble.coordinator: debug
    custom_components.tuya_ble.entity: debug
    custom_components.tuya_ble.cloud: debug
    custom_components.tuya_ble.tuya_ble: debug
```

Restart Home Assistant (or reload the config entry), reproduce the problem, then read
`home-assistant.log`.

| Logger                                   | Covers                                                                                          |
| ---------------------------------------- | ----------------------------------------------------------------------------------------------- |
| `custom_components.tuya_ble.config_flow` | Discovery, QR login, active scanning, cloud credential lookup, entry setup                      |
| `custom_components.tuya_ble.cloud`       | Token refresh, credential lookup by UUID, device cache refresh                                  |
| `custom_components.tuya_ble.coordinator` | Connect/disconnect transitions, idle timeout, received data point batches, unmapped data points |
| `custom_components.tuya_ble.entity`      | Unique-id resolution, DP-code matching, commands sent to the device                             |
| `custom_components.tuya_ble.tuya_ble`    | Data point values, batch flushes, and the raw BLE protocol (packets, AES)                       |

Each received data point is traced as
`Received DP id=<id> type=<type> flags=0x<flags> raw=<hex> decoded=<value>`. The `raw=`
field is the exact payload the device sent, so `raw=00000064` and `raw=64` can be told
apart even though both decode to `100`.

If a device reports a data point that none of its entities use, the coordinator logs it
separately:

```
... : Unmapped DP id=200 type=DT_VALUE raw=00000064 decoded=100 not used by any entity of wk/drlajpqc
```

A product that is not in the descriptor registry at all is reported as
`unknown product <category>/<product_id>` instead, because in that case every data point
is effectively unknown. Repeat reports of an unchanged data point are suppressed; a data
point is traced again as soon as its type or its bytes change.

**Credentials and tokens are never logged.** Local keys, access/refresh tokens, user codes,
QR tokens, terminal IDs and cloud endpoints are excluded from every log statement, so a
`debug` log can be attached to a bug report as-is. Device addresses, product IDs, data point
ids and values _are_ logged, since those are what make a trace useful.

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
