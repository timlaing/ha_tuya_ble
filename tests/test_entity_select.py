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
) -> TuyaBLESelect:
    """Build a select entity with the given options."""
    mapping = select.TuyaBLESelectMapping(
        dp_id=3,
        description=SelectEntityDescription(
            key="mode", options=options or ["off", "auto", "manual"]
        ),
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
