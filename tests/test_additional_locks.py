"""Issue 179: shipping descriptors never borrow an unverified unlock path."""

# pylint: disable=protected-access
from __future__ import annotations

from dataclasses import replace
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tuya_ble import (
    binary_sensor,
    button,
    lock,
    number,
    select,
    sensor,
    switch,
)
from custom_components.tuya_ble.const import (
    CONF_CATEGORY,
    CONF_PRODUCT_ID,
    CONF_UUID,
    DOMAIN,
)
from custom_components.tuya_ble.coordinator import TuyaBLECoordinator
from custom_components.tuya_ble.device_registry import get_mapped_dp_ids, get_registry
from custom_components.tuya_ble.diagnostics import async_get_config_entry_diagnostics
from custom_components.tuya_ble.entity import get_device_product_info
from custom_components.tuya_ble.tuya_ble import (
    TuyaBLEDataPoint,
    TuyaBLEDataPointType,
    TuyaBLEDevice,
)
from tests.conftest import add_dp, make_credentials, make_device

PRODUCTS = {
    "ms": (
        "7a4xvbtt",
        "6fibxtph",
        "99gv5nmz",
        "kpn4zaf7",
        "wgv4haro",
        "0qxp5u7s",
        "uyf1ewof",
    ),
    "jtmspro": (
        "y2yaegze",
        "qcrilcpr",
        "hc7n0urm",
        "kholoaew",
        "pyawczjj",
        "yfqp0shy",
        "qicggi0m",
    ),
}
MAPPED = {
    "0qxp5u7s": {6, 8, 12, 19, 33, 46, 47},
    "kholoaew": {8, 13, 28, 31, 33, 46, 47},
    "uyf1ewof": {8, 12, 19, 22, 33, 47},
    "y2yaegze": {8, 15, 19, 33, 46, 47, 62, 71},
    "qcrilcpr": {8},
}
PAIRS = [(category, pid) for category, ids in PRODUCTS.items() for pid in ids]


def _device(category: str, product_id: str) -> TuyaBLEDevice:
    device = make_device()
    device.initialize_with_credentials(
        make_credentials(category=category, product_id=product_id)
    )
    return device


def _datapoint(device: TuyaBLEDevice, dp_id: int) -> TuyaBLEDataPoint:
    datapoint = device.datapoints[dp_id]
    assert datapoint is not None
    return datapoint


