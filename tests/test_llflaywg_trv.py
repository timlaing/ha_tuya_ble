"""Owner-correct llflaywg thermostat mapping and wire regression tests."""

# pylint: disable=protected-access

from __future__ import annotations

import asyncio
from pathlib import Path
from struct import pack
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.components.climate import ClimateEntityDescription
from homeassistant.components.climate.const import HVACAction, HVACMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry
import pytest
import yaml

from custom_components.tuya_ble import (
    climate,
    device_registry,
    number,
    select,
    sensor,
    switch,
)
from custom_components.tuya_ble.const import DOMAIN
from custom_components.tuya_ble.coordinator import TuyaBLECoordinator
from custom_components.tuya_ble.device_descriptors.handlers import thermostat
from custom_components.tuya_ble.device_registry import (
    DeviceEntities,
    DeviceRegistry,
    DeviceRegistryError,
    EntityDescriptor,
    get_mapped_dp_ids,
)
from custom_components.tuya_ble.entity import (
    TuyaBLEProductInfo,
    get_device_product_info,
)
from custom_components.tuya_ble.tuya_ble import (
    TuyaBLEDataPointType as DPType,
)
from custom_components.tuya_ble.tuya_ble import (
    TuyaBLEDevice,
)
from custom_components.tuya_ble.tuya_ble.const import TuyaBLECode
from tests.conftest import add_dp, build_context, connect, make_credentials
from tests.protocol_harness import ProtocolHarness, encrypt_payload, frame_packet0


def _context(
    hass: HomeAssistant,
) -> tuple[TuyaBLEDevice, TuyaBLECoordinator, TuyaBLEProductInfo]:
    device, coordinator, _ = build_context(hass)
    device.initialize_with_credentials(
        make_credentials(category="wkf", product_id="llflaywg")
    )
    return device, coordinator, get_device_product_info(device)


def _climate(
    hass: HomeAssistant,
    device: TuyaBLEDevice,
    coordinator: TuyaBLECoordinator,
    product: TuyaBLEProductInfo,
) -> climate.TuyaBLEClimate:
    (mapping,) = climate.get_mapping_by_device(device)
    entity = climate.TuyaBLEClimate(hass, coordinator, device, product, mapping)
    entity.hass = hass
    return entity


async def test_real_platform_setup_and_keys(hass: HomeAssistant) -> None:
    """Public setup creates the shipped mappings, preserving genuine legacy keys."""
    device, coordinator, product = _context(hass)
    entry = SimpleNamespace(entry_id="trv")
    hass.data[DOMAIN] = {
        entry.entry_id: SimpleNamespace(
            device=device, coordinator=coordinator, product=product
        )
    }
    expected = {
        climate: ["thermostatic_radiator_valve"],
        number: ["temp_correction", "eco_temp_deviation"],
        select: ["valve_mode", "led_brightness", "screen_orientation"],
        switch: ["window_check", "child_lock"],
        sensor: [
            "signal_strength",
            "battery",
            "work_state",
            "window_state",
            "child_lock_state",
            "valve_open_degree",
            "motor_thrust_state",
            "mfg_model",
            "fw_version_int",
            "lower_temperature_limit",
            "upper_temperature_limit",
        ],
    }
    for platform, keys in expected.items():
        entities: list[Any] = []
        await platform.async_setup_entry(hass, entry, entities.extend)
        assert [entity.entity_description.key for entity in entities] == keys
        assert [entity.unique_id for entity in entities] == [
            f"{device.device_id}-{key}" for key in keys
        ]
    assert get_mapped_dp_ids("wkf", "llflaywg") == {
        1,
        2,
        3,
        6,
        7,
        8,
        12,
        13,
        15,
        16,
        101,
        108,
        109,
        110,
        111,
        112,
        113,
        114,
        115,
    }


