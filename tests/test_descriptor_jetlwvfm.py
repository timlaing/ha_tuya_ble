"""Verified Battery BLE Roller datapoints from issue #39."""

from custom_components.tuya_ble.device_registry import (
    get_entity_descriptors,
    get_registry,
)


def test_battery_ble_roller_descriptor() -> None:
    """The roller exposes the confirmed cover and diagnostic datapoints."""
    product = get_registry().get("cl", "jetlwvfm")
    assert product is not None
    cover = get_entity_descriptors("cl", "jetlwvfm", "cover")
    assert len(cover) == 1
    assert cover[0].extra["state_dp_id"] == 1
    assert cover[0].extra["position_set_dp_id"] == 2
    assert cover[0].extra["position_dp_id"] == 3
    assert cover[0].extra["battery_dp_id"] == 13

    sensors = get_entity_descriptors("cl", "jetlwvfm", "sensor")
    assert [sensor.dp_id for sensor in sensors] == [13, 10]
    assert [sensor.unit for sensor in sensors] == ["%", "ms"]
    assert [sensor.device_class for sensor in sensors] == ["battery", "duration"]
    assert all(sensor.entity_category == "diagnostic" for sensor in sensors)
