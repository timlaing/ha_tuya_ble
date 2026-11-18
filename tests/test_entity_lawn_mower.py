"""Unit tests for the Tuya BLE lawn mower platform and entity."""

from __future__ import annotations

from dataclasses import dataclass, replace
import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.components.lawn_mower.const import (
    DATA_COMPONENT,
    SERVICE_DOCK,
    SERVICE_PAUSE,
    SERVICE_START_MOWING,
    SERVICE_STOP,
    LawnMowerActivity,
    LawnMowerEntityFeature,
)
from homeassistant.components.lawn_mower.const import (
    DOMAIN as LAWN_MOWER_DOMAIN,
)
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest

from custom_components.tuya_ble import lawn_mower
from custom_components.tuya_ble.const import DOMAIN
from custom_components.tuya_ble.device_registry import (
    DeviceRegistryError,
    EntityDescriptor,
    get_mapped_dp_ids,
)
from custom_components.tuya_ble.devices import get_product_info_by_ids
from custom_components.tuya_ble.lawn_mower import TuyaBLELawnMower
from custom_components.tuya_ble.tuya_ble import TuyaBLEDataPointType
from custom_components.tuya_ble.tuya_ble.exceptions import TuyaBLEDeviceError
from tests.conftest import add_dp, build_context, connect

STATUS_DP = 101
COMMAND_DP = 115

# Command indexes in the order the Parkside descriptor declares them.
PAUSE_WORK, CANCEL_WORK, CONTINUE_WORK, START_MOWING = 0, 1, 2, 3
START_RETURN_STATION = 5

# Status indexes in the order the Parkside descriptor declares them.
STATUS_INDEX = {
    "STANDBY": 0,
    "CHARGING": 1,
    "MOWING": 2,
    "PAUSED": 3,
    "PARK": 4,
    "UPDATA": 5,
    "FIXED_MOWING": 6,
    "ERROR": 7,
    "SELF_TEST": 8,
    "CHARGING_WITH_TASK_SUSPEND": 9,
    "EMERGENCY": 10,
    "LOCKED": 11,
    "EDGE": 12,
}


def _parkside_mapping() -> lawn_mower.TuyaBLELawnMowerMapping:
    """Return the shipped Parkside PAMRC 250 A1 lawn mower mapping."""
    mappings = lawn_mower.get_mapping_by_device(
        _FakeDevice("gcj", "9hdajpiw")  # type: ignore[arg-type]
    )
    assert len(mappings) == 1
    return mappings[0]


@dataclass
class _FakeDevice:
    """Stand-in exposing only the category and product id."""

    category: str
    product_id: str


def _make_entity(
    hass: HomeAssistant,
    mapping: lawn_mower.TuyaBLELawnMowerMapping | None = None,
) -> tuple[TuyaBLELawnMower, Any]:
    """Build a Parkside lawn mower entity over a recording device."""
    device, coordinator, _ = build_context(hass)
    product = get_product_info_by_ids("gcj", "9hdajpiw")
    entity = TuyaBLELawnMower(
        hass, coordinator, device, product, mapping or _parkside_mapping()
    )
    entity.hass = hass
    return entity, device


def _record_commands(device: Any) -> list[int]:
    """Record the command DP value each time the device is asked to send."""
    values: list[int] = []

    async def send(dp_ids: list[int]) -> None:
        assert dp_ids == [COMMAND_DP]
        datapoint = device.datapoints[COMMAND_DP]
        assert datapoint is not None
        values.append(datapoint.value)

    device.send_datapoints = send
    return values


def _descriptor(**extra: Any) -> EntityDescriptor:
    """Build a lawn mower descriptor with the given auxiliary fields."""
    values = extra.pop("values", ["IDLE", "WORKING"])
    return EntityDescriptor(
        platform="lawn_mower",
        dp_id=1,
        translation_key="lawn_mower",
        values=values,
        extra=extra,
    )


# --- Descriptor and mapping -------------------------------------------------


def test_parkside_descriptor_mapping() -> None:
    """The shipped descriptor declares the status and command tables."""
    mapping = _parkside_mapping()
    assert mapping.dp_id == STATUS_DP
    assert mapping.command_dp_id == COMMAND_DP
    assert mapping.description.key == "lawn_mower"
    assert mapping.start_command == "StartMowing"
    assert mapping.resume_command == "ContinueWork"
    assert mapping.pause_command == "PauseWork"
    assert mapping.stop_command == "CancelWork"
    assert mapping.dock_commands == ["CancelWork", "StartReturnStation"]
    assert set(mapping.activities) == set(mapping.statuses)
    assert COMMAND_DP in get_mapped_dp_ids("gcj", "9hdajpiw")


