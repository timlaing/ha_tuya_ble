# Authenticated cloud-identity fallback

<!-- cspell:ignore Yoolax -->

During device setup, exact cloud/Bluetooth UUID matching remains the preferred
path. If a cloud-cache refresh still cannot match a **bound** BLE advertisement,
the integration tries a small, deterministic set of unconfigured cloud records.
The Yoolax YL23 can advertise an identity different from the cloud UUID.

The fallback currently supports classic A201 protocol 2/3 advertisements with a
known local datapoint mapping. Other protocols do not have a proven read-only
status handler in this integration and are not probed.

Each candidate is tested in a new, bounded Bluetooth session using its cloud
local key and device ID. The pairing request uses the **advertised** UUID first.
Only if the device explicitly rejects that pairing UUID do we start a fresh
session with the cloud UUID. A transport failure or a timeout is inconclusive:
it is not evidence that trying another UUID or another candidate will help.

Proof requires all three steps: a decrypted device-info response, an accepted
pair/authentication response and a status response followed by at least one
mapped, decrypted datapoint. A status acknowledgement without a datapoint is
not proof. No motor datapoint write, reset, unbind or OTA command is sent while
probing. We never pair unbound devices, to avoid modifying their binding.

The first authenticated match is used. Existing Tuya BLE config entries are
excluded from the candidate list (but standard cloud Tuya entries are not).
Successful claims are temporarily reserved while Home Assistant creates the
entry. The BLE pairing UUID is persisted separately from the original cloud UUID
when they differ, so later offline BLE sessions use the proven identity.

This is experimental until verified on a physical YL23 and a normal exact-UUID
device. Do not interpret a timeout as a definitive wrong-key result, and do not
publish diagnostic captures containing local keys or login tokens.
