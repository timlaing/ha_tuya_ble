"""Unit tests for the Tuya BLE light entity."""
# pylint: disable=protected-access

from __future__ import annotations

from homeassistant.components.light import (
    LightEntityDescription,
)
from homeassistant.components.light.const import ColorMode
from homeassistant.core import HomeAssistant

from custom_components.tuya_ble import light
from custom_components.tuya_ble.devices import (
    TuyaBLECoordinator,
    TuyaBLEProductInfo,
)
from custom_components.tuya_ble.light import TuyaBLELight
from custom_components.tuya_ble.tuya_ble import (
    TuyaBLEDataPointType,
    TuyaBLEDevice,
)
from custom_components.tuya_ble.tuya_ble.manager import TuyaBLEDeviceCredentials
from tests.conftest import add_dp, build_context, connect


def _make_entity(
    hass: HomeAssistant,
    device: TuyaBLEDevice,
    coordinator: TuyaBLECoordinator,
    product: TuyaBLEProductInfo,
    switch_dp_id: int = 1,
    brightness_dp_id: int = 3,
    color_temp_dp_id: int = 4,
    color_data_dp_id: int = 5,
    color_mode_dp_id: int = 2,
) -> TuyaBLELight:
    """Build a light entity."""
    mapping = light.TuyaBLELightMapping(
        description=LightEntityDescription(
            key="switch_led",
            name=None,
        ),
        switch_dp_id=switch_dp_id,
        brightness_dp_id=brightness_dp_id,
        color_temp_dp_id=color_temp_dp_id,
        color_data_dp_id=color_data_dp_id,
        color_mode_dp_id=color_mode_dp_id,
        brightness_min=0,
        brightness_max=1000,
        color_temp_min=0,
        color_temp_max=100,
    )
    entity = light.TuyaBLELight(hass, coordinator, device, product, mapping)
    entity.hass = hass
    return entity


def _set_device_credentials(
    device: TuyaBLEDevice, category: str, product_id: str
) -> None:
    """Set device credentials for category/product_id lookups."""
    device._device_info = TuyaBLEDeviceCredentials(  # noqa: SLF001
        uuid="u",
        local_key="k",
        device_id="dev",
        category=category,
        product_id=product_id,
        device_name="n",
        product_model="m",
        product_name="pm",
    )


# --- is_on ---


async def test_is_on_true(hass: HomeAssistant) -> None:
    """Verify is_on is True when switch DP is True."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    add_dp(device, 1, TuyaBLEDataPointType.DT_BOOL, True)
    assert entity.is_on is True


async def test_is_on_false(hass: HomeAssistant) -> None:
    """Verify is_on is False when switch DP is False."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    add_dp(device, 1, TuyaBLEDataPointType.DT_BOOL, False)
    assert entity.is_on is False


async def test_is_on_no_switch_dp(hass: HomeAssistant) -> None:
    """Verify is_on is False when switch_dp_id is 0."""
    device, coordinator, product = build_context(hass)
    mapping = light.TuyaBLELightMapping(
        description=LightEntityDescription(key="switch_led", name=None),
        switch_dp_id=0,
    )
    entity = light.TuyaBLELight(hass, coordinator, device, product, mapping)
    entity.hass = hass
    assert entity.is_on is False


async def test_is_on_no_datapoint(hass: HomeAssistant) -> None:
    """Verify is_on is False when datapoint is None."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    # No add_dp, so datapoint doesn't exist
    assert entity.is_on is False


# --- brightness ---


async def test_brightness_from_dp(hass: HomeAssistant) -> None:
    """Verify brightness is remapped from device range to 0-255."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    add_dp(device, 2, TuyaBLEDataPointType.DT_ENUM, "white")
    add_dp(device, 3, TuyaBLEDataPointType.DT_VALUE, 500)  # 500/1000 = 50%
    assert entity.brightness == 128


