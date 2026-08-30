# Supported devices

## Fingerbots (category_id `szjqr`)

- **Fingerbot** (product_ids `ltak7e1p`, `y6kttvd6`, `yrnk7mnn`, `nvr2rocq`, `bnt7wajf`, `rvdceqjh`, `5xhbk964`): original device, powered by CR2 battery.
- **Fingerbot Plus** (product_ids `blliqpsj`, `ndvkgsrm`, `yiihr7zh`, `neq16kgd`, `6jcvqwh0`, `riecov42`, `h8kdwywx`): has sensor button for manual control and program support.
- **CubeTouch 1s** (product_id `3yqdo5yt`): built-in battery with USB type C charging.
- **CubeTouch II** (product_id `xhf790if`): built-in battery with USB type C charging.
- **Nedis SmartLife Finger Robot** (product_id `yn4x5fa7`).

Programming (series of actions) is implemented for Fingerbot Plus. Exposed entities: Program (switch), Repeat forever, Repeats count, Idle position, and Program (text). Format: `position[/time];...` where position is in percent, optional time is in seconds.

## Fingerbot Plus / Switch Robot (category_id `kg`)

- **Fingerbot Plus** (product_ids `mknd4lci`, `riecov42`, `bs3ubslo`): uses kg DP IDs.
- **Switch Robot** (product_id `4ctjfrzq`).

## Temperature and humidity sensors (category_id `wsdcg`)

- Soil moisture sensor (product_id `ojzlzzsw`).
- Bluetooth Temperature Humidity Sensor (product_ids `iv7hudlj`, `jm6iasmb`, `vlzqwckk`, `tr0kabuq`).
- Soil Thermo-Hygrometer (product_id `tv6peegl`).

## CO2 sensors (category_id `co2bj`)

- CO2 Detector (product_id `59s19z5m`): bitmap alarm switches.

## Smart Locks (category_id `ms`)

- Smart Lock (product_ids `ludzroix`, `isk2p555`, `gumrixyt`, `uamrw6h3`, `sidhzylo`, `mqc2hevy`, `a6nttc41`, `okkyfgfs`, `k53ok3u9`). Supports lock/unlock, alarm events, door status, and per-device unlock tracking (BLE, fingerprint, password, card, phone remote, dynamic code).

## Smart Locks (category_id `jtmspro`)

- Raycube K7 Pro+ (product_id `xicdxood`).
- LA-01 Smart lock (product_id `oyqux5vv`).
- A1 PRO MAX (product_id `rlyxv7pe`).
- B16 (product_id `ajk32biq`).
- Smart Cylinder Lock (product_ids `z7lj676i`, `hs21i377`).
- CentralAcesso (product_id `ebd5e0uauqx0vfsp`).

Supports lock/unlock, alarm events, fingerprint/card/password unlock tracking, and battery status.

## Climate (category_id `wk`)

- Thermostatic Radiator Valve (product_ids `drlajpqc`, `nhj2j7su`, `zmachryv`). Supports temperature set, modes, and calibration. Additional switches: window check, antifreeze, child lock, water scale proof, programming mode.

## Smart water bottle (category_id `znhsb`)

- Smart water bottle (product_id `cdlandip`).

## Irrigation computer (category_id `ggq`)

- Irrigation computer (product_ids `6pahkcau`, `hfgdqhho`).
- Dual-outlet irrigation computer (product_ids `fnlw6npo`, `jjqi2syk`): separate water valve and countdown entities for each outlet.
- Dual water timer (product_ids `fdrbxxbg`, `jntxv3q4`, `qycalacn`): separate water valve and countdown entities for each outlet.

## Water valve controllers (category_id `sfkzq`)

- Aldi/Ferrex Smart Water Valve (product_id `16wgjvck`).
- SOP10 water timer (product_ids `nxquc5lb`, `c8800fd30884068f`, `so5ybnw9`).
- Valve controller (product_ids `svhikeyq`, `0axr5s0b`).
- Water valve controller (product_ids `46zia2nz`, `1fcnd8xk`).
- ZX-7378 Smart Irrigation Controller (product_id `ldcdnigc`).

Entities: valve (open/close/stop), battery, countdown timer, weather delay, smart weather, work state, use time.

## PARKSIDE Smart batteries (category_id `dcb`)

- PARKSIDE Smart battery 4Ah (product_id `z5ztlw3k`).
- PARKSIDE Smart battery 8Ah (product_id `ajrhf1aj`).

Entities: battery, temperature, charge/discharge current and voltage, tool diagnostics (rotation speed, torque, runtime), fault counters, configuration switches (upper temp, security, kickback, lamp, laser).

## LED strip lights and lamps (category_id `dd`)

- LGB102 Magic Strip Lights (product_id `nvfrtxlq`).
- Floor Lamp (product_id `umzu0c2y`).
- Sunset Lamp (product_id `6jxcdae1`).
- RGB Strip Light (product_id `0qgrjxum`).

Entities: on/off, brightness, color temperature, RGB color.

## Blind / curtain controllers (category_id `cl`)

- Blind Controller (product_ids `4pbr8eig`, `vlwf3ud6`).
- Curtain Controller (product_id `kcy0x4pi`).
- AOK AM24 Venetian Blinds Motor (product_id `dy4dh1q0`).

Entities: open/close/stop, battery, work state, cover speed.

## Plant sensors (category_id `zwjcy`)

- SRB-PM01 Soil Moisture Sensor (product_id `jabotj1z`).
- Smartlife Plant Sensor SGS01 (product_id `gvygg3m8`).

Entities: temperature, humidity, battery state, battery percentage.
