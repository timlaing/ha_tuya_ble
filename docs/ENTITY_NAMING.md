# Entity labels and compatibility

Entity labels use sentence case, retaining proper nouns and abbreviations such as
Bluetooth and CO2. Product names are device metadata and are not changed.

The #180 audit covers the descriptors shipped with this change and their English
labels in `strings.json` and `translations/en.json`. Fingerbot Touch (`kg` /
`bs3ubslo`) settings now show their channel number after the label, for example
“Touch duration 1” instead of “1-04 Touch duration”. Data points, options, handlers,
translation keys and entity keys are unchanged. Non-English translations are
unchanged.

## Upstream proposal disposition

- [PR 336](https://github.com/ha-tuya-ble/ha_tuya_ble/pull/336) by @rairulyle:
  adopted sentence-case fixes where the labels exist here. Our mappings come from
  YAML descriptors, not the upstream platform-specific mapping tables. Many
  upstream labels are already sentence case here or are not shipped.
- [PR 335](https://github.com/ha-tuya-ble/ha_tuya_ble/pull/335) by @rairulyle:
  no ID-renaming scheme is adopted. Entity-ID generation and unique-ID resolution
  remain unchanged, including legacy aliases. Our base entity already leaves
  entity-ID assignment to Home Assistant. Existing registry IDs and user-assigned
  names take precedence; this change performs no registry or config-entry migration.
  The proposed dynamic primary-lock naming change is not ported: our locks use
  descriptor descriptions rather than an upstream hard-coded product name.
  Changing their naming behavior is outside this bounded string cleanup.

New devices in outstanding PRs will receive a small naming/English-string audit
after those PRs merge. This cleanup does not depend on them and does not add their
translations or descriptors. The supported-device list is unchanged.

## Acceptance on Home Assistant

Automated tests use the shipped descriptors and a mocked BLE device. They check
mapping keys, unique IDs, readable labels, registry name overrides, reloads,
unavailability and number-service validation; JSON checks reject duplicate keys
and preserve all exception messages in one `exceptions` object.

No physical device validation has been performed for this label-only change.
On an existing installation:

1. Record a Fingerbot Touch entity ID and give a setting a custom name.
2. Update/reload the integration without removing the device. Confirm IDs,
   automations and the custom name are unchanged; unnamed settings show the new
   channel labels in English.
3. With the device out of range, confirm entities remain unavailable through a
   reload, retaining their IDs and names.
4. Reconnect and verify setting a touch duration still affects the same channel.
   Try an out-of-range value: Home Assistant should reject it without a BLE write.
5. Check an existing lock/thermostat/Fingerbot page for sentence-case labels.
   User-assigned entity and device names should remain untouched.
