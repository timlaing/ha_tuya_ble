"""Shipping primary-lock descriptors; BLE/cloud are mocked, not device proof."""

# pylint: disable=protected-access
from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable
from contextlib import ExitStack
from datetime import UTC, datetime
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.lock.const import LockEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tuya_ble import (
    async_setup_entry,
    binary_sensor,
    button,
    lock,
    number,
    select,
    sensor,
    switch,
)
from custom_components.tuya_ble.const import DOMAIN
from custom_components.tuya_ble.coordinator import TuyaBLECoordinator
from custom_components.tuya_ble.device_registry import DeviceRegistry, get_mapped_dp_ids
from custom_components.tuya_ble.devices import TuyaBLEData
from custom_components.tuya_ble.diagnostics import async_get_config_entry_diagnostics
from custom_components.tuya_ble.entity import get_product_info_by_ids
from custom_components.tuya_ble.tuya_ble import (
    TuyaBLEDataPoint,
    TuyaBLEDataPointType,
    TuyaBLEDeviceCredentials,
)
from tests.conftest import EntityDevice, FakeBLEManager, add_dp, make_status_device
from tests.test_init import _make_entry_data, _patch_deps

PRODUCTS = ("2hmqh0ty", "dappsdq4", "djrqe0q6", "tkbdvrzr")
# Entirely synthetic member/check/timestamp, never a device credential.
CHECK = base64.b64encode(
    b"\x00\x03\xff\xff87654321\x01\x01\x02\x03\x04\x00\x00"
).decode()
NOW = datetime.fromtimestamp(0x6AD8EB64, tz=UTC)
COMMAND = bytes.fromhex("ffff0003") + b"87654321" + bytes.fromhex("016ad8eb640001")


def device_for(product_id: str, check: str | None = None) -> EntityDevice:
    """Use the real BLE data-point collection with writes recorded, not sent."""
    credentials = TuyaBLEDeviceCredentials(
        uuid="u",
        local_key="k",
        device_id="dev",
        category="jtmspro",
        product_id=product_id,
        device_name="Lock",
        product_model="",
        product_name="Lock",
        ble_unlock_check=check,
    )
    device = make_status_device(FakeBLEManager(credentials), device_cls=EntityDevice)
    device._device_info = credentials
    return device


def datapoint(device: EntityDevice, dp_id: int) -> TuyaBLEDataPoint:
    """Require a data point to exist before inspecting its public value/type."""
    result = device.datapoints[dp_id]
    assert result is not None
    return result


@pytest.mark.parametrize("product_id", PRODUCTS)
def test_shipping_registry(product_id: str, tmp_path: Path) -> None:
    """Load/validate real YAML, including handler requirements and sensitive IDs."""
    # Validate the actual shipping file in isolation; unrelated products' YAML
    # need not be parsed four times under the per-test two-second timeout.
    filename = f"jtmspro_{product_id}.yaml"
    source = (
        Path(__file__).parents[1]
        / "custom_components/tuya_ble/device_descriptors"
        / filename
    )
    (tmp_path / filename).write_text(
        source.read_text(encoding="utf-8"), encoding="utf-8"
    )
    with patch("custom_components.tuya_ble.device_registry._DEVICES_DIR", tmp_path):
        registry = DeviceRegistry.load()
    descriptor = registry.get("jtmspro", product_id)
    assert descriptor is not None
    assert not descriptor.category_defaults
    assert descriptor.sensitive_dp_ids == {71}
    assert 71 in get_mapped_dp_ids("jtmspro", product_id)
    assert 24 not in get_mapped_dp_ids("jtmspro", product_id)
    for descriptors in descriptor.entities.values():
        for entity in descriptors:
            if entity.platform == "sensor":
                assert entity.dp_type is None
            else:
                assert entity.dp_type in (0, 1, 2, 4)
            assert entity.coefficient == 1
            for role in entity.handlers:
                assert callable(entity.resolved_handler(role))


@pytest.mark.parametrize("product_id", ("2hmqh0ty", "tkbdvrzr"))
def test_no_inferred_lock_state(product_id: str) -> None:
    """Neither button-only device inherits another lock's state or settings."""
    device = device_for(product_id)
    assert lock.get_mapping_by_device(device) == []
    assert binary_sensor.get_mapping_by_device(device) == []
    assert number.get_mapping_by_device(device) == []
    assert select.get_mapping_by_device(device) == []
    assert len(button.get_mapping_by_device(device)) == 1