@pytest.mark.parametrize("reported", [False, True])
@pytest.mark.parametrize(
    ("mode", "value"), [(HVACMode.AUTO, 0), (HVACMode.HEAT, 1), (HVACMode.OFF, 2)]
)
async def test_mode_enum_writes(
    hass: HomeAssistant, reported: bool, mode: HVACMode, value: int
) -> None:
    """Modes write a one-byte ENUM both before and after the first BLE report."""
    device, coordinator, product = _context(hass)
    entity = _climate(hass, device, coordinator, product)
    if reported:
        add_dp(device, 1, DPType.DT_ENUM, 3)
    with patch.object(device, "send_datapoints", new_callable=AsyncMock) as send:
        await entity.async_set_hvac_mode(mode)
    send.assert_awaited_once_with([1])
    datapoint = device.datapoints[1]
    assert datapoint is not None
    assert datapoint.dp_type == DPType.DT_ENUM
    assert datapoint.get_value() == bytes([value])
    assert device.datapoints[101] is None


@pytest.mark.parametrize(
    ("value", "mode"),
    [
        (0, HVACMode.AUTO),
        (1, HVACMode.HEAT),
        (2, HVACMode.OFF),
        (3, None),
        (99, None),
        (-1, None),
    ],
)
async def test_modes_and_work_state_reports(
    hass: HomeAssistant, value: int, mode: HVACMode | None
) -> None:
    """Full-open is never mislabelled Heat, Dry or Fan-only."""
    device, coordinator, product = _context(hass)
    entity = _climate(hass, device, coordinator, product)
    assert entity.hvac_mode is None
    assert entity.hvac_action is None
    await entity.async_added_to_hass()
    for state, action in [(0, HVACAction.IDLE), (1, HVACAction.HEATING), (99, None)]:
        add_dp(device, 1, DPType.DT_ENUM, value)
        add_dp(device, 6, DPType.DT_ENUM, state)
        coordinator.async_set_updated_data({})
        assert entity.hvac_mode == mode
        assert entity.hvac_action == (
            HVACAction.OFF if mode == HVACMode.OFF else action
        )
    assert entity.hvac_modes == [HVACMode.AUTO, HVACMode.HEAT, HVACMode.OFF]


@pytest.mark.parametrize("reported", [False, True])
async def test_enum_presets(hass: HomeAssistant, reported: bool) -> None:
    """Comfort/eco have explicit wire values, never inferred from bool presets."""
    device, coordinator, product = _context(hass)
    entity = _climate(hass, device, coordinator, product)
    await entity.async_added_to_hass()
    coordinator.async_set_updated_data({})
    assert entity.preset_mode is None
    assert entity.preset_modes == ["comfort", "eco"]
    if reported:
        add_dp(device, 114, DPType.DT_ENUM, 99)
    with patch.object(device, "send_datapoints", new_callable=AsyncMock):
        for preset, value in [("comfort", 0), ("eco", 1)]:
            await entity.async_set_preset_mode(preset)
            datapoint = device.datapoints[114]
            assert datapoint is not None
            assert datapoint.dp_type == DPType.DT_ENUM
            assert datapoint.get_value() == bytes([value])
            coordinator.async_set_updated_data({})
            assert entity.preset_mode == preset
        add_dp(device, 114, DPType.DT_ENUM, 99)
        coordinator.async_set_updated_data({})
        assert entity.preset_mode is None
        with pytest.raises(ValueError, match="Unsupported preset"):
            await entity.async_set_preset_mode("away")


@pytest.mark.parametrize("temperature", [5.0, 20.5, 35.0])
async def test_target_scale_and_bounds(hass: HomeAssistant, temperature: float) -> None:
    """Software validates encoding only; hardware acceptance of DP2 is unverified."""
    device, coordinator, product = _context(hass)
    entity = _climate(hass, device, coordinator, product)
    assert (entity.min_temp, entity.max_temp, entity.target_temperature_step) == (
        5,
        35,
        0.5,
    )
    with patch.object(device, "send_datapoints", new_callable=AsyncMock) as send:
        await entity.async_set_temperature(temperature=temperature)
    send.assert_awaited_once_with([2])
    datapoint = device.datapoints[2]
    assert datapoint is not None
    assert datapoint.dp_type == DPType.DT_VALUE
    assert datapoint.get_value() == pack(">i", round(temperature * 10))


