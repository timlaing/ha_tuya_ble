# Home Assistant support for Tuya BLE devices

[![Build Status](https://github.com/timlaing/ha_tuya_ble/actions/workflows/test.yml/badge.svg)](https://github.com/timlaing/ha_tuya_ble/actions/workflows/test.yml)
[![GitHub stars](https://img.shields.io/github/stars/timlaing/ha_tuya_ble.svg)](https://github.com/timlaing/ha_tuya_ble/stargazers)
[![GitHub issues](https://img.shields.io/github/issues/timlaing/ha_tuya_ble.svg)](https://github.com/timlaing/ha_tuya_ble/issues)
[![GitHub license](https://img.shields.io/github/license/timlaing/ha_tuya_ble.svg)](LICENSE)

[![Lines of Code](https://sonarcloud.io/api/project_badges/measure?project=timlaing_ha_tuya_ble&metric=ncloc)](https://sonarcloud.io/summary/new_code?id=timlaing_ha_tuya_ble)
[![Code style: Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Duplicated Lines (%)](https://sonarcloud.io/api/project_badges/measure?project=timlaing_ha_tuya_ble&metric=duplicated_lines_density)](https://sonarcloud.io/summary/new_code?id=timlaing_ha_tuya_ble)
[![Coverage](https://sonarcloud.io/api/project_badges/measure?project=timlaing_ha_tuya_ble&metric=coverage)](https://sonarcloud.io/summary/new_code?id=timlaing_ha_tuya_ble)

[![Vulnerabilities](https://sonarcloud.io/api/project_badges/measure?project=timlaing_ha_tuya_ble&metric=vulnerabilities)](https://sonarcloud.io/summary/new_code?id=timlaing_ha_tuya_ble)
[![Security Rating](https://sonarcloud.io/api/project_badges/measure?project=timlaing_ha_tuya_ble&metric=security_rating)](https://sonarcloud.io/summary/new_code?id=timlaing_ha_tuya_ble)
[![Maintainability Rating](https://sonarcloud.io/api/project_badges/measure?project=timlaing_ha_tuya_ble&metric=sqale_rating)](https://sonarcloud.io/summary/new_code?id=timlaing_ha_tuya_ble)
[![Reliability Rating](https://sonarcloud.io/api/project_badges/measure?project=timlaing_ha_tuya_ble&metric=reliability_rating)](https://sonarcloud.io/summary/new_code?id=timlaing_ha_tuya_ble)
[![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=timlaing_ha_tuya_ble&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=timlaing_ha_tuya_ble)
[![Code Smells](https://sonarcloud.io/api/project_badges/measure?project=timlaing_ha_tuya_ble&metric=code_smells)](https://sonarcloud.io/summary/new_code?id=timlaing_ha_tuya_ble)
[![Bugs](https://sonarcloud.io/api/project_badges/measure?project=timlaing_ha_tuya_ble&metric=bugs)](https://sonarcloud.io/summary/new_code?id=timlaing_ha_tuya_ble)
[![Technical Debt](https://sonarcloud.io/api/project_badges/measure?project=timlaing_ha_tuya_ble&metric=sqale_index)](https://sonarcloud.io/summary/new_code?id=timlaing_ha_tuya_ble)

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

**Manual credentials (fallback):** if the app login cannot find your device but you
already have its credentials, choose **Enter the device credentials manually** instead of
logging in. Enter the UUID, local key, device ID, category and product ID, plus the
security key only for devices that use the newer Security V2 login. Home Assistant checks
them over Bluetooth before adding the device, so the product must already be supported.
If the check fails for a protocol v4 device, setup offers to add it anyway, as with the app
login. The keys are stored only in Home Assistant and are never shown again.

To change the keys of an existing device, for example after pairing it again in the app,
open the integration's **Configure** menu and choose **Replace the device keys**. Both keys
are replaced together; leave the security key empty to remove it. The device ID, UUID and
entities are kept. The new keys are not checked over Bluetooth before they are saved.

### Refresh a lock's unlock check

Locks using the timestamped Bluetooth open command need a cloud-issued unlock check.
If it is missing or was re-issued to another app member, open the integration's
**Configure** menu and choose **Refresh the lock unlock check**. Submit to fetch the
current check from the Tuya Sharing SDK cloud status. The lock need not be connected
over Bluetooth. A valid check is saved and the integration reloads; entity unique
IDs, device identity, BLE keys and all other entry data stay unchanged.

If no cloud login is stored, first choose **Log in again with the QR code**, scan
with the account containing that device, then open Configure again to refresh.
This also links manually added entries, but their stored device ID and cloud UUID
must match the cloud record. No check can be fetched for a manual-only device not
in a Tuya account; entering an unlock check by hand is not supported.

Network failures, missing checks, malformed checks and unmatched devices keep the
existing check. For a missing check, open the lock once in Smart Life or Tuya Smart
and retry. For expired login, use the QR login again. Neither the check nor member
credentials or commands containing them are shown in errors, logs or diagnostics;
diagnostics expose only `ble_unlock_check_stored`.

Hardware acceptance (not yet performed here): safely test **Open** on a supported
timestamped-command lock, refresh after its app check changes, and confirm **Open**
uses the new member's authorization without changing entity IDs. Test refresh while
the lock is unavailable, reload/reconnect, and a cloud failure retaining the previous
check. Download diagnostics and confirm DP 71/check values remain redacted.

For Tuya BLE protocol v4 devices, setup checks that Home Assistant can connect
over Bluetooth and remembers how, so the device reconnects after restarts
without the cloud. Keep the device nearby and close the Smart Life app after
logging in, because the app can hold the device's Bluetooth connection. To run
the check again for an existing device, use **Reconfigure**; entities are kept.
If the check fails, setup tells you and lets you add the device anyway. It may
then show as unavailable, but its diagnostics download helps add support.
See [Tuya BLE protocol v4 details](docs/PROTOCOL_4.md).

### Connection settings

By default Home Assistant keeps each device's Bluetooth connection open and reconnects
whenever it drops. This gives the fastest response, but it uses one connection slot on
the Bluetooth adapter or ESPHome proxy, and a battery-powered device such as a Fingerbot
spends battery on the open link and on every reconnect. To change this, open the
integration's **Configure** menu and choose **Connection settings**:

- **Keep the Bluetooth connection open** (default on): today's behaviour.
- **Idle delay before disconnecting** (5–3600 s, default 30): with the connection not
  kept open, Home Assistant connects when a command is sent, which adds about 1–3
  seconds, and disconnects once the device has been idle for this long. Each command
  restarts the delay, so a burst of commands shares one connection. A connection that
  drops is not reopened until the next command or status refresh.
- **Status refresh interval** (0 or 60–86400 s, default 900): with the connection not
  kept open, a device only reports its values while connected, so Home Assistant
  connects this often to read them. Every connection also reads them. Enter 0 for
  devices whose values only change through commands, for example a Fingerbot.

Saving changed settings reloads the device; its entities are kept. With the connection
not kept open, the device's entities become available once Home Assistant has connected
to it after startup. They stay available between connections while Home Assistant's
Bluetooth integration still receives the device's advertisements, and become unavailable
when it marks the device as unavailable, until advertisements arrive again. A command
that cannot connect fails with an error; a failed connection is then retried in the
background, as with the connection kept open, until it succeeds.

Battery devices also stay awake while a Bluetooth proxy in active scanning mode is close
to them: a battery pack's Bluetooth module may never go to sleep. If a battery drains
while not in use, consider a passive proxy near it, or moving the proxy further away
(reported in ha-tuya-ble/ha_tuya_ble#283).

## Extra app login for some Bluetooth devices

If a device uses Tuya BLE protocol v4 Security V2 and the usual QR connection
does not provide its extra security key, setup offers a one-time app login.
Devices that already connect normally never need this step. Select **Tuya**
or **Smart Life**, enter your email or phone number and app password, then
optionally enter a numeric country dial code. A code is required for phone
login. Email login uses your supplied code or a default from the QR cloud
region (Europe uses 44).

The app's matching local key and security key are retrieved together and
checked over Bluetooth. Only the device keys are stored for offline operation;
your app password and mobile session are not saved. The app API is private:
captcha, MFA, social sign-in and expired application profiles can prevent
login. You can still add the device without verification to collect diagnostics.

For an existing device, use **Reconfigure**. Older entries may first ask for
a QR login to determine the account's cloud region. Recovery keeps entity IDs
and changes existing keys only after successful Bluetooth verification.

Diagnostics include a stored, source-labelled `mobile_cloud_info` snapshot
of device details, product information and status. Only explicitly approved
metadata values are retained; other fields show their type and size. Unknown
containers, live DP values, signed URLs, account identifiers and credentials
are withheld before the snapshot is stored. Diagnostics do not log into the
mobile service or automatically refresh this snapshot.
Known structural field names are preserved; opaque or unrecognized mapping
keys are anonymized as well, so identifiers cannot leak through field names.

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
    custom_components.tuya_ble.device_registry: debug
    custom_components.tuya_ble.tuya_ble: debug
```

Restart Home Assistant (or reload the config entry), reproduce the problem, then read
`home-assistant.log`.

| Logger                                       | Covers                                                                                          |
| -------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| `custom_components.tuya_ble.config_flow`     | Discovery, QR login, active scanning, cloud or manual credential checks, entry setup            |
| `custom_components.tuya_ble.cloud`           | Token refresh, credential lookup by UUID, device cache refresh                                  |
| `custom_components.tuya_ble.coordinator`     | Connect/disconnect transitions, idle timeout, received data point batches, unmapped data points |
| `custom_components.tuya_ble.entity`          | Unique-id resolution, DP-code matching, commands sent to the device                             |
| `custom_components.tuya_ble.device_registry` | Descriptor loading, validation errors, which descriptor a product resolved to                   |
| `custom_components.tuya_ble.tuya_ble`        | Data point values, batch flushes, and the raw BLE protocol (packets, AES)                       |

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
is effectively unknown. The config entry still loads — a descriptor-less device must not
hold up the whole Home Assistant startup — so it appears with **no entities** and a second
warning naming the category and product:

```
... : no descriptor for category ms / product unknownproduct; loading with no entities
```

Both are logged at `warning` level, but they are not equally visible: the
`no descriptor …` line is emitted once at setup, so it appears at the default log level,
whereas `unknown product …` comes from the unmapped-data-point reporting, which only runs
when `custom_components.tuya_ble.coordinator` is set to `debug` and the device actually
reports a data point no entity uses.

If a device you own shows up with no entities at all, the setup warning is the place to
start — it means the integration has no descriptor for it, and the category/product pair in
it is what a new descriptor has to be written against. You do not need debug logging to see
that one. Repeat reports of an unchanged data point are suppressed; a data point is traced
again as soon as its type or its bytes change.

**Credentials and tokens are never logged.** Local keys, access/refresh tokens, user codes,
QR tokens, terminal IDs and cloud endpoints are excluded from every log statement, so a
`debug` log can be attached to a bug report as-is. Device addresses, product IDs, data point
ids and values _are_ logged, since those are what make a trace useful.

## Diagnostics

Both the integration and each device expose **Download diagnostics** from the Home Assistant
UI, which produces a JSON file for a bug report:

- **Device → ⋮ → Download diagnostics** — the live data points with their raw bytes, the
  descriptor that was resolved for the product, and the Home Assistant device/registry entry.
- **Settings → Devices & services → Tuya BLE → ⋮ → Download diagnostics** — the above for
  every device, plus the entry-wide view.

A download includes the device's **full cloud schema**: the local strategy for every data
point with its value format, value type, enum mapping and scale, and the status range with
each data point's `report_type`. That last field is the reason a download is often enough
to settle a bug — it distinguishes a device that reports a data point incrementally
(`sum`) from one that reports the running total (`minux`, Tuya's own name for a full value).
Treating full values as increments is the usual explanation for a number that looks too large.

The schema is captured once at setup and stored on the config entry, so a download works
even when the device is offline or the cloud is unreachable. When re-authentication
credentials are available the integration refreshes the schema from the cloud first; if
that fails it falls back to the stored copy and says so in the `schema_source` field.

Secrets are removed before the file is written — local key, access and refresh tokens, user
code, terminal ID and cloud endpoint. The MAC `uuid`, the cloud `device_id`, product and
data point ids, and entity data are **not** removed: they identify the device without being
able to reach it, and a download that cannot be matched to the reported problem is not much
use. Review a file before attaching it to a public issue.

## Supported device platforms

| Platform        | Description                                                             |
| --------------- | ----------------------------------------------------------------------- |
| `binary_sensor` | Door/window state, smoke/CO alarms, button events                       |
| `button`        | Reset, program triggers                                                 |
| `climate`       | Thermostatic radiator valves (TRV)                                      |
| `cover`         | Blind and curtain controllers (open/close/stop)                         |
| `lawn_mower`    | Robot lawn mowers (start/pause/stop/dock)                               |
| `event`         | Wireless button clicks and long presses — experimental                  |
| `light`         | LED strip lights, lamps (on/off, brightness, color)                     |
| `lock`          | Smart locks (lock/unlock, unlock tracking)                              |
| `number`        | Countdown timers, temperature calibration, Fingerbot program parameters |
| `select`        | Mode selection, Fingerbot program mode                                  |
| `sensor`        | Battery, temperature, humidity, usage time, RSSI, work state            |
| `switch`        | On/off, child lock, window check, antifreeze, Fingerbot switches        |
| `text`          | Fingerbot program text                                                  |
| `vacuum`        | Cleaning robots (start/stop, cleaning mode) — experimental              |
| `valve`         | Water valve controllers (open/close/stop)                               |

## Supported device categories

The integration supports a broad range of Tuya BLE devices across the following categories:

| Category                                 | Examples                                                                  |
| ---------------------------------------- | ------------------------------------------------------------------------- |
| Fingerbots (`szjqr`, `kg`)               | Fingerbot, Fingerbot Plus, CubeTouch, Nedis Finger Robot, Switch Robot    |
| Temperature & humidity sensors (`wsdcg`) | Soil moisture sensors, BLE temp/humidity sensors, soil thermo-hygrometers |
| CO2 sensors (`co2bj`)                    | CO2 detectors                                                             |
| Water quality (`sh`)                     | Yieryi BLE-YL01 (experimental read-only gateway-schema subset)            |
| Smart locks (`ms`, `jtmspro`)            | Smart locks, cylinder locks, Raycube K7 Pro+, CentralAcesso               |
| Wireless buttons (`wxkg`)                | Arlec SG022HA / ZG101Z (experimental; string reports only)                |
| Climate (`wk`)                           | Thermostatic radiator valves (TRV)                                        |
| Climate (`wkf`)                          | Bluetooth Smart TRV `llflaywg` ([experimental](docs/LLFLAYWG_TRV.md))     |
| Heaters (`qn`)                           | MaXpeedingRods diesel parking heater (experimental)                       |
| Irrigation & water (`ggq`, `sfkzq`)      | Irrigation computers, water valves, dual water timers                     |
| Smart water bottle (`znhsb`)             | Smart water bottles                                                       |
| PARKSIDE batteries (`dcb`)               | Smart batteries 4Ah / 8Ah, 12Ah (experimental, sensors only)              |
| Lights (`dd`, `dj`)                      | LED strip lights, floor/sunset lamps, wall lamps                          |
| Ceiling lights (`xdd`)                   | Globo WiFi/BLE CCT ceiling light                                          |
| Blinds & curtains (`cl`)                 | Blind/curtain controllers, venetian blind motors                          |
| Plant sensors (`zwjcy`)                  | Soil moisture / plant sensors                                             |
| Generators (`qt`)                        | MaXpeedingRods MXR4000GT inverter generator                               |
| Window cleaning robots (`ccjqr`)         | CHYW200.ABIR window cleaner (experimental)                                |
| Water meters (`slj`)                     | RESTMO FML026A Bluetooth water meter                                      |
| Aroma diffusers (`jsq`)                  | Dituo DT-T2190A aroma diffuser                                            |
| Cooking thermometers (`swtz`)            | GRILLMEISTER / SK-T100, BTH08 meat thermometers                           |
| Robot mowers (`gcj`)                     | PARKSIDE PAMRC 250 A1 (BT) City robot mower (experimental)                |

Category codes are Tuya's, and several of them do not mean what their name suggests — `kg` is
"Switch" and holds Fingerbot Plus, `znhsb` is "Smart glass" but the product is a water bottle.
See [docs/TUYA_CATEGORIES.md](docs/TUYA_CATEGORIES.md) for what each code actually covers.

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