def test_unknown_device_has_no_mapping() -> None:
    """A device without a lawn mower descriptor gets no mower entity."""
    assert lawn_mower.get_mapping_by_device(_FakeDevice("nope", "nope")) == []  # type: ignore[arg-type]
    assert lawn_mower.get_mapping_by_device(_FakeDevice("gcj", "unknown")) == []  # type: ignore[arg-type]


def test_build_minimal_descriptor_offers_no_features() -> None:
    """A descriptor with no commands builds a read-only mower."""
    built = lawn_mower._build_lawn_mower_mapping(  # pylint: disable=protected-access
        EntityDescriptor(platform="lawn_mower", dp_id=7, translation_key="m")
    )
    assert not built.commands
    assert not built.statuses
    assert built.command_dp_id is None
    assert built.dp_type is None
    assert lawn_mower._supported_features(built) == LawnMowerEntityFeature(0)  # pylint: disable=protected-access


def test_build_descriptor_dp_type() -> None:
    """An explicit dp_type is carried onto the mapping."""
    built = lawn_mower._build_lawn_mower_mapping(  # pylint: disable=protected-access
        EntityDescriptor(platform="lawn_mower", dp_id=7, dp_type=4)
    )
    assert built.dp_type is TuyaBLEDataPointType.DT_ENUM
    assert built.description.key == "7"


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"values": "IDLE"}, "'values' must be a list of strings"),
        ({"commands": ["Go", 1], "command_dp_id": 2}, "'commands' must be a list"),
        ({"commands": ["Go"]}, "without an integer 'command_dp_id'"),
        ({"commands": ["Go"], "command_dp_id": "2"}, "without an integer"),
        ({"activities": ["idle"]}, "'activities' must be a mapping"),
        ({"activities": {"GONE": "idle"}}, "unknown status 'GONE'"),
        ({"activities": {"IDLE": "napping"}}, "unknown activity 'napping'"),
        ({"start_command": "Go"}, "'start_command' 'Go' is not a declared"),
        (
            {"commands": ["Go"], "command_dp_id": 2, "pause_command": "Hold"},
            "'pause_command' 'Hold'",
        ),
        (
            {"commands": ["Go"], "command_dp_id": 2, "dock_commands": ["Home"]},
            "dock command 'Home' is not a declared command",
        ),
        ({"dock_commands": "Home"}, "'dock_commands' must be a list"),
    ],
)
def test_build_descriptor_rejects_invalid_fields(
    fields: dict[str, Any], message: str
) -> None:
    """Malformed descriptors fail when the mapping is built."""
    descriptor = _descriptor(**fields)
    with pytest.raises(DeviceRegistryError, match=message):
        lawn_mower._build_lawn_mower_mapping(descriptor)  # pylint: disable=protected-access


def test_parkside_supported_features() -> None:
    """Every declared command is offered, and only those."""
    entity_features = lawn_mower._supported_features(_parkside_mapping())  # pylint: disable=protected-access
    assert entity_features == (
        LawnMowerEntityFeature.START_MOWING
        | LawnMowerEntityFeature.PAUSE
        | LawnMowerEntityFeature.DOCK
        | LawnMowerEntityFeature.STOP
    )


def test_features_follow_declared_commands() -> None:
    """A descriptor declaring only start and dock offers only those."""
    built = lawn_mower._build_lawn_mower_mapping(  # pylint: disable=protected-access
        _descriptor(
            commands=["Go", "Home"],
            command_dp_id=2,
            start_command="Go",
            dock_commands=["Home"],
        )
    )
    assert lawn_mower._supported_features(built) == (  # pylint: disable=protected-access
        LawnMowerEntityFeature.START_MOWING | LawnMowerEntityFeature.DOCK
    )


# --- Entity state ------------------------------------------------------------


async def test_unique_id_and_translation_key(hass: HomeAssistant) -> None:
    """The mower entity keeps a stable key-based unique id."""
    entity, device = _make_entity(hass)
    assert entity.unique_id == f"{device.device_id}-lawn_mower"
    assert entity.translation_key == "lawn_mower"


@pytest.mark.parametrize(
    ("status", "activity"),
    [
        ("STANDBY", LawnMowerActivity.IDLE),
        ("CHARGING", LawnMowerActivity.DOCKED),
        ("MOWING", LawnMowerActivity.MOWING),
        ("PAUSED", LawnMowerActivity.PAUSED),
        ("PARK", LawnMowerActivity.RETURNING),
        ("UPDATA", LawnMowerActivity.IDLE),
        ("FIXED_MOWING", LawnMowerActivity.MOWING),
        ("ERROR", LawnMowerActivity.ERROR),
        ("SELF_TEST", LawnMowerActivity.IDLE),
        ("CHARGING_WITH_TASK_SUSPEND", LawnMowerActivity.DOCKED),
        ("EMERGENCY", LawnMowerActivity.ERROR),
        ("LOCKED", LawnMowerActivity.IDLE),
        ("EDGE", LawnMowerActivity.MOWING),
    ],
)
async def test_activity_follows_status(
    hass: HomeAssistant, status: str, activity: LawnMowerActivity
) -> None:
    """Each reported status maps to its Home Assistant activity."""
    entity, device = _make_entity(hass)
    add_dp(device, STATUS_DP, TuyaBLEDataPointType.DT_ENUM, STATUS_INDEX[status])
    assert entity.activity is activity


