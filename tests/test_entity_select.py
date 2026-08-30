"""Unit tests for the Tuya BLE select entity."""

from __future__ import annotations

from homeassistant.components.select import SelectEntityDescription
from homeassistant.core import HomeAssistant

from custom_components.tuya_ble import select
from custom_components.tuya_ble.devices import (
    TuyaBLECoordinator,
    TuyaBLEProductInfo,
)
from custom_components.tuya_ble.select import TuyaBLESelect
from custom_components.tuya_ble.tuya_ble import (
    TuyaBLEDataPointType,
    TuyaBLEDevice,
)
from tests.conftest import add_dp, build_context, connect


def _make_entity(
    hass: HomeAssistant,
    device: TuyaBLEDevice,
    coordinator: TuyaBLECoordinator,
    product: TuyaBLEProductInfo,
    options: list[str] | None = None,
    values: list[str] | None = None,
    dp_type: TuyaBLEDataPointType | None = None,
) -> TuyaBLESelect:
    """Build a select entity with the given options."""
    mapping = select.TuyaBLESelectMapping(
        dp_id=3,
        description=SelectEntityDescription(
            key="mode", options=options or ["off", "auto", "manual"]
        ),
        values=values,
        dp_type=dp_type,
    )
    entity = select.TuyaBLESelect(hass, coordinator, device, product, mapping)
    entity.hass = hass
    return entity


async def test_current_option_index(hass: HomeAssistant) -> None:
    """Verify an enum datapoint indexes into the options."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    add_dp(device, 3, TuyaBLEDataPointType.DT_ENUM, 1)
    assert entity.current_option == "auto"


async def test_current_option_raw(hass: HomeAssistant) -> None:
    """Verify a string datapoint is returned as the current option."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    add_dp(device, 3, TuyaBLEDataPointType.DT_STRING, "custom")
    assert entity.current_option == "custom"


async def test_current_option_value(hass: HomeAssistant) -> None:
    """Verify a string datapoint matching a mapped value returns its option."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        options=["cancel", "24h", "48h"],
        values=["cancel", "24h", "48h"],
    )
    add_dp(device, 3, TuyaBLEDataPointType.DT_STRING, "48h")
    assert entity.current_option == "48h"


async def test_current_option_value_day_range(hass: HomeAssistant) -> None:
    """Verify a string datapoint maps to its display option."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        options=["Off", "1 day", "2 days"],
        values=["OFF", "1", "2"],
    )
    add_dp(device, 3, TuyaBLEDataPointType.DT_STRING, "2")
    assert entity.current_option == "2 days"


async def test_current_option_value_out_of_range(hass: HomeAssistant) -> None:
    """Verify an out-of-range mapped value falls back to the raw value."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        options=["Off"],
        values=["OFF", "1"],
    )
    add_dp(device, 3, TuyaBLEDataPointType.DT_STRING, "1")
    assert entity.current_option == "1"


async def test_select_option_values_out_of_range(hass: HomeAssistant) -> None:
    """Verify an option beyond the mapped values does not create a datapoint."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        options=["Off", "1 day", "2 days"],
        values=["OFF", "1"],
    )
    entity.select_option("2 days")
    await hass.async_block_till_done()
    assert device.datapoints[3] is None


async def test_select_option_values(hass: HomeAssistant) -> None:
    """Verify select_option writes the mapped string value."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        options=["cancel", "24h", "48h"],
        values=["cancel", "24h", "48h"],
    )
    entity.select_option("24h")
    await hass.async_block_till_done()
    dp = device.datapoints[3]
    assert dp is not None
    assert dp.dp_type == TuyaBLEDataPointType.DT_STRING
    assert dp.value == "24h"


async def test_select_option_values_mapping_dp_type(hass: HomeAssistant) -> None:
    """Verify select_option uses the mapping dp_type for string values."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        options=["cancel", "24h", "48h"],
        values=["cancel", "24h", "48h"],
        dp_type=TuyaBLEDataPointType.DT_STRING,
    )
    entity.select_option("48h")
    await hass.async_block_till_done()
    dp = device.datapoints[3]
    assert dp is not None
    assert dp.dp_type == TuyaBLEDataPointType.DT_STRING
    assert dp.value == "48h"


async def test_select_option_mapping_dp_type(hass: HomeAssistant) -> None:
    """Verify select_option uses the mapping dp_type without values."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        dp_type=TuyaBLEDataPointType.DT_STRING,
    )
    entity.select_option("manual")
    await hass.async_block_till_done()
    dp = device.datapoints[3]
    assert dp is not None
    assert dp.dp_type == TuyaBLEDataPointType.DT_STRING
    assert dp.value == "2"


async def test_select_option_values_day_range(hass: HomeAssistant) -> None:
    """Verify select_option writes the mapped string value."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(
        hass,
        device,
        coordinator,
        product,
        options=["Off", "1 day", "2 days"],
        values=["OFF", "1", "2"],
    )
    entity.select_option("1 day")
    await hass.async_block_till_done()
    dp = device.datapoints[3]
    assert dp is not None
    assert dp.dp_type == TuyaBLEDataPointType.DT_STRING
    assert dp.value == "1"


async def test_current_option_none(hass: HomeAssistant) -> None:
    """Verify current_option is None without a datapoint."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    assert entity.current_option is None


async def test_select_option(hass: HomeAssistant) -> None:
    """Verify select_option writes the option index."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    entity.select_option("manual")
    await hass.async_block_till_done()
    dp = device.datapoints[3]
    assert dp is not None
    assert dp.value == 2


async def test_select_option_invalid(hass: HomeAssistant) -> None:
    """Verify an invalid option does not create a datapoint."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    entity.select_option("nope")
    await hass.async_block_till_done()
    assert device.datapoints[3] is None


async def test_available(hass: HomeAssistant) -> None:
    """Verify availability follows the coordinator connection state."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    assert entity.available is False
    await connect(coordinator)
    assert entity.available is True