async def test_c014s_lock_commands_and_reports(hass: HomeAssistant) -> None:
    """DP46 locks, empty RAW DP71 unlocks, state is only the DP47 report."""
    device = device_for("djrqe0q6")
    (mapping,) = lock.get_mapping_by_device(device)
    entity = lock.TuyaBLELock(
        hass,
        TuyaBLECoordinator(hass, device),
        device,
        get_product_info_by_ids("jtmspro", device.product_id),
        mapping,
    )
    assert entity.unique_id == "dev-lock"
    assert entity.supported_features == 0
    assert entity.is_locked is None
    await entity.async_lock()
    assert device.sent == [[46]]
    assert datapoint(device, 46).value is True
    assert entity.is_locked is None
    await entity.async_unlock()
    assert device.sent == [[46], [71]]
    assert datapoint(device, 71).dp_type is TuyaBLEDataPointType.DT_RAW
    assert datapoint(device, 71).value == b""
    assert datapoint(device, 46).value is True
    add_dp(device, 47, TuyaBLEDataPointType.DT_BOOL, True)
    assert entity.is_locked is False
    add_dp(device, 47, TuyaBLEDataPointType.DT_BOOL, False)
    assert entity.is_locked is True
    assert not button.get_mapping_by_device(device)


async def test_8052f66_passage_and_open(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Open does not manufacture temporary unlocked state or change passage."""
    freezer.move_to(NOW)
    device = device_for("dappsdq4", CHECK)
    (mapping,) = lock.get_mapping_by_device(device)
    entity = lock.TuyaBLELock(
        hass,
        TuyaBLECoordinator(hass, device),
        device,
        get_product_info_by_ids("jtmspro", device.product_id),
        mapping,
    )
    assert entity.unique_id == "dev-lock"
    assert entity.is_locked is None
    assert entity.supported_features == LockEntityFeature.OPEN
    await entity.async_lock()
    assert entity.is_locked is True
    await entity.async_open()
    assert datapoint(device, 71).value == COMMAND
    assert entity.is_locked is True
    await entity.async_unlock()
    assert datapoint(device, 33).value is False
    assert entity.is_locked is False
    assert device.sent == [[33], [71], [33]]


@pytest.mark.parametrize("product_id", ("2hmqh0ty", "tkbdvrzr"))
async def test_shipping_unlock_buttons(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, product_id: str
) -> None:
    """Repeated public presses await the exact product's unlock payload."""
    freezer.move_to(NOW)
    device = device_for(product_id, CHECK)
    (mapping,) = button.get_mapping_by_device(device)
    entity = button.TuyaBLEButton(
        hass,
        TuyaBLECoordinator(hass, device),
        device,
        get_product_info_by_ids("jtmspro", product_id),
        mapping,
    )
    assert entity.unique_id == "dev-bluetooth_unlock"
    await entity.async_press()
    await entity.async_press()
    assert device.sent == [[71], [71]]
    assert datapoint(device, 71).dp_type is TuyaBLEDataPointType.DT_RAW
    assert datapoint(device, 71).value == (COMMAND if product_id == "tkbdvrzr" else b"")


@pytest.mark.parametrize("product_id", ("dappsdq4", "tkbdvrzr"))
@pytest.mark.parametrize("check", (None, "not-base64!"))
async def test_bad_unlock_check_never_writes(
    hass: HomeAssistant, product_id: str, check: str | None
) -> None:
    """Real descriptors reject missing/invalid credentials before creating DP71."""
    device = device_for(product_id, check)
    coordinator = TuyaBLECoordinator(hass, device)
    product = get_product_info_by_ids("jtmspro", product_id)
    action: Callable[[], Awaitable[None]]
    if product_id == "dappsdq4":
        lock_entity = lock.TuyaBLELock(
            hass, coordinator, device, product, lock.get_mapping_by_device(device)[0]
        )
        action = lock_entity.async_open
    else:
        button_entity = button.TuyaBLEButton(
            hass, coordinator, device, product, button.get_mapping_by_device(device)[0]
        )
        action = button_entity.async_press
    with pytest.raises(HomeAssistantError) as error:
        await action()
    assert error.value.translation_key == (
        "ble_unlock_check_invalid" if check else "ble_unlock_check_missing"
    )
    assert not device.sent
    assert device.datapoints[71] is None


@pytest.mark.parametrize("product_id", ("dappsdq4", "djrqe0q6"))
async def test_auto_lock_delay(hass: HomeAssistant, product_id: str) -> None:
    """Shipping number identity, unit coefficient and range endpoints."""
    device = device_for(product_id)
    coordinator = TuyaBLECoordinator(hass, device)
    product = get_product_info_by_ids("jtmspro", product_id)
    (mapping,) = number.get_mapping_by_device(device)
    entity = number.TuyaBLENumber(hass, coordinator, device, product, mapping)
    entity.hass = hass
    bounds = (5, 20) if product_id == "dappsdq4" else (3, 600)
    assert entity.native_min_value == bounds[0]
    assert entity.native_max_value == bounds[1]
    assert entity.native_step == 1
    assert entity.native_unit_of_measurement == "s"
    for value in bounds:
        entity.set_native_value(value)
        await hass.async_block_till_done()
        assert datapoint(device, 36).value == value
        assert entity.native_value == value
    assert entity.unique_id == "dev-auto_lock_time"


@pytest.mark.parametrize("product_id", ("dappsdq4", "djrqe0q6"))
async def test_selects(hass: HomeAssistant, product_id: str) -> None:
    """Every evidenced option uses its source BLE index; invalid writes do nothing."""
    device = device_for(product_id)
    coordinator = TuyaBLECoordinator(hass, device)
    product = get_product_info_by_ids("jtmspro", product_id)
    for select_mapping in select.get_mapping_by_device(device):
        select_entity = select.TuyaBLESelect(
            hass, coordinator, device, product, select_mapping
        )
        select_entity.hass = hass
        for index, option in enumerate(select_entity.options):
            select_entity.select_option(option)
            await hass.async_block_till_done()
            assert datapoint(device, select_mapping.dp_id).value == index
            assert select_entity.current_option == option
        before = list(device.sent)
        select_entity.select_option("invalid")
        await hass.async_block_till_done()
        assert device.sent == before


@pytest.mark.parametrize("product_id", ("dappsdq4", "djrqe0q6"))
async def test_reported_sensors(hass: HomeAssistant, product_id: str) -> None:
    """Reads use incoming wire types, not an inert declared sensor dp_type."""
    device = device_for(product_id)
    coordinator = TuyaBLECoordinator(hass, device)
    product = get_product_info_by_ids("jtmspro", product_id)
    for sensor_mapping in sensor.get_mapping_by_device(device):
        sensor_entity = sensor.TuyaBLESensor(
            hass, coordinator, device, product, sensor_mapping
        )
        sensor_entity.hass = hass
        await sensor_entity.async_added_to_hass()
        value = 1 if sensor_mapping.dp_id == 21 else 42
        wire_type = (
            TuyaBLEDataPointType.DT_ENUM
            if sensor_mapping.dp_id == 21
            else TuyaBLEDataPointType.DT_VALUE
        )
        add_dp(device, sensor_mapping.dp_id, wire_type, value)
        coordinator.async_set_updated_data({})
        await hass.async_block_till_done()
        assert sensor_entity.unique_id == f"dev-{sensor_mapping.description.key}"
        assert sensor_entity.native_value == (
            "wrong_password" if sensor_mapping.dp_id == 21 else 42
        )
        if sensor_mapping.dp_id == 8:
            assert sensor_entity.native_unit_of_measurement == "%"
        if sensor_mapping.dp_id == 21:
            add_dp(device, 21, TuyaBLEDataPointType.DT_ENUM, 255)
            coordinator.async_set_updated_data({})
            await hass.async_block_till_done()
            assert sensor_entity.native_value is None
        await sensor_entity.async_will_remove_from_hass()


@pytest.mark.parametrize("product_id", PRODUCTS)
async def test_shipping_sensitive_diagnostics_and_logs(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture, product_id: str
) -> None:
    """Real descriptor DP71 redacts both reports and outgoing DP collection logs."""
    device = device_for(product_id, CHECK)
    product = get_product_info_by_ids("jtmspro", product_id)
    device.datapoints.sensitive_ids = product.sensitive_dp_ids
    with caplog.at_level(logging.DEBUG):
        add_dp(device, 71, TuyaBLEDataPointType.DT_RAW, COMMAND, COMMAND)
        await datapoint(device, 71).set_value(COMMAND)
        add_dp(
            device, 71, TuyaBLEDataPointType.DT_RAW, b"secret-report", b"secret-report"
        )
    assert "87654321" not in caplog.text
    assert "secret-report" not in caplog.text
    assert COMMAND.hex() not in caplog.text
    assert "<redacted>" in caplog.text
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "category": "jtmspro",
            "product_id": product_id,
            "device_id": "dev",
            "uuid": "u",
            "ble_unlock_check": CHECK,
        },
    )
    entry.add_to_hass(hass)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = TuyaBLEData(
        title="Lock",
        device=device,
        product=product,
        manager=MagicMock(),
        coordinator=TuyaBLECoordinator(hass, device),
    )
    payload = await async_get_config_entry_diagnostics(hass, entry)
    assert payload["live"]["71"]["value"] == "**REDACTED**"
    assert payload["live"]["71"]["raw"] == "**REDACTED**"
    assert CHECK not in repr(payload)
    assert "secret-report" not in repr(payload)