async def test_brightness_from_hs_color(hass: HomeAssistant) -> None:
    """Verify brightness extracted from HS color data when in HS mode."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    add_dp(device, 2, TuyaBLEDataPointType.DT_ENUM, "colour")
    # H=0, S=0, V=500 -> brightness = 500/1000*255 ~ 128
    add_dp(device, 5, TuyaBLEDataPointType.DT_STRING, "0000000001f4")
    assert entity.brightness == 128


async def test_brightness_no_dp(hass: HomeAssistant) -> None:
    """Verify brightness is None when brightness_dp_id is 0."""
    device, coordinator, product = build_context(hass)
    mapping = light.TuyaBLELightMapping(
        description=LightEntityDescription(key="switch_led", name=None),
        switch_dp_id=1,
        brightness_dp_id=0,
    )
    entity = light.TuyaBLELight(hass, coordinator, device, product, mapping)
    entity.hass = hass
    assert entity.brightness is None


async def test_brightness_no_datapoint(hass: HomeAssistant) -> None:
    """Verify brightness is None when brightness datapoint is missing."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    add_dp(device, 2, TuyaBLEDataPointType.DT_ENUM, "white")
    # No brightness DP added
    assert entity.brightness is None


# --- color_mode ---


async def test_color_mode_onoff(hass: HomeAssistant) -> None:
    """Verify color mode is ONOFF when no brightness/color DPs."""
    device, coordinator, product = build_context(hass)
    mapping = light.TuyaBLELightMapping(
        description=LightEntityDescription(key="switch_led", name=None),
        switch_dp_id=1,
    )
    entity = light.TuyaBLELight(hass, coordinator, device, product, mapping)
    entity.hass = hass
    assert entity.color_mode == ColorMode.ONOFF


async def test_color_mode_brightness(hass: HomeAssistant) -> None:
    """Verify color mode is BRIGHTNESS when only brightness DP is set."""
    device, coordinator, product = build_context(hass)
    mapping = light.TuyaBLELightMapping(
        description=LightEntityDescription(key="switch_led", name=None),
        switch_dp_id=1,
        brightness_dp_id=3,
    )
    entity = light.TuyaBLELight(hass, coordinator, device, product, mapping)
    entity.hass = hass
    add_dp(device, 3, TuyaBLEDataPointType.DT_VALUE, 500)
    assert entity.color_mode == ColorMode.BRIGHTNESS


async def test_color_mode_color_temp(hass: HomeAssistant) -> None:
    """Verify color mode is COLOR_TEMP when color_temp DP is set."""
    device, coordinator, product = build_context(hass)
    mapping = light.TuyaBLELightMapping(
        description=LightEntityDescription(key="switch_led", name=None),
        switch_dp_id=1,
        color_temp_dp_id=4,
    )
    entity = light.TuyaBLELight(hass, coordinator, device, product, mapping)
    entity.hass = hass
    add_dp(device, 4, TuyaBLEDataPointType.DT_VALUE, 50)
    assert entity.color_mode == ColorMode.COLOR_TEMP


async def test_color_mode_hs(hass: HomeAssistant) -> None:
    """Verify color mode is HS when work_mode is not white."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    add_dp(device, 2, TuyaBLEDataPointType.DT_ENUM, "colour")
    add_dp(device, 5, TuyaBLEDataPointType.DT_STRING, "00e600640064")
    assert entity.color_mode == ColorMode.HS


# --- hs_color ---


async def test_hs_color(hass: HomeAssistant) -> None:
    """Verify HS color is parsed from color data."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    add_dp(device, 2, TuyaBLEDataPointType.DT_ENUM, "colour")
    add_dp(device, 5, TuyaBLEDataPointType.DT_STRING, "00e600640064")
    hs = entity.hs_color
    assert hs is not None
    assert 0 <= hs[0] <= 360
    assert 0 <= hs[1] <= 100


async def test_hs_color_not_in_hs_mode(hass: HomeAssistant) -> None:
    """Verify hs_color is None when not in HS mode."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    add_dp(device, 2, TuyaBLEDataPointType.DT_ENUM, "white")
    assert entity.hs_color is None


async def test_hs_color_no_color_data(hass: HomeAssistant) -> None:
    """Verify hs_color is None when color data DP is missing."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product, color_data_dp_id=0)
    add_dp(device, 2, TuyaBLEDataPointType.DT_ENUM, "colour")
    assert entity.hs_color is None


# --- color_temp_kelvin ---