@pytest.mark.parametrize("command", ["mode", "preset", "temperature"])
async def test_climate_write_failure_is_propagated(
    hass: HomeAssistant, command: str
) -> None:
    """Awaited climate commands do not silently lose device write errors."""
    device, coordinator, product = _context(hass)
    entity = _climate(hass, device, coordinator, product)
    with patch.object(
        device,
        "send_datapoints",
        new_callable=AsyncMock,
        side_effect=RuntimeError("write failed"),
    ):
        if command == "mode":
            operation = entity.async_set_hvac_mode(HVACMode.HEAT)
        elif command == "preset":
            operation = entity.async_set_preset_mode("eco")
        else:
            operation = entity.async_set_temperature(temperature=20)
        with pytest.raises(RuntimeError, match="write failed"):
            await operation


@pytest.mark.parametrize(
    ("dp_id", "option", "value"),
    [
        (1, "auto", 0),
        (1, "manual", 1),
        (1, "off", 2),
        (1, "full_open", 3),
        (111, "high", 0),
        (111, "mid", 1),
        (111, "low", 2),
        (113, "up", 0),
        (113, "down", 2),
    ],
)
async def test_aux_selects(
    hass: HomeAssistant, dp_id: int, option: str, value: int
) -> None:
    """Every offered option uses its actual ENUM index, especially down = 2."""
    device, coordinator, product = _context(hass)
    mapping = next(
        item for item in select.get_mapping_by_device(device) if item.dp_id == dp_id
    )
    entity = select.TuyaBLESelect(hass, coordinator, device, product, mapping)
    entity.hass = hass
    with patch.object(device, "send_datapoints", new_callable=AsyncMock) as send:
        entity.select_option(option)
        await hass.async_block_till_done()
    send.assert_awaited_once_with([dp_id])
    datapoint = device.datapoints[dp_id]
    assert datapoint is not None
    assert datapoint.dp_type == DPType.DT_ENUM
    assert datapoint.get_value() == bytes([value])
    assert entity.current_option == option
    if dp_id == 113:
        assert entity.options == ["up", "down"]
        add_dp(device, 113, DPType.DT_ENUM, 1)
        assert entity.current_option == "1"
        entity.select_option("right")
        datapoint = device.datapoints[113]
        assert datapoint is not None
        assert datapoint.value == 1


@pytest.mark.parametrize("dp_id", [8, 12])
async def test_aux_switches(hass: HomeAssistant, dp_id: int) -> None:
    """Child lock is DP12, not the copied wk DP40."""
    device, coordinator, product = _context(hass)
    mapping = next(
        item for item in switch.get_mapping_by_device(device) if item.dp_id == dp_id
    )
    entity = switch.TuyaBLESwitch(hass, coordinator, device, product, mapping)
    entity.hass = hass
    with patch.object(device, "send_datapoints", new_callable=AsyncMock) as send:
        entity.turn_on()
        await hass.async_block_till_done()
        assert entity.is_on is True
        datapoint = device.datapoints[dp_id]
        assert datapoint is not None
        assert datapoint.dp_type == DPType.DT_BOOL
        entity.turn_off()
        await hass.async_block_till_done()
        assert entity.is_on is False
    assert send.await_args_list[0].args == ([dp_id],)
    assert send.await_count == 2