@pytest.mark.parametrize("product_id", PRODUCTS)
async def test_setup_applies_shipping_sensitive_ids(
    hass: HomeAssistant, product_id: str
) -> None:
    """Setup uses the shipping descriptor before any connection/report occurs."""
    data = {**_make_entry_data(), "category": "jtmspro", "product_id": product_id}
    entry = MockConfigEntry(domain=DOMAIN, data=data)
    entry.add_to_hass(hass)
    deps = _patch_deps(
        hass, product_info=get_product_info_by_ids("jtmspro", product_id)
    )
    try:
        with ExitStack() as stack:
            for patcher in deps["patches"]:
                stack.enter_context(patcher)
            assert await async_setup_entry(hass, entry)
    finally:
        for task in deps["background_tasks"]:
            task["target"].close()
    assert deps["device"].datapoints.sensitive_ids == {71}


@pytest.mark.parametrize("product_id", ("dappsdq4", "djrqe0q6"))
async def test_automatic_lock_switch(hass: HomeAssistant, product_id: str) -> None:
    """The source switch keeps its key and directly controls boolean DP33."""
    device = device_for(product_id)
    (mapping,) = switch.get_mapping_by_device(device)
    entity = switch.TuyaBLESwitch(
        hass,
        TuyaBLECoordinator(hass, device),
        device,
        get_product_info_by_ids("jtmspro", product_id),
        mapping,
    )
    entity.hass = hass
    assert entity.unique_id == "dev-automatic_lock"
    entity.turn_on()
    await hass.async_block_till_done()
    assert datapoint(device, 33).value is True
    entity.turn_off()
    await hass.async_block_till_done()
    assert datapoint(device, 33).value is False
    assert device.sent == [[33], [33]]


async def test_c014s_motor_diagnostic(hass: HomeAssistant) -> None:
    """The diagnostic has the source key and reads DP47 without writing it."""
    device = device_for("djrqe0q6")
    coordinator = TuyaBLECoordinator(hass, device)
    (mapping,) = binary_sensor.get_mapping_by_device(device)
    entity = binary_sensor.TuyaBLEBinarySensor(
        hass,
        coordinator,
        device,
        get_product_info_by_ids("jtmspro", device.product_id),
        mapping,
    )
    entity.hass = hass
    await entity.async_added_to_hass()
    assert entity.unique_id == "dev-lock_motor_state"
    assert entity.is_on is None
    for value in (True, False):
        add_dp(device, 47, TuyaBLEDataPointType.DT_BOOL, value)
        coordinator.async_set_updated_data({})
        await hass.async_block_till_done()
        assert entity.is_on is value
    assert not device.sent
    await entity.async_will_remove_from_hass()