async def test_activity_unknown_without_report(hass: HomeAssistant) -> None:
    """Before the mower reports its status the activity is unknown."""
    entity, _ = _make_entity(hass)
    assert entity.activity is None
    assert entity.state is None


@pytest.mark.parametrize("value", [True, "MOWING", b"\x02"])
async def test_activity_unknown_for_non_enum_value(
    hass: HomeAssistant, value: Any
) -> None:
    """A status that is not an enum index is reported as unknown."""
    entity, device = _make_entity(hass)
    add_dp(device, STATUS_DP, TuyaBLEDataPointType.DT_RAW, value)
    assert entity.activity is None


async def test_activity_unknown_for_out_of_range_code(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """An undeclared status code is unknown and logged at debug."""
    entity, device = _make_entity(hass)
    add_dp(device, STATUS_DP, TuyaBLEDataPointType.DT_ENUM, 99)
    with caplog.at_level(logging.DEBUG):
        assert entity.activity is None
    assert "Mower status code 99 is outside the declared statuses" in caplog.text


async def test_activity_unknown_for_unmapped_status(hass: HomeAssistant) -> None:
    """A declared status without an activity is unknown."""
    mapping = lawn_mower._build_lawn_mower_mapping(  # pylint: disable=protected-access
        _descriptor(activities={"WORKING": "mowing"})
    )
    entity, device = _make_entity(hass, mapping)
    add_dp(device, 1, TuyaBLEDataPointType.DT_ENUM, 0)
    assert entity.activity is None
    add_dp(device, 1, TuyaBLEDataPointType.DT_ENUM, 1)
    assert entity.activity is LawnMowerActivity.MOWING


async def test_available_follows_coordinator(hass: HomeAssistant) -> None:
    """The mower is unavailable until the device is connected."""
    entity, _ = _make_entity(hass)
    assert entity.available is False
    await connect(entity._coordinator)  # pylint: disable=protected-access
    assert entity.available is True


# --- Commands -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (None, START_MOWING),
        ("STANDBY", START_MOWING),
        ("CHARGING", START_MOWING),
        ("MOWING", START_MOWING),
        ("PAUSED", CONTINUE_WORK),
    ],
)
async def test_start_mowing_resumes_when_paused(
    hass: HomeAssistant, status: str | None, expected: int
) -> None:
    """Start sends StartMowing, or ContinueWork while the mower is paused."""
    entity, device = _make_entity(hass)
    if status is not None:
        add_dp(device, STATUS_DP, TuyaBLEDataPointType.DT_ENUM, STATUS_INDEX[status])
    sent = _record_commands(device)
    await entity.async_start_mowing()
    assert sent == [expected]


async def test_start_without_resume_command_restarts_when_paused(
    hass: HomeAssistant,
) -> None:
    """A mower with no resume command always sends its start command."""
    mapping = lawn_mower._build_lawn_mower_mapping(  # pylint: disable=protected-access
        _descriptor(
            values=["PAUSED"],
            activities={"PAUSED": "paused"},
            commands=["Go"],
            command_dp_id=COMMAND_DP,
            start_command="Go",
        )
    )
    entity, device = _make_entity(hass, mapping)
    add_dp(device, 1, TuyaBLEDataPointType.DT_ENUM, 0)
    sent = _record_commands(device)
    await entity.async_start_mowing()
    assert sent == [0]


async def test_pause_and_stop(hass: HomeAssistant) -> None:
    """Pause sends PauseWork and stop sends CancelWork."""
    entity, device = _make_entity(hass)
    sent = _record_commands(device)
    await entity.async_pause()
    await entity.async_stop()
    assert sent == [PAUSE_WORK, CANCEL_WORK]


async def test_dock_cancels_then_returns(hass: HomeAssistant) -> None:
    """Dock cancels the job first, then sends the return command, in order."""
    entity, device = _make_entity(hass)
    sent = _record_commands(device)
    await entity.async_dock()
    assert sent == [CANCEL_WORK, START_RETURN_STATION]
    datapoint = device.datapoints[COMMAND_DP]
    assert datapoint is not None
    assert datapoint.dp_type is TuyaBLEDataPointType.DT_ENUM