@pytest.mark.parametrize(
    ("dp_id", "limits", "values"),
    [
        (101, (-10, 10, 0.1), [-10.0, -0.1, 0.0, 10.0]),
        (115, (0.5, 5, 0.1), [0.5, 1.1, 5.0]),
    ],
)
async def test_aux_numbers(
    hass: HomeAssistant,
    dp_id: int,
    limits: tuple[float, float, float],
    values: list[float],
) -> None:
    """Calibration and eco deviation use signed tenths and their correct bounds."""
    device, coordinator, product = _context(hass)
    mapping = next(
        item for item in number.get_mapping_by_device(device) if item.dp_id == dp_id
    )
    entity = number.TuyaBLENumber(hass, coordinator, device, product, mapping)
    entity.hass = hass
    assert entity.native_min_value == limits[0]
    assert entity.native_max_value == limits[1]
    assert entity.native_step == limits[2]
    with patch.object(device, "send_datapoints", new_callable=AsyncMock):
        for value in values:
            entity.set_native_value(value)
            await hass.async_block_till_done()
            datapoint = device.datapoints[dp_id]
            assert datapoint is not None
            assert datapoint.dp_type == DPType.DT_VALUE
            assert datapoint.get_value() == pack(">i", round(value * 10))
            assert entity.native_value == pytest.approx(value)
        await connect(coordinator)
        if dp_id == 115:
            assert not entity.available
            for value in [0, 1, 2]:
                add_dp(device, 114, DPType.DT_ENUM, value)
                assert entity.available == (value == 1)
        await coordinator.async_shutdown()


async def test_all_read_only_diagnostics(hass: HomeAssistant) -> None:
    """Read every added diagnostic and verify uncertainty never creates writes."""
    device, coordinator, product = _context(hass)
    entities = [
        sensor.TuyaBLESensor(hass, coordinator, device, product, mapping)
        for mapping in sensor.get_mapping_by_device(device)
    ]
    reports: list[tuple[int, DPType, bytes | bool | int | str, str | float]] = [
        (13, DPType.DT_VALUE, 67, 67),
        (6, DPType.DT_ENUM, 0, "closed"),
        (7, DPType.DT_ENUM, 1, "opened"),
        (12, DPType.DT_BOOL, False, "disabled"),
        (108, DPType.DT_VALUE, 555, 55.5),
        (110, DPType.DT_ENUM, 1, "middle"),
        (109, DPType.DT_STRING, "bt3l_60x", "bt3l_60x"),
        (112, DPType.DT_VALUE, 1003, 1003),
        (15, DPType.DT_VALUE, 50, 5),
        (16, DPType.DT_VALUE, 300, 30),
    ]
    with patch.object(sensor.TuyaBLESensor, "async_write_ha_state"):
        for entity in entities:
            await entity.async_added_to_hass()
        coordinator.async_set_updated_data({})
        assert entities[3].native_value is None
        for dp_id, dp_type, value, _ in reports:
            add_dp(device, dp_id, dp_type, value)
        coordinator.async_set_updated_data({})
        assert [entity.native_value for entity in entities] == [
            report[3] for report in reports
        ]
        add_dp(device, 12, DPType.DT_BOOL, True)
        coordinator.async_set_updated_data({})
        assert entities[3].native_value == "enabled"
        add_dp(device, 12, DPType.DT_VALUE, 1)
        coordinator.async_set_updated_data({})
        assert entities[3].native_value is None
    writable = [
        item.dp_id
        for platform in (number, select, switch)
        for item in platform.get_mapping_by_device(device)
    ]
    assert set(writable) == {1, 8, 12, 101, 111, 113, 115}