def _record_writes(device: TuyaBLEDevice, monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    send = AsyncMock()
    monkeypatch.setattr(device, "send_datapoints", send)
    return send


@pytest.mark.parametrize(("category", "product_id"), PAIRS)
def test_registry_and_platform_safety(category: str, product_id: str) -> None:
    """Explicit empty lock lists suppress the unsafe ms category default."""
    descriptor = get_registry().get(category, product_id)
    assert descriptor is not None
    assert descriptor.device_name
    assert descriptor.manufacturer != "Example Product"
    assert get_mapped_dp_ids(category, product_id) == MAPPED.get(product_id, set())
    device = _device(category, product_id)
    assert bool(lock.get_mapping_by_device(device)) == (
        product_id in {"0qxp5u7s", "y2yaegze"}
    )
    assert not number.get_mapping_by_device(device)
    if product_id not in MAPPED:
        for platform in (sensor, binary_sensor, button, switch, select):
            assert not platform.get_mapping_by_device(device)
    if product_id not in {"0qxp5u7s", "kholoaew"}:
        for platform in (button, switch, select):
            assert not platform.get_mapping_by_device(device)


@pytest.mark.parametrize(
    ("category", "product_id"),
    [
        ("ms", "0qxp5u7s"),
        ("ms", "uyf1ewof"),
        ("jtmspro", "kholoaew"),
        ("jtmspro", "y2yaegze"),
        ("jtmspro", "qcrilcpr"),
    ],
)
@pytest.mark.parametrize("value", [0, 50, 100])
async def test_battery_identity_scaling(
    hass: HomeAssistant, category: str, product_id: str, value: int
) -> None:
    """Battery percentage is unscaled and unknown before a report."""
    device = _device(category, product_id)
    mapping = next(m for m in sensor.get_mapping_by_device(device) if m.dp_id == 8)
    assert mapping.coefficient == 1
    assert mapping.description.native_unit_of_measurement == "%"
    # Sensors decode the wire's reported type, not an inert declared type.
    assert mapping.dp_type is None
    coordinator = TuyaBLECoordinator(hass, device)
    entity = sensor.TuyaBLESensor(
        hass, coordinator, device, get_device_product_info(device), mapping
    )
    with patch.object(entity, "async_write_ha_state"):
        await entity.async_added_to_hass()
        assert entity.native_value is None
        add_dp(device, 8, TuyaBLEDataPointType.DT_VALUE, value)
        coordinator.async_set_updated_data({})
        assert entity.native_value == value


async def test_pulido_commands_and_reported_state(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Use inherited handlers; commands do not manufacture a bolt report."""
    device = _device("ms", "0qxp5u7s")
    send = _record_writes(device, monkeypatch)
    coordinator = TuyaBLECoordinator(hass, device)
    entity = lock.TuyaBLELock(
        hass,
        coordinator,
        device,
        get_device_product_info(device),
        lock.get_mapping_by_device(device)[0],
    )
    assert entity.is_locked is None
    await entity.async_open()
    await entity.async_open()
    assert send.call_args_list[0].args == ([6],)
    assert send.call_args_list[1].args == ([6],)
    assert device.datapoints[47] is None
    assert entity.is_locked is None
    await entity.async_lock()
    assert send.call_args.args == ([46],)
    assert _datapoint(device, 46).value is True
    assert _datapoint(device, 46).reported_value is None
    assert entity.is_locked is None
    await entity.async_unlock()
    assert send.call_args.args == ([33],)
    assert _datapoint(device, 33).value is True
    assert _datapoint(device, 33).reported_value is None
    assert entity.is_locked is None
    for passage, bolt, expected in [
        (True, False, False),
        (False, False, True),
        (False, True, False),
    ]:
        add_dp(device, 33, TuyaBLEDataPointType.DT_BOOL, passage)
        add_dp(device, 47, TuyaBLEDataPointType.DT_BOOL, bolt)
        assert entity.is_locked is expected
    assert {m.dp_id for m in switch.get_mapping_by_device(device)} == {33}


async def test_pulido_pending_writes_preserve_last_reported_state(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Successful sends are not bolt reports or confirmation of passage mode."""
    device = _device("ms", "0qxp5u7s")
    _record_writes(device, monkeypatch)
    entity = lock.TuyaBLELock(
        hass,
        TuyaBLECoordinator(hass, device),
        device,
        get_device_product_info(device),
        lock.get_mapping_by_device(device)[0],
    )
    add_dp(device, 33, TuyaBLEDataPointType.DT_BOOL, False)
    add_dp(device, 47, TuyaBLEDataPointType.DT_BOOL, False)
    assert entity.is_locked is True
    await entity.async_unlock()
    assert _datapoint(device, 33).value is True
    assert _datapoint(device, 33).reported_value is False
    assert entity.is_locked is True

    add_dp(device, 33, TuyaBLEDataPointType.DT_BOOL, True)
    assert entity.is_locked is False
    await entity.async_lock()
    assert entity.is_locked is False
    add_dp(device, 33, TuyaBLEDataPointType.DT_BOOL, False)
    assert entity.is_locked is True

    await entity.async_open()
    assert entity.is_locked is True
    assert _datapoint(device, 6).reported_value is None
    add_dp(device, 47, TuyaBLEDataPointType.DT_BOOL, True)
    assert entity.is_locked is False
    add_dp(device, 47, TuyaBLEDataPointType.DT_BOOL, False)
    assert entity.is_locked is True


async def test_h13_lock_only_button(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tested manual-lock trigger is a button, never an unlock switch."""
    device = _device("jtmspro", "kholoaew")
    send = _record_writes(device, monkeypatch)
    entity = button.TuyaBLEButton(
        hass,
        TuyaBLECoordinator(hass, device),
        device,
        get_device_product_info(device),
        button.get_mapping_by_device(device)[0],
    )
    await entity.async_press()
    await entity.async_press()
    assert send.call_count == 2
    assert send.call_args.args == ([46],)
    assert _datapoint(device, 46).value is True
    assert _datapoint(device, 46).dp_type == TuyaBLEDataPointType.DT_BOOL
    assert device.datapoints[71] is None
    assert {m.dp_id for m in switch.get_mapping_by_device(device)} == {33}


@pytest.mark.parametrize(
    ("dp_id", "options"),
    [
        (31, ["mute", "low", "normal", "high"]),
        (28, ["chinese_simplified", "english"]),
    ],
)
async def test_h13_verified_select_indices(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, dp_id: int, options: list[str]
) -> None:
    """Source-tested BLE enum order; invalid options never write."""
    device = _device("jtmspro", "kholoaew")
    send = _record_writes(device, monkeypatch)
    mapping = next(m for m in select.get_mapping_by_device(device) if m.dp_id == dp_id)
    entity = select.TuyaBLESelect(
        hass,
        TuyaBLECoordinator(hass, device),
        device,
        get_device_product_info(device),
        mapping,
    )
    entity.hass = hass
    assert entity.options == options
    for index, option in enumerate(options):
        add_dp(device, dp_id, TuyaBLEDataPointType.DT_ENUM, index)
        assert entity.current_option == option
        entity.select_option(option)
        await hass.async_block_till_done()
        assert send.call_args.args == ([dp_id],)
        assert _datapoint(device, dp_id).value == index
        assert _datapoint(device, dp_id).dp_type == TuyaBLEDataPointType.DT_ENUM
    send.reset_mock()
    entity.select_option("invalid")
    await hass.async_block_till_done()
    send.assert_not_called()


async def test_h13_automatic_lock_setting(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Automatic locking is a separate BOOL setting, not an unlock command."""
    device = _device("jtmspro", "kholoaew")
    send = _record_writes(device, monkeypatch)
    entity = switch.TuyaBLESwitch(
        hass,
        TuyaBLECoordinator(hass, device),
        device,
        get_device_product_info(device),
        switch.get_mapping_by_device(device)[0],
    )
    entity.hass = hass
    entity.turn_on()
    await hass.async_block_till_done()
    assert _datapoint(device, 33).value is True
    entity.turn_off()
    await hass.async_block_till_done()
    assert _datapoint(device, 33).value is False
    assert _datapoint(device, 33).dp_type == TuyaBLEDataPointType.DT_BOOL
    assert send.call_count == 2
    assert device.datapoints[46] is None
    assert device.datapoints[71] is None


@pytest.mark.parametrize(
    ("category", "product_id", "dp_ids"),
    [
        ("ms", "uyf1ewof", {22, 33, 47}),
        ("jtmspro", "kholoaew", {47}),
        ("jtmspro", "y2yaegze", {33}),
    ],
)
async def test_read_only_boolean_diagnostics(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    category: str,
    product_id: str,
    dp_ids: set[int],
) -> None:
    """Reported diagnostics create no commands or physical lock state."""
    device = _device(category, product_id)
    send = _record_writes(device, monkeypatch)
    coordinator = TuyaBLECoordinator(hass, device)
    mappings = binary_sensor.get_mapping_by_device(device)
    assert {m.dp_id for m in mappings} == dp_ids
    entities = [
        binary_sensor.TuyaBLEBinarySensor(
            hass, coordinator, device, get_device_product_info(device), mapping
        )
        for mapping in mappings
    ]
    with patch.object(binary_sensor.TuyaBLEBinarySensor, "async_write_ha_state"):
        for entity in entities:
            await entity.async_added_to_hass()
        for value in (True, False):
            for dp_id in dp_ids:
                add_dp(device, dp_id, TuyaBLEDataPointType.DT_BOOL, value)
            coordinator.async_set_updated_data({})
            assert all(entity.is_on is value for entity in entities)
    send.assert_not_called()


def test_existing_category_default_unchanged() -> None:
    """Existing identifiers and inherited lock mappings remain intact."""
    product = get_registry().get("ms", "bvclwu9b")
    assert product is not None
    assert product.get("lock")[0].dp_id == 47
    assert lock.get_mapping_by_device(_device("ms", "gumrixyt"))[0].dp_id == 47


async def test_lock_setup_requires_report_when_not_forced(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Public setup skips an unreported optional lock, then adds it on report."""
    device = _device("ms", "0qxp5u7s")
    mapping = replace(lock.get_mapping_by_device(device)[0], force_add=False)
    monkeypatch.setattr(lock, "get_mapping_by_device", lambda _device: [mapping])
    entry = MockConfigEntry(domain=DOMAIN)
    hass.data[DOMAIN] = {
        entry.entry_id: SimpleNamespace(
            device=device,
            coordinator=TuyaBLECoordinator(hass, device),
            product=get_device_product_info(device),
        )
    }
    add_entities = Mock()
    await lock.async_setup_entry(hass, entry, add_entities)
    add_entities.assert_called_once_with([])
    add_dp(device, 47, TuyaBLEDataPointType.DT_BOOL, False)
    add_entities.reset_mock()
    await lock.async_setup_entry(hass, entry, add_entities)
    (entity,) = add_entities.call_args.args[0]
    # The bolt alone cannot call the lock closed; passage still unreported.
    assert entity.is_locked is None
    add_dp(device, 33, TuyaBLEDataPointType.DT_BOOL, False)
    assert entity.is_locked is True


@pytest.mark.parametrize("product_id", ["kholoaew", "qcrilcpr"])
async def test_unmapped_unlock_check_reports_remain_private(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture, product_id: str
) -> None:
    """A known check DP is private even without an exposed unlock command."""
    device = _device("jtmspro", product_id)
    product = get_device_product_info(device)
    assert product.sensitive_dp_ids == {71}
    assert 71 not in get_mapped_dp_ids("jtmspro", product_id)
    device.datapoints.sensitive_ids = product.sensitive_dp_ids
    command = b"synthetic-check-payload"
    with caplog.at_level(logging.DEBUG):
        device.datapoints.update_from_device(
            71, 0.0, 0, TuyaBLEDataPointType.DT_RAW, b"", b""
        )
        device.datapoints.update_from_device(
            71, 1.0, 0, TuyaBLEDataPointType.DT_RAW, command, command
        )
    assert command.decode() not in caplog.text
    assert "<redacted>" in caplog.text

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CATEGORY: "jtmspro",
            CONF_PRODUCT_ID: product_id,
            CONF_UUID: "synthetic-device",
        },
    )
    entry.add_to_hass(hass)
    hass.data[DOMAIN] = {entry.entry_id: SimpleNamespace(device=device)}
    payload = await async_get_config_entry_diagnostics(hass, entry)
    assert payload["live"]["71"]["value"] == "**REDACTED**"
    assert payload["live"]["71"]["raw"] == "**REDACTED**"
    assert command.decode() not in repr(payload)
    assert command.hex() not in repr(payload)