async def test_dock_stops_after_a_rejected_command(hass: HomeAssistant) -> None:
    """A rejected cancel aborts the dock sequence and reverts the local value."""
    entity, device = _make_entity(hass)
    add_dp(device, COMMAND_DP, TuyaBLEDataPointType.DT_ENUM, PAUSE_WORK)
    send = AsyncMock(side_effect=TuyaBLEDeviceError(1))
    with (
        patch.object(device, "send_datapoints", new=send),
        pytest.raises(TuyaBLEDeviceError),
    ):
        await entity.async_dock()
    send.assert_awaited_once_with([COMMAND_DP])
    datapoint = device.datapoints[COMMAND_DP]
    assert datapoint is not None
    assert datapoint.value == PAUSE_WORK


async def test_undeclared_commands_send_nothing(hass: HomeAssistant) -> None:
    """A read-only mower ignores every command."""
    mapping = lawn_mower._build_lawn_mower_mapping(  # pylint: disable=protected-access
        _descriptor(activities={"IDLE": "idle"})
    )
    entity, device = _make_entity(hass, mapping)
    send = AsyncMock()
    with patch.object(device, "send_datapoints", new=send):
        await entity.async_start_mowing()
        await entity.async_pause()
        await entity.async_stop()
        await entity.async_dock()
    send.assert_not_awaited()
    assert device.datapoints[COMMAND_DP] is None


# --- Home Assistant services -------------------------------------------------


async def _add_to_component(hass: HomeAssistant, entity: TuyaBLELawnMower) -> None:
    """Register the entity with the lawn_mower component."""
    assert await async_setup_component(hass, LAWN_MOWER_DOMAIN, {})
    await hass.data[DATA_COMPONENT].async_add_entities([entity])
    await hass.async_block_till_done()


async def test_services_drive_the_command_dp(hass: HomeAssistant) -> None:
    """The lawn_mower services reach the device through the entity."""
    entity, device = _make_entity(hass)
    await connect(entity._coordinator)  # pylint: disable=protected-access
    add_dp(device, STATUS_DP, TuyaBLEDataPointType.DT_ENUM, STATUS_INDEX["PAUSED"])
    sent = _record_commands(device)
    await _add_to_component(hass, entity)
    state = hass.states.get(entity.entity_id)
    assert state is not None
    assert state.state == LawnMowerActivity.PAUSED

    for service in (SERVICE_START_MOWING, SERVICE_PAUSE, SERVICE_STOP, SERVICE_DOCK):
        await hass.services.async_call(
            LAWN_MOWER_DOMAIN,
            service,
            {"entity_id": entity.entity_id},
            blocking=True,
        )
    assert sent == [
        CONTINUE_WORK,
        PAUSE_WORK,
        CANCEL_WORK,
        CANCEL_WORK,
        START_RETURN_STATION,
    ]


async def test_services_skip_an_unavailable_mower(hass: HomeAssistant) -> None:
    """A disconnected mower is unavailable and receives no commands."""
    entity, device = _make_entity(hass)
    sent = _record_commands(device)
    await _add_to_component(hass, entity)
    state = hass.states.get(entity.entity_id)
    assert state is not None
    assert state.state == "unavailable"
    await hass.services.async_call(
        LAWN_MOWER_DOMAIN,
        SERVICE_START_MOWING,
        {"entity_id": entity.entity_id},
        blocking=True,
    )
    assert not sent


async def test_coordinator_update_refreshes_the_activity(hass: HomeAssistant) -> None:
    """A status report published by the coordinator updates the state."""
    entity, device = _make_entity(hass)
    await connect(entity._coordinator)  # pylint: disable=protected-access
    await _add_to_component(hass, entity)
    add_dp(device, STATUS_DP, TuyaBLEDataPointType.DT_ENUM, STATUS_INDEX["MOWING"])
    entity._handle_coordinator_update()  # pylint: disable=protected-access
    state = hass.states.get(entity.entity_id)
    assert state is not None
    assert state.state == LawnMowerActivity.MOWING


@pytest.mark.parametrize("reported", [False, True])
async def test_setup_entry_waits_for_the_status_dp(
    hass: HomeAssistant, reported: bool
) -> None:
    """A mower that is not force-added appears once its status is reported."""
    device, coordinator, _ = build_context(hass)
    if reported:
        add_dp(device, STATUS_DP, TuyaBLEDataPointType.DT_ENUM, 0)
    mapping = replace(_parkside_mapping(), force_add=False)
    data = SimpleNamespace(
        device=device,
        coordinator=coordinator,
        product=get_product_info_by_ids("gcj", "9hdajpiw"),
    )
    entry = SimpleNamespace(entry_id="mower")
    hass.data[DOMAIN] = {entry.entry_id: data}
    added: list[Any] = []
    with patch.object(lawn_mower, "get_mapping_by_device", return_value=[mapping]):
        await lawn_mower.async_setup_entry(hass, entry, added.extend)  # type: ignore[arg-type]
    assert len(added) == int(reported)