async def test_real_ble_report_decoding(hass: HomeAssistant) -> None:
    """Decode the owner's report values and a signed room temperature on the wire."""
    harness = ProtocolHarness()
    await harness.register_notify()
    device = harness.device
    device._device_info = make_credentials(category="wkf", product_id="llflaywg")  # pylint: disable=protected-access
    coordinator = TuyaBLECoordinator(hass, device)
    entity = _climate(hass, device, coordinator, get_device_product_info(device))
    reports = [
        (1, DPType.DT_ENUM, b"\x01"),
        (2, DPType.DT_VALUE, pack(">i", 70)),
        (3, DPType.DT_VALUE, pack(">i", 255)),
        (6, DPType.DT_ENUM, b"\x00"),
        (7, DPType.DT_ENUM, b"\x00"),
        (8, DPType.DT_BOOL, b"\x00"),
        (12, DPType.DT_BOOL, b"\x00"),
        (13, DPType.DT_VALUE, pack(">i", 67)),
        (101, DPType.DT_VALUE, pack(">i", 0)),
        (110, DPType.DT_ENUM, b"\x01"),
        (111, DPType.DT_ENUM, b"\x00"),
    ]
    assert device._session_key is not None  # pylint: disable=protected-access
    with patch.object(climate.TuyaBLEClimate, "async_write_ha_state"):
        await entity.async_added_to_hass()
        for sequence, data in [
            (
                5,
                b"".join(
                    pack(">BBB", dp_id, dp_type.value, len(value)) + value
                    for dp_id, dp_type, value in reports
                ),
            ),
            (6, pack(">BBBi", 3, DPType.DT_VALUE.value, 4, -300)),
        ]:
            harness.notify(
                frame_packet0(
                    encrypt_payload(
                        device._session_key,
                        5,
                        sequence,
                        0,
                        TuyaBLECode.FUN_RECEIVE_DP,
                        data,
                    )
                )
            )  # pylint: disable=protected-access
            await asyncio.gather(*tuple(device._send_response_tasks))  # pylint: disable=protected-access
            coordinator.async_set_updated_data({})
            assert entity.current_temperature == (25.5 if sequence == 5 else -30)
            assert entity.target_temperature == 7
            assert entity.hvac_mode == HVACMode.HEAT
            assert entity.hvac_action == HVACAction.IDLE
    for dp_id, dp_type, value in reports:
        datapoint = device.datapoints[dp_id]
        assert datapoint is not None
        assert datapoint.dp_type == dp_type
        if dp_id != 3:
            assert datapoint.get_value() == value


@pytest.mark.parametrize("product_id", ["drlajpqc", "nhj2j7su", "zmachryv"])
async def test_wk_regression(hass: HomeAssistant, product_id: str) -> None:
    """Existing wk IDs, Boolean mode/preset writes and temperature DPs stay intact."""
    device, coordinator, _ = build_context(hass)
    device.initialize_with_credentials(
        make_credentials(category="wk", product_id=product_id)
    )
    if product_id == "zmachryv":
        assert not climate.get_mapping_by_device(device)
        return
    entity = _climate(hass, device, coordinator, get_device_product_info(device))
    assert entity.unique_id == f"{device.device_id}-thermostatic_radiator_valve"
    assert entity.hvac_modes == [HVACMode.OFF, HVACMode.HEAT]
    with patch.object(device, "send_datapoints", new_callable=AsyncMock):
        await entity.async_set_hvac_mode(HVACMode.HEAT)
        await entity.async_set_preset_mode("away")
        await hass.async_block_till_done()
    for dp_id in [101, 106]:
        datapoint = device.datapoints[dp_id]
        assert datapoint is not None
        assert datapoint.dp_type == DPType.DT_BOOL
        assert datapoint.value is True


@pytest.mark.parametrize(
    ("platform", "metadata"),
    [
        ("climate", {"hvac_mode_values": "bad"}),
        ("climate", {"hvac_mode_values": []}),
        ("climate", {"hvac_modes": ["heat"], "hvac_mode_values": [True]}),
        ("climate", {"hvac_modes": ["heat"], "hvac_mode_values": ["bad"]}),
        ("climate", {"hvac_modes": ["heat"], "hvac_mode_values": [-1]}),
        ("climate", {"hvac_modes": ["heat"], "hvac_mode_values": [256]}),
        ("climate", {"hvac_modes": ["heat", "off"], "hvac_mode_values": [1, 1]}),
        ("climate", {"hvac_mode_dp_type": 1}),
        ("climate", {"preset_enum_values": []}),
        ("climate", {"preset_enum_values": {}}),
        ("climate", {"preset_enum_values": {"eco": True}, "preset_enum_dp_id": 114}),
        ("climate", {"preset_enum_values": {"eco": 256}, "preset_enum_dp_id": 114}),
        (
            "climate",
            {"preset_enum_values": {"eco": 0, "comfort": 0}, "preset_enum_dp_id": 114},
        ),
        ("climate", {"preset_enum_values": {"eco": 1}}),
        ("climate", {"preset_enum_dp_id": 114}),
        ("climate", {"preset_enum_values": {"eco": 1}, "preset_enum_dp_id": -1}),
        ("climate", {"preset_enum_values": {"eco": 1}, "preset_enum_dp_id": True}),
        (
            "climate",
            {
                "preset_enum_values": {"eco": 1},
                "preset_enum_dp_id": 114,
                "preset_mode_dp_ids": {"away": 106},
            },
        ),
        ("select", {"options": ["up"], "enum_values": [0, 2]}),
        ("select", {"options": ["up"], "enum_values": [0], "values": ["0"]}),
        ("select", {"options": ["up"], "enum_values": [0], "dp_type": 3}),
    ],
)
def test_invalid_wire_metadata(
    tmp_path: Path, platform: str, metadata: dict[str, Any]
) -> None:
    """Public registry loading rejects ambiguous enum metadata."""
    descriptor = {
        "category": "test",
        "product_id": "test",
        "entities": {platform: [{"dp_id": 1, **metadata}]},
    }
    (tmp_path / "test.yaml").write_text(yaml.safe_dump(descriptor), encoding="utf-8")
    with (
        patch.object(device_registry, "_DEVICES_DIR", tmp_path),
        pytest.raises(DeviceRegistryError),
    ):
        DeviceRegistry.load()