async def test_color_temp_kelvin(hass: HomeAssistant) -> None:
    """Verify color_temp_kelvin returns a value."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    add_dp(device, 4, TuyaBLEDataPointType.DT_VALUE, 50)
    result = entity.color_temp_kelvin
    assert result is not None
    assert isinstance(result, int)


async def test_color_temp_kelvin_no_dp(hass: HomeAssistant) -> None:
    """Verify color_temp_kelvin is None when color_temp_dp_id is 0."""
    device, coordinator, product = build_context(hass)
    mapping = light.TuyaBLELightMapping(
        description=LightEntityDescription(key="switch_led", name=None),
        switch_dp_id=1,
        color_temp_dp_id=0,
    )
    entity = light.TuyaBLELight(hass, coordinator, device, product, mapping)
    entity.hass = hass
    assert entity.color_temp_kelvin is None


async def test_color_temp_kelvin_no_datapoint(hass: HomeAssistant) -> None:
    """Verify color_temp_kelvin is None when datapoint is missing."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    # No color temp DP added
    assert entity.color_temp_kelvin is None


# --- turn_on / turn_off ---


async def test_turn_on(hass: HomeAssistant) -> None:
    """Verify turn_on sends switch True."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    entity.turn_on()
    await hass.async_block_till_done()
    dp = device.datapoints[1]
    assert dp is not None
    assert dp.value is True


async def test_turn_off(hass: HomeAssistant) -> None:
    """Verify turn_off sends switch False."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    entity.turn_off()
    await hass.async_block_till_done()
    dp = device.datapoints[1]
    assert dp is not None
    assert dp.value is False


async def test_turn_on_with_brightness(hass: HomeAssistant) -> None:
    """Verify turn_on with brightness sends correct value."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    entity.turn_on(brightness=128)
    await hass.async_block_till_done()
    dp = device.datapoints[3]
    assert dp is not None
    assert isinstance(dp.value, int)
    assert 490 <= dp.value <= 510


async def test_turn_on_with_color_temp(hass: HomeAssistant) -> None:
    """Verify turn_on with color_temp_kelvin sends work_mode white."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    entity.turn_on(color_temp_kelvin=4000)
    await hass.async_block_till_done()
    dp_mode = device.datapoints[2]
    dp_temp = device.datapoints[4]
    assert dp_mode is not None
    assert dp_mode.value == "white"
    assert dp_temp is not None


async def test_turn_on_with_hs_color(hass: HomeAssistant) -> None:
    """Verify turn_on with hs_color sends work_mode colour and color data."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    entity.turn_on(hs_color=(180.0, 50.0), brightness=128)
    await hass.async_block_till_done()
    dp_mode = device.datapoints[2]
    dp_color = device.datapoints[5]
    assert dp_mode is not None
    assert dp_mode.value == "colour"
    assert dp_color is not None
    assert isinstance(dp_color.value, str)
    assert len(dp_color.value) == 12


async def test_turn_on_hs_color_no_brightness(hass: HomeAssistant) -> None:
    """Verify turn_on with hs_color but no brightness uses default."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    entity.turn_on(hs_color=(0.0, 0.0))
    await hass.async_block_till_done()
    dp_color = device.datapoints[5]
    assert dp_color is not None


# --- _get_color_data ---


async def test_get_color_data_valid(hass: HomeAssistant) -> None:
    """Verify _get_color_data parses 12-char hex string."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    add_dp(device, 5, TuyaBLEDataPointType.DT_STRING, "00e600640064")
    result = entity._get_color_data()
    assert result is not None
    h, s, v = result
    assert h == 0x00E6
    assert s == 0x0064
    assert v == 0x0064


async def test_get_color_data_no_dp(hass: HomeAssistant) -> None:
    """Verify _get_color_data returns None when color_data_dp_id is 0."""
    device, coordinator, product = build_context(hass)
    mapping = light.TuyaBLELightMapping(
        description=LightEntityDescription(key="switch_led", name=None),
        switch_dp_id=1,
        color_data_dp_id=0,
    )
    entity = light.TuyaBLELight(hass, coordinator, device, product, mapping)
    entity.hass = hass
    assert entity._get_color_data() is None


async def test_get_color_data_no_datapoint(hass: HomeAssistant) -> None:
    """Verify _get_color_data returns None when datapoint is missing."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    # No color data DP added
    assert entity._get_color_data() is None


async def test_get_color_data_empty_value(hass: HomeAssistant) -> None:
    """Verify _get_color_data returns None when value is empty."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    add_dp(device, 5, TuyaBLEDataPointType.DT_STRING, "")
    assert entity._get_color_data() is None


async def test_get_color_data_wrong_length(hass: HomeAssistant) -> None:
    """Verify _get_color_data returns None for non-12-char strings."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    add_dp(device, 5, TuyaBLEDataPointType.DT_STRING, "abc")
    assert entity._get_color_data() is None


