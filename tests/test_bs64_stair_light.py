"""Public regression tests for the experimental MiBoxer BS64 descriptor."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from homeassistant.components.light.const import ColorMode
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
import pytest
import yaml

from custom_components.tuya_ble import light as light_platform
from custom_components.tuya_ble.coordinator import TuyaBLECoordinator
import custom_components.tuya_ble.device_registry as dr
from custom_components.tuya_ble.device_registry import (
    DeviceRegistry,
    DeviceRegistryError,
    get_mapped_dp_ids,
)
from custom_components.tuya_ble.entity import (
    TuyaBLEProductInfo,
    get_product_info_by_ids,
)
from custom_components.tuya_ble.light import TuyaBLELight, get_mapping_by_device
from custom_components.tuya_ble.select import (
    TuyaBLESelect,
)
from custom_components.tuya_ble.select import (
    get_mapping_by_device as get_select_mapping,
)
from custom_components.tuya_ble.tuya_ble import (
    TuyaBLEDataPoint,
    TuyaBLEDataPointType,
    TuyaBLEDevice,
    TuyaBLEDeviceCredentials,
)
from tests.conftest import add_dp, build_context
from tests.test_entity_light import _make_entity


def _context(
    hass: HomeAssistant,
) -> tuple[TuyaBLEDevice, TuyaBLECoordinator, TuyaBLEProductInfo, TuyaBLELight]:
    """Build a real device and entity with BLE writes mocked."""
    device, coordinator, _product = build_context(hass)
    device._device_info = TuyaBLEDeviceCredentials(  # pylint: disable=protected-access
        uuid="u",
        local_key="k",
        device_id="bs64",
        category="dj",
        product_id="c2twgywo",
        device_name="BS64",
        product_model="BS64",
        product_name="BS64",
    )
    device._protocol_version = 4  # pylint: disable=protected-access
    device.send_datapoints = AsyncMock()  # type: ignore[method-assign]  # Mock BLE I/O.
    product = get_product_info_by_ids("dj", "c2twgywo")
    entity = TuyaBLELight(
        hass, coordinator, device, product, get_mapping_by_device(device)[0]
    )
    entity.hass = hass
    return device, coordinator, product, entity


def _send(device: TuyaBLEDevice) -> AsyncMock:
    """Return the mocked BLE send for assertions."""
    return cast(AsyncMock, device.send_datapoints)


def _dp(device: TuyaBLEDevice, dp_id: int) -> TuyaBLEDataPoint:
    """Require a datapoint before inspecting its wire value."""
    datapoint = device.datapoints[dp_id]
    assert datapoint is not None
    return datapoint


def test_registry_overrides_and_deferred_controls() -> None:
    """Product mappings replace dj defaults without affecting other lights."""
    registry = dr.get_registry()
    product = registry.get("dj", "c2twgywo")
    assert product is not None
    assert product.manufacturer == "MiBoxer"
    assert product.get("number") == []
    light = product.get("light")[0]
    assert light.resolved_handler("read") is not None
    assert light.resolved_handler("write") is not None
    assert "color_temp_dp_id" not in light.extra
    assert get_mapped_dp_ids("dj", "c2twgywo") == {2, 3, 104, 114}
    assert registry.category_defaults["dj"]["light"][0].extra["switch_dp_id"] == 20


async def test_modes_and_unique_id(hass: HomeAssistant) -> None:
    """Auto/off are off; DP1 has no bearing on light state."""
    device, _coordinator, _product, entity = _context(hass)
    assert entity.unique_id == "bs64-switch_led"
    assert entity.supported_color_modes == {ColorMode.BRIGHTNESS}
    assert entity.is_on is False
    assert entity.brightness is None
    assert entity.color_temp_kelvin is None
    add_dp(device, 1, TuyaBLEDataPointType.DT_BOOL, True)
    for mode, expected in ((0, False), (1, True), (2, False), (99, False)):
        add_dp(device, 114, TuyaBLEDataPointType.DT_ENUM, mode)
        assert entity.is_on is expected


@pytest.mark.parametrize("brightness,expected", [(0, 10), (128, 507), (255, 1000)])
@pytest.mark.parametrize("mode", [0, 1, 2])
async def test_atomic_brightness(
    hass: HomeAssistant, brightness: int, expected: int, mode: int
) -> None:
    """Always include work mode, even if it already reports white."""
    device, _coordinator, _product, entity = _context(hass)
    add_dp(device, 114, TuyaBLEDataPointType.DT_ENUM, mode)
    add_dp(device, 2, TuyaBLEDataPointType.DT_ENUM, 0)
    await entity.async_turn_on(brightness=brightness)
    _send(device).assert_awaited_once_with([2, 3] if mode == 1 else [114, 2, 3])
    assert _dp(device, 2).get_value() == b"\x00"
    assert _dp(device, 3).value == expected
    assert entity.brightness == brightness
    assert device.datapoints[1] is None
    assert _dp(device, 114).reported_value == mode
    assert _dp(device, 114).report_revision == 1
    assert _dp(device, 2).reported_value == 0
    assert _dp(device, 2).report_revision == 1
    assert _dp(device, 3).reported_value is None
    assert _dp(device, 3).report_revision == 0


async def test_on_off_noop_and_sync_paths(hass: HomeAssistant) -> None:
    """Synchronous compatibility wrappers keep mode writes atomic."""
    device, _coordinator, _product, entity = _context(hass)
    entity.turn_on()
    await hass.async_block_till_done()
    _send(device).assert_awaited_once_with([114])
    assert _dp(device, 114).value == 1
    _send(device).reset_mock()
    await entity.async_turn_on()
    _send(device).assert_not_called()
    entity.turn_off(brightness=255)
    await hass.async_block_till_done()
    _send(device).assert_awaited_once_with([114])
    assert _dp(device, 114).get_value() == b"\x02"
    assert device.datapoints[1] is None
    assert device.datapoints[3] is None


@pytest.mark.parametrize("value", [-1, 256, True, 1.5, "128", float("nan")])
async def test_invalid_brightness_no_mutation(hass: HomeAssistant, value: Any) -> None:
    """Reject bad input before creating or modifying any DP."""
    device, _coordinator, _product, entity = _context(hass)
    with pytest.raises(ServiceValidationError):
        await entity.async_turn_on(brightness=value)
    _send(device).assert_not_called()
    assert device.datapoints[114] is None
    assert device.datapoints[3] is None


@pytest.mark.parametrize(
    "error", [TimeoutError("write rejected"), asyncio.CancelledError()]
)
async def test_failure_rollback(hass: HomeAssistant, error: BaseException) -> None:
    """Await errors and cancellation; restore every reported batch value."""
    device, _coordinator, _product, entity = _context(hass)
    add_dp(device, 114, TuyaBLEDataPointType.DT_ENUM, 0)
    add_dp(device, 2, TuyaBLEDataPointType.DT_ENUM, 1)
    add_dp(device, 3, TuyaBLEDataPointType.DT_VALUE, 400)
    _send(device).side_effect = error
    with pytest.raises(type(error)):
        await entity.async_turn_on(brightness=255)
    assert [_dp(device, i).value for i in (114, 2, 3)] == [0, 1, 400]
    assert [_dp(device, i).reported_value for i in (114, 2, 3)] == [0, 1, 400]
    assert [_dp(device, i).report_revision for i in (114, 2, 3)] == [1, 1, 1]
    _send(device).assert_awaited_once_with([114, 2, 3])


async def test_select_read_write_and_invalid(hass: HomeAssistant) -> None:
    """Exercise both selects through the platform's public entry points."""
    device, coordinator, product, _entity = _context(hass)
    for mapping in get_select_mapping(device):
        entity = TuyaBLESelect(hass, coordinator, device, product, mapping)
        entity.hass = hass
        for index, option in enumerate(entity.options):
            add_dp(device, mapping.dp_id, TuyaBLEDataPointType.DT_ENUM, index)
            assert entity.current_option == option
            entity.select_option(option)
            await hass.async_block_till_done()
            assert _dp(device, mapping.dp_id).get_value() == bytes([index])
        _send(device).reset_mock()
        entity.select_option("invalid")
        await hass.async_block_till_done()
        _send(device).assert_not_called()


