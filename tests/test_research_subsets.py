"""Public setup/report regressions for the issue 181 read-only subsets."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.tuya_ble import binary_sensor, light, sensor
from custom_components.tuya_ble.const import DOMAIN
from custom_components.tuya_ble.coordinator import TuyaBLECoordinator
from custom_components.tuya_ble.device_registry import get_mapped_dp_ids, get_registry
from custom_components.tuya_ble.entity import get_device_product_info
from custom_components.tuya_ble.tuya_ble import (
    TuyaBLEDataPointType as Type,
)
from custom_components.tuya_ble.tuya_ble import (
    TuyaBLEDevice,
)
from tests.conftest import add_dp, make_credentials, make_device
from tests.test_setup_entries import PLATFORMS

PRODUCTS = [
    ("sh", "v1jqz5cy", {1, 2, 7, 10, 11, 101, 102, 117}),
    ("sfkzq", "smd9kj1n", {1, 2, 42, 43, 101, 102, 103, 104}),
    ("dj", "e2kdgdsu", {101, 102}),
]


def _device(category: str, product_id: str) -> tuple[TuyaBLEDevice, AsyncMock]:
    device = make_device()
    device.initialize_with_credentials(
        make_credentials(category=category, product_id=product_id)
    )
    # Read-only devices must never write; stub the BLE write path so any
    # accidental command is both harmless and observable via assert_not_awaited.
    send_datapoints = AsyncMock()
    device.send_datapoints = send_datapoints  # type: ignore[method-assign]
    return device, send_datapoints


async def _setup(
    hass: HomeAssistant, device: TuyaBLEDevice, platform: Any
) -> tuple[list[Any], TuyaBLECoordinator]:
    entry = MockConfigEntry(domain=DOMAIN)
    coordinator = TuyaBLECoordinator(hass, device)
    hass.data[DOMAIN] = {
        entry.entry_id: SimpleNamespace(
            device=device,
            coordinator=coordinator,
            product=get_device_product_info(device),
        )
    }
    added = MagicMock()
    await platform.async_setup_entry(hass, entry, added)
    return added.call_args.args[0], coordinator


@pytest.mark.parametrize(("category", "product_id", "expected"), PRODUCTS)
async def test_only_verified_report_ids_and_no_write_entities(
    hass: HomeAssistant, category: str, product_id: str, expected: set[int]
) -> None:
    """Even unrelated live DPs cannot expose guessed writes or category defaults."""
    device, _ = _device(category, product_id)
    assert get_mapped_dp_ids(category, product_id) == expected
    descriptor = get_registry().get(category, product_id)
    assert descriptor is not None
    for dp_id in range(1, 119):
        add_dp(device, dp_id, Type.DT_VALUE, 1)
    for name, (platform, _) in PLATFORMS.items():
        if name not in {"sensor", "binary_sensor"}:
            entities, _ = await _setup(hass, device, platform)
            assert entities == []
    for name in ("sensor", "binary_sensor"):
        assert all(not d.force_add for d in descriptor.get(name))


@pytest.mark.parametrize(("category", "product_id", "_expected"), PRODUCTS)
async def test_no_phantom_report_entities(
    hass: HomeAssistant, category: str, product_id: str, _expected: set[int]
) -> None:
    """Absent/wrong-type reports add no product sensors (RSSI is independent)."""
    device, _ = _device(category, product_id)
    for platform in (sensor, binary_sensor):
        entities, _ = await _setup(hass, device, platform)
        assert all(e.entity_description.key == "signal_strength" for e in entities)
    for dp_id in get_mapped_dp_ids(category, product_id):
        add_dp(device, dp_id, Type.DT_STRING, "unknown")
    for platform in (sensor, binary_sensor):
        entities, _ = await _setup(hass, device, platform)
        assert all(e.entity_description.key == "signal_strength" for e in entities)


@pytest.mark.parametrize(
    ("category", "product_id", "dp_id", "wire_type", "raw", "expected", "unit"),
    [
        ("sh", "v1jqz5cy", 1, Type.DT_VALUE, 123, 123, "ppm"),
        ("sh", "v1jqz5cy", 2, Type.DT_VALUE, -25, -2.5, "°C"),
        ("sh", "v1jqz5cy", 7, Type.DT_VALUE, 87, 87, "%"),
        ("sh", "v1jqz5cy", 10, Type.DT_VALUE, 72, 7.2, None),
        ("sh", "v1jqz5cy", 11, Type.DT_VALUE, 1234, 1234, "µS/cm"),
        ("sh", "v1jqz5cy", 101, Type.DT_VALUE, -35, -35, "mV"),
        ("sh", "v1jqz5cy", 102, Type.DT_VALUE, 12, 1.2, "mg/L"),
        ("sh", "v1jqz5cy", 117, Type.DT_VALUE, 345, 345, "ppm"),
        ("sfkzq", "smd9kj1n", 42, Type.DT_VALUE, 2, 2, "h"),
        ("sfkzq", "smd9kj1n", 101, Type.DT_VALUE, 60, 60, "s"),
        ("sfkzq", "smd9kj1n", 102, Type.DT_VALUE, 61, 61, "s"),
        ("sfkzq", "smd9kj1n", 103, Type.DT_VALUE, 62, 62, "s"),
        ("sfkzq", "smd9kj1n", 104, Type.DT_VALUE, 63, 63, "s"),
        ("dj", "e2kdgdsu", 102, Type.DT_ENUM, 255, 255, None),
    ],
)
async def test_numeric_report_read_paths(
    hass: HomeAssistant,
    category: str,
    product_id: str,
    dp_id: int,
    wire_type: Type,
    raw: int,
    expected: float,
    unit: str | None,
) -> None:
    """Known scales are applied; unknown enum values remain unlabeled numbers."""
    device, send_datapoints = _device(category, product_id)
    add_dp(device, dp_id, wire_type, raw)
    entities, coordinator = await _setup(hass, device, sensor)
    entity = next(e for e in entities if e.entity_description.key == str(dp_id))
    with patch.object(sensor.TuyaBLESensor, "async_write_ha_state"):
        await entity.async_added_to_hass()
        coordinator.async_set_updated_data({})
        assert entity.native_value == pytest.approx(expected)
        assert entity.native_unit_of_measurement == unit
        assert entity.entity_description.options is None
        for bad_type, bad_value in (
            (Type.DT_STRING, "unknown"),
            (wire_type, True),
            (wire_type, b"\x01"),
        ):
            add_dp(device, dp_id, bad_type, bad_value)
            coordinator.async_set_updated_data({})
            assert entity.native_value is None
        add_dp(device, dp_id, wire_type, raw)
        coordinator.async_set_updated_data({})
        assert entity.native_value == pytest.approx(expected)

    send_datapoints.assert_not_awaited()


@pytest.mark.parametrize(
    ("category", "product_id", "dp_id"),
    [
        ("sfkzq", "smd9kj1n", 1),
        ("sfkzq", "smd9kj1n", 2),
        ("sfkzq", "smd9kj1n", 43),
        ("dj", "e2kdgdsu", 101),
    ],
)
async def test_boolean_report_read_paths(
    hass: HomeAssistant, category: str, product_id: str, dp_id: int
) -> None:
    """BOOL reports have unknown state on type/value mismatch, never writes."""
    device, send_datapoints = _device(category, product_id)
    add_dp(device, dp_id, Type.DT_BOOL, True)
    entities, coordinator = await _setup(hass, device, binary_sensor)
    entity = next(e for e in entities if e.entity_description.key == str(dp_id))
    with patch.object(binary_sensor.TuyaBLEBinarySensor, "async_write_ha_state"):
        await entity.async_added_to_hass()
        for dp_type, value, expected in (
            (Type.DT_BOOL, True, True),
            (Type.DT_BOOL, False, False),
            (Type.DT_VALUE, 1, None),
            (Type.DT_BOOL, 1, None),
            (Type.DT_STRING, "unknown", None),
        ):
            add_dp(device, dp_id, dp_type, value)
            coordinator.async_set_updated_data({})
            assert entity.is_on is expected
    send_datapoints.assert_not_awaited()


async def test_absent_reports_stay_unknown(hass: HomeAssistant) -> None:
    """Forced direct construction also rejects absent or locally created values."""
    device, _ = _device("dj", "e2kdgdsu")
    coordinator = TuyaBLECoordinator(hass, device)
    product = get_device_product_info(device)
    numeric = sensor.TuyaBLESensor(
        hass, coordinator, device, product, sensor.get_mapping_by_device(device)[0]
    )
    boolean = binary_sensor.TuyaBLEBinarySensor(
        hass,
        coordinator,
        device,
        product,
        binary_sensor.get_mapping_by_device(device)[0],
    )
    with (
        patch.object(sensor.TuyaBLESensor, "async_write_ha_state"),
        patch.object(binary_sensor.TuyaBLEBinarySensor, "async_write_ha_state"),
    ):
        await numeric.async_added_to_hass()
        await boolean.async_added_to_hass()
        coordinator.async_set_updated_data({})
        assert numeric.native_value is None
        assert boolean.is_on is None
        device.datapoints.get_or_create(102, Type.DT_ENUM, 1)
        device.datapoints.get_or_create(101, Type.DT_BOOL, True)
        coordinator.async_set_updated_data({})
        assert numeric.native_value is None
        assert boolean.is_on is None


def test_unrelated_light_defaults_and_deferred_mower_unchanged() -> None:
    """The empty override affects only the aquarium, not existing generic lights."""
    assert light.get_mapping_by_device(_device("dj", "unknown")[0])
    assert not light.get_mapping_by_device(_device("dj", "e2kdgdsu")[0])
    assert not light.get_mapping_by_device(_device("xdd", "unknown")[0])
    assert get_registry().get("gcj", "7yr5iwga") is None
    assert not get_mapped_dp_ids("gcj", "7yr5iwga")


async def test_generic_light_reports_still_update(hass: HomeAssistant) -> None:
    """Empty product overrides do not alter unrelated generic report handling."""
    device, _ = _device("dj", "unknown")
    coordinator = TuyaBLECoordinator(hass, device)
    entity = light.TuyaBLELight(
        hass,
        coordinator,
        device,
        get_device_product_info(device),
        light.get_mapping_by_device(device)[0],
    )
    with patch.object(light.TuyaBLELight, "async_write_ha_state") as write_state:
        await entity.async_added_to_hass()
        add_dp(device, 21, Type.DT_ENUM, 1)
        add_dp(device, 22, Type.DT_VALUE, 1000)
        coordinator.async_set_updated_data({})
        assert entity.brightness == 255
        write_state.assert_called()