@pytest.mark.parametrize(
    "descriptors",
    [None, [], [{"preset_enum_dp_id": 114}], [{"preset_enum_values": {"eco": 1}}]],
)
async def test_eco_gating_without_complete_metadata(
    hass: HomeAssistant, descriptors: list[dict[str, Any]] | None
) -> None:
    """An unknown product or incomplete preset metadata never enables a control."""
    device, coordinator, product = _context(hass)
    mapping = number.get_mapping_by_device(device)[1]
    entity = number.TuyaBLENumber(hass, coordinator, device, product, mapping)
    metadata_product = TuyaBLEProductInfo(
        entities=(
            DeviceEntities(
                category="test",
                product_id="test",
                entities={
                    "climate": [
                        EntityDescriptor(platform="climate", dp_id=0, extra=extra)
                        for extra in descriptors
                    ]
                },
            )
            if descriptors is not None
            else None
        )
    )
    assert not thermostat.in_eco_mode(entity, metadata_product)


async def test_explicit_mode_values_are_not_list_indices(hass: HomeAssistant) -> None:
    """Reusable metadata can map two offered HVAC modes to nonzero wire indices."""
    device, coordinator, product = _context(hass)
    mapping = climate.TuyaBLEClimateMapping(
        description=ClimateEntityDescription(key="test"),
        hvac_mode_dp_id=1,
        hvac_mode_dp_type=DPType.DT_ENUM,
        hvac_modes=[HVACMode.HEAT, HVACMode.OFF],
        hvac_mode_values=[1, 2],
    )
    entity = climate.TuyaBLEClimate(hass, coordinator, device, product, mapping)
    await entity.async_added_to_hass()
    with patch.object(device, "send_datapoints", new_callable=AsyncMock):
        await entity.async_set_hvac_mode(HVACMode.OFF)
        datapoint = device.datapoints[1]
        assert datapoint is not None
        assert datapoint.get_value() == b"\x02"
    coordinator.async_set_updated_data({})
    assert entity.hvac_mode == HVACMode.OFF


async def test_calibration_adopts_only_the_same_semantic_legacy_id(
    hass: HomeAssistant,
) -> None:
    """The source calibration key is compatible; bogus programming IDs are not."""
    device, coordinator, product = _context(hass)
    registry = entity_registry.async_get(hass)
    registry.async_get_or_create(
        "number", DOMAIN, f"{device.device_id}-temperature_calibration"
    )
    mapping = number.get_mapping_by_device(device)[0]
    entity = number.TuyaBLENumber(hass, coordinator, device, product, mapping)
    assert entity.unique_id == f"{device.device_id}-temperature_calibration"
    assert mapping.dp_id == 101
    descriptor = device_registry.get_registry().get("wkf", "llflaywg")
    assert descriptor is not None
    assert not any(item.legacy_keys for item in descriptor.get("sensor"))