async def test_listener_notification(hass: HomeAssistant) -> None:
    """Subscribe through HA lifecycle, then observe a reported mode."""
    device, coordinator, _product, entity = _context(hass)
    with patch.object(entity, "async_write_ha_state") as write_state:
        await entity.async_added_to_hass()
        add_dp(device, 114, TuyaBLEDataPointType.DT_ENUM, 1)
        coordinator.async_set_updated_data({})
        write_state.assert_called()
        assert entity.is_on is True
        await entity.async_will_remove_from_hass()


@pytest.mark.parametrize(
    "key,value",
    [
        ("mode_dp_id", None),
        ("mode_dp_id", True),
        ("mode_dp_id", 0),
        ("color_mode_dp_id", 256),
        ("color_mode_dp_id", 114),
        ("brightness_dp_id", "3"),
        ("on_value", None),
        ("on_value", True),
        ("on_value", -1),
        ("off_value", 1),
        ("brightness_min", "10"),
        ("brightness_max", True),
        ("brightness_min", -1),
        ("brightness_max", 10),
    ],
)
def test_invalid_light_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: str, value: Any
) -> None:
    """Reject unsafe mode parameters through the public registry loader."""
    raw: dict[str, Any] = {
        "mode_dp_id": 114,
        "color_mode_dp_id": 2,
        "brightness_dp_id": 3,
        "on_value": 1,
        "off_value": 2,
        "white_value": 0,
        "brightness_min": 10,
        "brightness_max": 1000,
    }
    raw[key] = value
    (tmp_path / "test.yaml").write_text(
        yaml.safe_dump({
            "category": "dj",
            "product_id": "test",
            "entities": {"light": [raw]},
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(dr, "_DEVICES_DIR", tmp_path)
    with pytest.raises(DeviceRegistryError):
        DeviceRegistry.load()


async def test_wrong_wire_type_rejected(hass: HomeAssistant) -> None:
    """An unexpected report cannot turn a mode enum into a boolean command."""
    device, _coordinator, _product, entity = _context(hass)
    add_dp(device, 2, TuyaBLEDataPointType.DT_BOOL, True)
    with pytest.raises(ServiceValidationError):
        await entity.async_turn_on(brightness=255)
    _send(device).assert_not_called()
    assert device.datapoints[114] is None


async def test_mode_bool_report_rejected(hass: HomeAssistant) -> None:
    """A DT_BOOL mode report matching on_value is still rejected as On."""
    device, _coordinator, _product, entity = _context(hass)
    # DT_BOOL True compares equal to on_value 1, so the no-op path would
    # omit DP114 from the update and never reach the per-update type loop.
    add_dp(device, 114, TuyaBLEDataPointType.DT_BOOL, True)
    with pytest.raises(ServiceValidationError):
        await entity.async_turn_on(brightness=255)
    _send(device).assert_not_called()


async def test_wire_type_recheck_inside_write_lock(hass: HomeAssistant) -> None:
    """A report changing a wire type while the write waits still raises."""
    device, _coordinator, _product, entity = _context(hass)
    for dp_id, dp_type, value in (
        (114, TuyaBLEDataPointType.DT_ENUM, 0),
        (2, TuyaBLEDataPointType.DT_ENUM, 1),
        (3, TuyaBLEDataPointType.DT_VALUE, 400),
    ):
        add_dp(device, dp_id, dp_type, value)

    lock = device.datapoints._write_lock  # pylint: disable=protected-access
    async with device.datapoints.apply_with_rollback([]):
        task = asyncio.create_task(entity.async_turn_on(brightness=255))
        for _ in range(50):
            # pylint: disable-next=protected-access
            if lock._waiters:
                break
            await asyncio.sleep(0)
        else:
            pytest.fail("write request never reached the lock")
        # A device report changes the brightness wire type while queued.
        add_dp(device, 3, TuyaBLEDataPointType.DT_STRING, "400")

    with pytest.raises(ServiceValidationError):
        await task
    _send(device).assert_not_called()
    assert _dp(device, 3).value == "400"


async def test_existing_light_async_defaults(hass: HomeAssistant) -> None:
    """Generic async hooks still delegate to existing light write behavior."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    await entity.async_turn_on(brightness=128, color_temp_kelvin=4000)
    await hass.async_block_till_done()
    assert _dp(device, 1).value is True
    assert _dp(device, 2).value == 0
    assert _dp(device, 3).value == 502
    assert _dp(device, 4).value == 55
    await entity.async_turn_off()
    await hass.async_block_till_done()
    assert _dp(device, 1).value is False


async def test_existing_light_missing_color_falls_back(hass: HomeAssistant) -> None:
    """HS mode without a color report still reads the brightness DP."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    add_dp(device, 2, TuyaBLEDataPointType.DT_ENUM, 1)
    add_dp(device, 3, TuyaBLEDataPointType.DT_VALUE, 500)
    assert entity.brightness == 128


@pytest.mark.parametrize("empty_products", [None, {}])
async def test_light_category_without_products(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, empty_products: Any
) -> None:
    """A category with no product table has no mapping."""
    device, _coordinator, _product, _entity = _context(hass)
    monkeypatch.setitem(
        light_platform.mapping,
        "dj",
        light_platform.TuyaBLECategoryLightMapping(products=empty_products),
    )
    assert get_mapping_by_device(device) == []


@pytest.mark.parametrize("error", [TimeoutError(), asyncio.CancelledError()])
async def test_new_datapoint_failure_uses_safe_floor(
    hass: HomeAssistant, error: BaseException
) -> None:
    """No first-write failure leaves requested brightness or mode cached."""
    device, _coordinator, _product, entity = _context(hass)
    _send(device).side_effect = error
    with pytest.raises(type(error)):
        await entity.async_turn_on(brightness=255)
    assert entity.is_on is False
    assert entity.brightness == 0
    assert _dp(device, 114).value == 2
    assert _dp(device, 3).value == 10
    for dp_id in (114, 2, 3):
        assert _dp(device, dp_id).reported_value is None
        assert _dp(device, dp_id).report_revision == 0


async def test_new_report_survives_rollback(hass: HomeAssistant) -> None:
    """An in-flight report supersedes optimistic values even if send fails."""
    device, _coordinator, _product, entity = _context(hass)
    add_dp(device, 114, TuyaBLEDataPointType.DT_ENUM, 0)
    add_dp(device, 3, TuyaBLEDataPointType.DT_VALUE, 400)

    async def report_then_fail(_ids: list[int]) -> None:
        add_dp(device, 3, TuyaBLEDataPointType.DT_VALUE, 600)
        raise TimeoutError()

    _send(device).side_effect = report_then_fail
    with pytest.raises(TimeoutError):
        await entity.async_turn_on(brightness=255)
    assert _dp(device, 114).value == 0
    assert _dp(device, 3).value == 600
    assert _dp(device, 114).reported_value == 0
    assert _dp(device, 114).report_revision == 1
    assert _dp(device, 3).reported_value == 600
    assert _dp(device, 3).report_revision == 2
    assert _dp(device, 2).reported_value is None
    assert _dp(device, 2).report_revision == 0


async def test_first_success_is_not_a_device_report(hass: HomeAssistant) -> None:
    """A completed write and an optimistic On no-op never invent reports."""
    device, _coordinator, _product, entity = _context(hass)
    await entity.async_turn_on(brightness=255)
    assert entity.is_on is True
    assert entity.brightness == 255
    for dp_id in (114, 2, 3):
        assert _dp(device, dp_id).reported_value is None
        assert _dp(device, dp_id).report_revision == 0
    await entity.async_turn_on()
    _send(device).assert_awaited_once_with([114, 2, 3])
    for dp_id, value, dp_type in (
        (114, 1, TuyaBLEDataPointType.DT_ENUM),
        (2, 0, TuyaBLEDataPointType.DT_ENUM),
        (3, 1000, TuyaBLEDataPointType.DT_VALUE),
    ):
        add_dp(device, dp_id, dp_type, value)
        assert _dp(device, dp_id).reported_value == value
        assert _dp(device, dp_id).report_revision == 1
        add_dp(device, dp_id, dp_type, value)
        assert _dp(device, dp_id).report_revision == 2


async def test_pending_on_preserves_confirmed_values(hass: HomeAssistant) -> None:
    """Pending and completed writes keep reported state separate from optimism."""
    device, _coordinator, _product, entity = _context(hass)
    for dp_id, value, dp_type in (
        (114, 0, TuyaBLEDataPointType.DT_ENUM),
        (2, 1, TuyaBLEDataPointType.DT_ENUM),
        (3, 400, TuyaBLEDataPointType.DT_VALUE),
    ):
        add_dp(device, dp_id, dp_type, value)

    async def inspect_pending(ids: list[int]) -> None:
        assert ids == [114, 2, 3]
        assert entity.is_on is True
        assert entity.brightness == 255
        assert [_dp(device, i).value for i in ids] == [1, 0, 1000]
        assert [_dp(device, i).reported_value for i in ids] == [0, 1, 400]
        assert [_dp(device, i).report_revision for i in ids] == [1, 1, 1]

    _send(device).side_effect = inspect_pending
    await entity.async_turn_on(brightness=255)
    assert [_dp(device, i).reported_value for i in (114, 2, 3)] == [0, 1, 400]
    assert [_dp(device, i).report_revision for i in (114, 2, 3)] == [1, 1, 1]


@pytest.mark.parametrize("error", [None, TimeoutError(), asyncio.CancelledError()])
async def test_off_preserves_confirmed_mode(
    hass: HomeAssistant, error: BaseException | None
) -> None:
    """An Off write does not become a report, even after failure or cancellation."""
    device, _coordinator, _product, entity = _context(hass)
    add_dp(device, 114, TuyaBLEDataPointType.DT_ENUM, 1)

    async def inspect_pending(ids: list[int]) -> None:
        assert ids == [114]
        assert entity.is_on is False
        assert _dp(device, 114).reported_value == 1
        assert _dp(device, 114).report_revision == 1
        if error is not None:
            raise error

    _send(device).side_effect = inspect_pending
    if error is None:
        await entity.async_turn_off()
        assert entity.is_on is False
    else:
        with pytest.raises(type(error)):
            await entity.async_turn_off()
        assert entity.is_on is True
    assert _dp(device, 114).reported_value == 1
    assert _dp(device, 114).report_revision == 1
    assert device.datapoints[1] is None
    assert device.datapoints[2] is None
    assert device.datapoints[3] is None
