# Tuya category reference

Every device reports a **category** (`category`) and a **product id** (`product_id`) — for
example `szjqr/blliqpsj` or `ms/gumrixyt`. Both arrive from the Tuya cloud when the device is
added, and the pair is what identifies a device: a product id on its own means nothing, because
several categories reuse the same product ids.

The category decides three things:

- **Which file holds the descriptor** — `<category>_<product_id>.yaml`.
- **Which category defaults apply** — a `_category_<category>.yaml` file supplies entities for
  every product in that category, but only for the platforms a product does not define itself.
- **Nothing else.** Per-product metadata such as `manufacturer` and `device_name` lives in the
  descriptor, keyed by product id, not by category.

Supporting a category means supporting a product id inside it — one descriptor is enough. That
is how most of the categories below got their first device.

## Categories this integration supports

| Category  | Tuya's official name                       | Notes                                                                                                                                                                                                               |
| --------- | ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `szjqr`   | Fingerbot (undocumented)                   | Undocumented by Tuya; the community name is Fingerbot.                                                                                                                                                              |
| `ms`      | Residential lock                           | Residential lock. `_category_ms.yaml` supplies the default `lock` entity (DP 47) for every `ms` product that does not define its own. Its accessories report `ms_category` — see [adjacent codes](#adjacent-codes). |
| `sfkzq`   | Smart Water Timer (undocumented)           |                                                                                                                                                                                                                     |
| `ggq`     | Irrigator                                  |                                                                                                                                                                                                                     |
| `jtmspro` | Residential lock pro                       | Residential lock **PRO**. Its entity layout is identical to `ms`.                                                                                                                                                   |
| `wsdcg`   | Temperature and humidity sensor            |                                                                                                                                                                                                                     |
| `cl`      | Curtain                                    | Curtain **motors**. `_category_cl.yaml` supplies the default `cover` entity. Curtain robots are a different code — see [gotchas](#gotchas).                                                                         |
| `kg`      | Switch                                     | Officially **Switch**. Fingerbot Plus is sold under it, which is why the [README table](../README.md#supported-device-categories) groups `kg` with `szjqr`. See [gotchas](#gotchas).                                |
| `wk`      | Thermostat                                 |                                                                                                                                                                                                                     |
| `dcb`     | _not in Tuya's list_                       | Not in Tuya's published list. PARKSIDE smart batteries.                                                                                                                                                             |
| `zwjcy`   | Soil sensor - plant monitor (undocumented) |                                                                                                                                                                                                                     |
| `co2bj`   | CO2 detector                               |                                                                                                                                                                                                                     |
| `dd`      | Strip lights                               | Strip lights. `_category_dd.yaml` supplies the default `light` entity for every `dd` product that does not define its own.                                                                                          |
| `znhsb`   | Smart glass (not in Tuya's list)           | Officially _Smart glass_, but the `cdlandip` product here is a smart water bottle. See [gotchas](#gotchas).                                                                                                         |

To see the products in a category:

```sh
ls custom_components/tuya_ble/device_descriptors/ | grep '^<category>_'
```

### Adjacent codes

These turn up in log lines and issue reports but are not categories with descriptors here:

| Code          | What it is                                                                                                       |
| ------------- | ---------------------------------------------------------------------------------------------------------------- |
| `ms_category` | Tuya category for lock **accessories**. A `ms` lock's key modules and fingerprint readers report this, not `ms`. |
| `jdcljqr`     | Curtain robot. A `cljqr` code, unrelated to `cl`.                                                                |
| `cljqr`       | Curtain robot. See [gotchas](#gotchas) before reusing the `cl` entity layout.                                    |
| `cdlandip`    | Not a category — a product id. The smart water bottle's category is `znhsb`.                                     |

## All Tuya categories

The 138 category codes in Tuya's developer documentation, for reading a log line or a
cloud response. Ones marked _undocumented_ are absent from that documentation but are known
from shipped devices. ✓ marks the 12 of the 14 categories this integration supports that
appear in Tuya's list. The other two, `dcb` and `znhsb`, are not in Tuya's documentation at
all, so they have no row here — they are in the table above.

| Category      | Official name                                                      | Used here |
| ------------- | ------------------------------------------------------------------ | :-------: |
| `amy`         | Massage chair                                                      |           |
| `aqcz`        | Single Phase power meter (undocumented)                            |           |
| `bgl`         | Wall-hung boiler                                                   |           |
| `bh`          | Smart kettle                                                       |           |
| `bx`          | Refrigerator                                                       |           |
| `bxx`         | Safe box                                                           |           |
| `bzyd`        | White noise machine (undocumented)                                 |           |
| `cjkg`        | Scene switch                                                       |           |
| `ckmkzq`      | Garage door opener                                                 |           |
| `ckqdkg`      | Card switch                                                        |           |
| `cl`          | Curtain                                                            |     ✓     |
| `clkg`        | Curtain switch                                                     |           |
| `cn`          | Milk dispenser                                                     |           |
| `co2bj`       | CO2 detector                                                       |     ✓     |
| `cobj`        | CO detector                                                        |           |
| `cs`          | Dehumidifier                                                       |           |
| `cwjwq`       | Smart Odor Eliminator-Pro (undocumented)                           |           |
| `cwtswsq`     | Pet treat feeder                                                   |           |
| `cwwqfsq`     | Pet ball thrower                                                   |           |
| `cwwsq`       | Pet feeder                                                         |           |
| `cwysj`       | Pet fountain                                                       |           |
| `cz`          | Socket                                                             |           |
| `dbl`         | Electric fireplace                                                 |           |
| `dc`          | String lights                                                      |           |
| `dcl`         | Induction cooker                                                   |           |
| `dd`          | Strip lights                                                       |     ✓     |
| `dghsxj`      | Smart Camera - Low power consumption camera (undocumented)         |           |
| `dgnbj`       | Multi-functional alarm                                             |           |
| `dj`          | Light                                                              |           |
| `dlq`         | Circuit breaker                                                    |           |
| `dr`          | Electric blanket                                                   |           |
| `ds`          | TV set                                                             |           |
| `dsd`         | Filament Light                                                     |           |
| `fs`          | Fan                                                                |           |
| `fsd`         | Ceiling fan light                                                  |           |
| `fskg`        | Fan wall switch (undocumented)                                     |           |
| `fwd`         | Ambiance light                                                     |           |
| `ggq`         | Irrigator                                                          |     ✓     |
| `gyd`         | Motion sensor light                                                |           |
| `gyms`        | Business lock                                                      |           |
| `hcdd`        | Chasing tape light (undocumented)                                  |           |
| `hjjcy`       | Air Quality Monitor                                                |           |
| `hotelms`     | Hotel lock                                                         |           |
| `hps`         | Human presence sensor                                              |           |
| `hxd`         | Wake Up Light II (undocumented)                                    |           |
| `jdcljqr`     | Curtain Robot (undocumented)                                       |           |
| `jqbj`        | Formaldehyde Detector (undocumented)                               |           |
| `js`          | Water purifier                                                     |           |
| `jsq`         | Humidifier                                                         |           |
| `jtmsbh`      | Smart lock (keep alive)                                            |           |
| `jtmspro`     | Residential lock pro                                               |     ✓     |
| `jwbj`        | Methane detector                                                   |           |
| `kfj`         | Coffee maker                                                       |           |
| `kg`          | Switch                                                             |     ✓     |
| `kj`          | Air purifier                                                       |           |
| `kqzg`        | Air fryer                                                          |           |
| `ks`          | Tower fan (undocumented)                                           |           |
| `kt`          | Air conditioner                                                    |           |
| `ktkzq`       | Air conditioner controller                                         |           |
| `ldcg`        | Luminance sensor                                                   |           |
| `liliao`      | Physiotherapy product                                              |           |
| `lyj`         | Drying rack                                                        |           |
| `mal`         | Alarm host                                                         |           |
| `mb`          | Bread maker                                                        |           |
| `mbd`         | Unknown light product                                              |           |
| `mc`          | Door/window controller                                             |           |
| `mcs`         | Contact sensor                                                     |           |
| `mg`          | Rice cabinet                                                       |           |
| `mjj`         | Towel rack                                                         |           |
| `mk`          | Access control                                                     |           |
| `ms`          | Residential lock                                                   |     ✓     |
| `ms_category` | Lock accessories                                                   |           |
| `msp`         | Cat toilet                                                         |           |
| `mzj`         | Sous vide cooker                                                   |           |
| `nnq`         | Bottle warmer                                                      |           |
| `ntq`         | HVAC                                                               |           |
| `pc`          | Power strip                                                        |           |
| `photolock`   | Audio and video lock                                               |           |
| `pir`         | Human motion sensor                                                |           |
| `pm2.5`       | PM2.5 detector                                                     |           |
| `qccdz`       | AC charging (undocumented)                                         |           |
| `qjdcz`       | Unknown product with light capabilities                            |           |
| `qn`          | Heater                                                             |           |
| `qxj`         | Temperature and Humidity Sensor with External Probe (undocumented) |           |
| `rqbj`        | Gas alarm                                                          |           |
| `rs`          | Water heater                                                       |           |
| `sb`          | Watch/band                                                         |           |
| `sd`          | Robot vacuum                                                       |           |
| `sf`          | Sofa                                                               |           |
| `sfkzq`       | Smart Water Timer (undocumented)                                   |     ✓     |
| `sgbj`        | Siren alarm                                                        |           |
| `sj`          | Water leak detector                                                |           |
| `sjz`         | Electric desk (undocumented)                                       |           |
| `sos`         | Emergency button                                                   |           |
| `sp`          | Smart camera                                                       |           |
| `swtz`        | Cooking thermometer (undocumented)                                 |           |
| `sz`          | Smart indoor garden                                                |           |
| `szjcy`       | Water tester (undocumented)                                        |           |
| `szjqr`       | Fingerbot (undocumented)                                           |     ✓     |
| `tdq`         | Dimmer (undocumented)                                              |           |
| `tgkg`        | Dimmer switch                                                      |           |
| `tgq`         | Dimmer                                                             |           |
| `tnq`         | Smart milk kettle                                                  |           |
| `tracker`     | Tracker                                                            |           |
| `ts`          | Smart jump rope                                                    |           |
| `tyd`         | Outdoor flood light (undocumented)                                 |           |
| `tyndj`       | Solar light                                                        |           |
| `tyy`         | Projector                                                          |           |
| `tzc1`        | Body fat scale                                                     |           |
| `videolock`   | Lock with camera                                                   |           |
| `voc`         | Volatile Organic Compound Sensor (undocumented)                    |           |
| `wg2`         | Gateway control                                                    |           |
| `wk`          | Thermostat                                                         |     ✓     |
| `wkcz`        | Two-way temperature and humidity switch (undocumented)             |           |
| `wkf`         | Thermostatic Radiator Valve (undocumented)                         |           |
| `wnykq`       | Smart WiFi IR Remote (undocumented)                                |           |
| `wsdcg`       | Temperature and humidity sensor                                    |     ✓     |
| `wxkg`        | Wireless Switch                                                    |           |
| `xdd`         | Ceiling light                                                      |           |
| `xfj`         | Ventilation system                                                 |           |
| `xnyjcn`      | Micro Storage Inverter                                             |           |
| `xxj`         | Diffuser                                                           |           |
| `xy`          | Washing machine                                                    |           |
| `yb`          | Bathroom heater                                                    |           |
| `yg`          | Bathtub                                                            |           |
| `ykq`         | Remote control                                                     |           |
| `ylcg`        | Pressure sensor                                                    |           |
| `ywbj`        | Smoke alarm                                                        |           |
| `ywcgq`       | Tank Level Sensor (undocumented)                                   |           |
| `zd`          | Vibration sensor                                                   |           |
| `zndb`        | Smart electricity meter                                            |           |
| `znfh`        | Bento box                                                          |           |
| `znjxs`       | Hejhome whitelabel Fingerbot (undocumented)                        |           |
| `znnbq`       | VESKA-micro inverter (undocumented)                                |           |
| `znrb`        | Pool HeatPump (undocumented)                                       |           |
| `znsb`        | Smart water meter                                                  |           |
| `znyh`        | Smart pill box                                                     |           |
| `zwjcy`       | Soil sensor - plant monitor (undocumented)                         |     ✓     |

## Gotchas

### `kg` is "Switch", and it is where Fingerbot Plus lives

Fingerbot Plus reports category `kg`, not `szjqr`. Searching `szjqr` for a Fingerbot Plus will
not find it — and `szjqr` is itself undocumented, being what Tuya uses for the plain Fingerbot.
Both categories share the same `mode.*` handlers, so their entity definitions are nearly
identical, which hides the difference if you compare descriptors instead of cloud data.

### Curtain robots must not inherit `_category_cl.yaml`

A curtain **robot** (`cljqr`, `jdcljqr`) is not a curtain **motor** (`cl`). A motor reports a
controllable position (`position_set_dp_id`, `position_dp_id`); a robot only reports the two end
states and the app drives it to one of them. Applying the `_category_cl.yaml` defaults to a
robot produces a cover entity that advertises a position it can never report, so the slider
appears and then does nothing. Give robots their own descriptor.

### `znhsb` means "Smart glass"

The category name is misleading: the product supported here, `cdlandip`, is a smart water
bottle, and there is no glass device to compare it against. Trust the data points.

### `ms_category` is not `ms`

A `ms` lock can have accessory sub-devices — key modules, fingerprint readers — that report
`ms_category`. They are separate devices in the cloud, and a descriptor for `ms` does not
apply to them.

## Finding the category of a new device

1. Add the device through the config flow, then look for the
   `no descriptor for category <category> / product <product_id>` warning — both halves are
   in it, and it is emitted at setup for every device with no descriptor.
2. The cloud `dev_category` field in the device list response carries it, if you read the
   response with the integration's debug logging enabled.
3. Look the code up in the table above to confirm the official name before guessing at a
   mapping.

Then see [CONTRIBUTING.md](../CONTRIBUTING.md) for creating the descriptor, and
[SUPPORTED_DEVICES.md](../SUPPORTED_DEVICES.md) for every supported device and product id.
