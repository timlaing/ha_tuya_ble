# Event descriptors

Event entities are driven by fresh reports, not by value changes. Declare the
wire type and each observed value explicitly; never infer an integer enum order
from the order of cloud strings:

```yaml
event:
  - dp_id: 1
    translation_key: event
    device_class: button
    dp_type: 3
    event_values:
      single_click: single_click
      double_click: double_click
      long_press: long_press
```

`dp_type` supports boolean (1), string (3) and enum (4). Keys must match that
type; values must be non-empty event names. The registry rejects `restore: true`.
`force_add` defaults to true. Product and category-default descriptors use the
same validation. Descriptors own all metadata and wire mappings.

For a lock whose captured schema confirms boolean DP 24, a future descriptor
can use `device_class: doorbell`, `dp_type: 1`, and
`event_values: {true: ring}`. False does not ring; repeated true reports do.
Doorbell events must use Home Assistant's standard `ring` event name.
No lock is enabled by this platform alone; actual mappings are deferred to #166.

The coordinator exposes only the current report batch during synchronous
listener dispatch and clears it afterwards. Event entities track report
revisions (including identical values), not `changed_by_device`. Local writes,
cached values, other data points and connection-only updates cannot create events.
Event values come from the device's reported snapshot, even if another listener
applies a local write during dispatch. Locally created data points start with no
reports and cannot consume the initial report baseline.
Subscriptions are removed through Home Assistant's entity lifecycle.

The first matching report after setup or physical disconnect establishes a silent
baseline. The protocol currently provides no verified status/live-event
discriminator. This prevents initial/reconnect snapshots from fabricating clicks,
but may discard the first physical click if firmware sends no initial status.
Captures of startup, status replies, repeated clicks and sleep/reconnect cycles
are needed to improve that limitation safely. The Arlec profiles are experimental:
only gateway string reports are verified, not direct BLE enum indices.