# --- get_mapping_by_device ---


async def test_get_mapping_by_device_dd(hass: HomeAssistant) -> None:
    """Verify get_mapping_by_device returns mappings for dd category."""
    device, _coordinator, _product = build_context(hass)
    _set_device_credentials(device, "dd", "nvfrtxlq")
    mappings = light.get_mapping_by_device(device)
    assert len(mappings) == 1
    assert mappings[0].switch_dp_id == 1


async def test_get_mapping_by_device_unknown(hass: HomeAssistant) -> None:
    """Verify get_mapping_by_device returns empty for unknown category."""
    device, _coordinator, _product = build_context(hass)
    _set_device_credentials(device, "unknown_cat", "unknown_prod")
    mappings = light.get_mapping_by_device(device)
    assert mappings == []


async def test_get_mapping_by_device_fallback(hass: HomeAssistant) -> None:
    """Verify get_mapping_by_device returns category.mapping when product not found."""
    device, _coordinator, _product = build_context(hass)
    _set_device_credentials(device, "dd", "nonexistent_product")
    mappings = light.get_mapping_by_device(device)
    assert len(mappings) == 1
    assert mappings[0].switch_dp_id == 1


# --- Availability ---


async def test_available(hass: HomeAssistant) -> None:
    """Verify availability follows the coordinator connection state."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    assert entity.available is False
    await connect(coordinator)
    assert entity.available is True


# --- color_mode branches ---


async def test_turn_on_with_color_temp_and_mode_dp(hass: HomeAssistant) -> None:
    """Verify turn_on with color_temp_kelvin and color_mode_dp_id sends white mode."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    entity.turn_on(color_temp_kelvin=4000)
    dp_mode = device.datapoints[2]
    assert dp_mode is not None
    assert dp_mode.value == "white"
    dp_temp = device.datapoints[4]
    assert dp_temp is not None


async def test_turn_on_with_hs_color_and_mode_dp(hass: HomeAssistant) -> None:
    """Verify turn_on with hs_color and color_mode_dp_id sends colour mode."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    entity.turn_on(hs_color=(180.0, 50.0), brightness=128)
    dp_mode = device.datapoints[2]
    assert dp_mode is not None
    assert dp_mode.value == "colour"
    dp_color = device.datapoints[5]
    assert dp_color is not None
    assert isinstance(dp_color.value, str)
    assert len(dp_color.value) == 12


async def test_turn_on_brightness_only(hass: HomeAssistant) -> None:
    """Verify turn_on with brightness only sends brightness value."""
    device, coordinator, product = build_context(hass)
    mapping = light.TuyaBLELightMapping(
        description=LightEntityDescription(key="switch_led", name=None),
        switch_dp_id=1,
        brightness_dp_id=3,
        brightness_min=0,
        brightness_max=1000,
    )
    entity = light.TuyaBLELight(hass, coordinator, device, product, mapping)
    entity.hass = hass
    entity.turn_on(brightness=128)
    dp = device.datapoints[3]
    assert dp is not None
    assert isinstance(dp.value, int)
    assert 490 <= dp.value <= 510


# --- color_mode branches ---


async def test_color_mode_hs_with_no_color_data(hass: HomeAssistant) -> None:
    """Verify color_mode returns HS when mode is colour but color data is missing."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product, color_data_dp_id=0)
    add_dp(device, 2, TuyaBLEDataPointType.DT_ENUM, "colour")
    # color_data_dp_id is 0, so it falls through to HS check
    # but color_data_dp_id=0 means HS not supported, so COLOR_TEMP
    assert entity.color_mode == ColorMode.COLOR_TEMP


async def test_color_mode_hs_with_no_color_mode_dp(hass: HomeAssistant) -> None:
    """Verify color_mode returns COLOR_TEMP when color_mode_dp_id is 0."""
    device, coordinator, product = build_context(hass)
    mapping = light.TuyaBLELightMapping(
        description=LightEntityDescription(key="switch_led", name=None),
        switch_dp_id=1,
        color_temp_dp_id=4,
        color_mode_dp_id=0,
        color_data_dp_id=5,
    )
    entity = light.TuyaBLELight(hass, coordinator, device, product, mapping)
    entity.hass = hass
    assert entity.color_mode == ColorMode.COLOR_TEMP
