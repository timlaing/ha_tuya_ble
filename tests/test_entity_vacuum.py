"""Unit tests for the Tuya BLE vacuum platform."""
# pylint: disable=protected-access
# The tests deliberately set up device/coordinator internals as test setup
# state, which is sanctioned by the project's test conventions.

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from homeassistant.components.vacuum import StateVacuumEntityDescription
from homeassistant.components.vacuum.const import (
    DOMAIN as VACUUM_DOMAIN,
)
from homeassistant.components.vacuum.const import (
    VacuumActivity,
    VacuumEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceNotSupported, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant.common import (
    MockConfigEntry,
    MockEntityPlatform,
)

from custom_components.tuya_ble import vacuum
from custom_components.tuya_ble.const import DOMAIN
from custom_components.tuya_ble.device_registry import (
    DeviceRegistryError,
    EntityDescriptor,
    _parse_entity,
    get_mapped_dp_ids,
)
from custom_components.tuya_ble.devices import TuyaBLECoordinator, TuyaBLEProductInfo
from custom_components.tuya_ble.tuya_ble import TuyaBLEDataPointType, TuyaBLEDevice
from custom_components.tuya_ble.vacuum import TuyaBLEVacuum, TuyaBLEVacuumMapping
from tests.conftest import add_dp, build_context, connect, make_credentials

CATEGORY = "ccjqr"
PRODUCT_ID = "pnxl0r3l"
VACUUM_KEY = "window_cleaner"
WINDOW_CLEANER = SimpleNamespace(category=CATEGORY, product_id=PRODUCT_ID)


def _window_cleaner_mapping() -> TuyaBLEVacuumMapping:
    """Return the vacuum mapping the CHYW200 descriptor produces."""
    mappings = vacuum.get_mapping_by_device(cast(TuyaBLEDevice, WINDOW_CLEANER))
    assert len(mappings) == 1
    return mappings[0]


def _make_entity(
    hass: HomeAssistant,
    device: TuyaBLEDevice,
    coordinator: TuyaBLECoordinator,
    product: TuyaBLEProductInfo,
    vacuum_mapping: TuyaBLEVacuumMapping | None = None,
) -> TuyaBLEVacuum:
    """Build a vacuum entity, defaulting to the window cleaner mapping."""
    entity = TuyaBLEVacuum(
        hass,
        coordinator,
        device,
        product,
        vacuum_mapping or _window_cleaner_mapping(),
    )
    entity.hass = hass
    return entity


def _plain_mapping(**kwargs: Any) -> TuyaBLEVacuumMapping:
    """Return a start/stop-only vacuum mapping on dp 1."""
    return TuyaBLEVacuumMapping(
        dp_id=1, description=StateVacuumEntityDescription(key="vac"), **kwargs
    )


# --- Descriptor and mapping ----------------------------------------------------


def test_window_cleaner_descriptor_builds_vacuum_mapping() -> None:
    """The CHYW200 descriptor maps start/stop, status and cleaning mode."""
    built = _window_cleaner_mapping()

    assert built.dp_id == 1
    assert built.description.key == VACUUM_KEY
    assert built.status_dp_id == 4
    assert built.mode_dp_id == 2
    assert built.fan_speeds == ["smart", "z_mode", "n_mode", "edge", "spot"]
    assert list(built.status_activities) == [
        "standby",
        "cleaning",
        "smart_clean",
        "z_clean",
        "n_clean",
        "edge_clean",
        "spot_clean",
        "pause",
        "stop",
        "charge",
    ]
    assert built.status_activities["charge"] is VacuumActivity.DOCKED


def test_window_cleaner_maps_every_reported_datapoint() -> None:
    """Every data point the reporter documented has an entity."""
    assert get_mapped_dp_ids(CATEGORY, PRODUCT_ID) == frozenset({
        1,
        2,
        3,
        4,
        6,
        11,
        103,
    })


def test_window_cleaner_is_not_registered_under_wrong_category() -> None:
    """The fork's original ``cxjmb`` category was corrected to ``ccjqr``."""
    wrong = SimpleNamespace(category="cxjmb", product_id=PRODUCT_ID)
    assert vacuum.get_mapping_by_device(cast(TuyaBLEDevice, wrong)) == []


def test_unknown_device_has_no_vacuum() -> None:
    """A device without a vacuum descriptor gets no mapping."""
    unknown = SimpleNamespace(category="nope", product_id="nope")
    assert vacuum.get_mapping_by_device(cast(TuyaBLEDevice, unknown)) == []


def test_known_category_unknown_product_without_default() -> None:
    """An unknown product in a vacuum category has no default mapping."""
    other = SimpleNamespace(category=CATEGORY, product_id="unknown")
    assert vacuum.get_mapping_by_device(cast(TuyaBLEDevice, other)) == []


def test_build_vacuum_mapping_minimal_descriptor() -> None:
    """A descriptor with only a dp_id gets no status or fan speed."""
    built = vacuum._build_vacuum_mapping(
        EntityDescriptor(platform="vacuum", dp_id=7, name="Robot")
    )
    assert built.description.key == "7"
    assert built.description.name == "Robot"
    assert built.status_dp_id is None
    assert not built.status_activities
    assert built.mode_dp_id is None
    assert not built.fan_speeds
    assert built.dp_type is None


@pytest.mark.parametrize(
    ("enabled", "expected"), [(None, True), (True, True), (False, False)]
)
def test_build_vacuum_mapping_enabled_by_default(
    enabled: bool | None, expected: bool
) -> None:
    """``enabled_by_default: false`` hides the vacuum; otherwise it is enabled."""
    built = vacuum._build_vacuum_mapping(
        EntityDescriptor(platform="vacuum", dp_id=1, enabled_by_default=enabled)
    )
    assert built.description.entity_registry_enabled_default is expected


def test_build_vacuum_mapping_dp_type() -> None:
    """A declared dp_type is resolved for the setup has_id gate."""
    built = vacuum._build_vacuum_mapping(
        EntityDescriptor(platform="vacuum", dp_id=1, dp_type=1)
    )
    assert built.dp_type is TuyaBLEDataPointType.DT_BOOL


def test_parse_vacuum_entity_requires_dp_id() -> None:
    """The start/stop data point is mandatory."""
    with pytest.raises(DeviceRegistryError, match="missing 'dp_id'"):
        _parse_entity("vacuum", {"translation_key": "robot"})


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"status_activities": ["idle"]}, "status_activities must be a mapping"),
        (
            {"status_dp_id": 4, "status_activities": {"run": "running"}},
            "unknown activity 'running' for status 'run'",
        ),
        ({"status_activities": {"run": "cleaning"}}, "without status_dp_id"),
        ({"mode_dp_id": 2, "fan_speeds": "smart"}, "list of strings"),
        ({"mode_dp_id": 2, "fan_speeds": [1]}, "list of strings"),
        ({"mode_dp_id": 2}, "both mode_dp_id and fan_speeds"),
        ({"fan_speeds": ["smart"]}, "both mode_dp_id and fan_speeds"),
    ],
)
def test_parse_vacuum_entity_rejects_invalid_fields(
    raw: dict[str, Any], message: str
) -> None:
    """Invalid vacuum fields fail at descriptor load time."""
    with pytest.raises(DeviceRegistryError, match=message):
        _parse_entity("vacuum", {"dp_id": 1, "translation_key": "robot", **raw})


def test_parse_vacuum_entity_accepts_valid_fields() -> None:
    """Valid vacuum fields are kept in ``extra``."""
    parsed = _parse_entity(
        "vacuum",
        {
            "dp_id": 1,
            "status_dp_id": 4,
            "status_activities": {"standby": "idle"},
            "mode_dp_id": 2,
            "fan_speeds": ["smart"],
        },
    )
    assert parsed.extra["status_activities"] == {"standby": "idle"}
    assert parsed.extra["fan_speeds"] == ["smart"]


# --- Entity state --------------------------------------------------------------


async def test_supported_features_window_cleaner(hass: HomeAssistant) -> None:
    """Only start, stop, state and fan speed are offered, never pause or dock."""
    entity = _make_entity(hass, *build_context(hass))
    features = entity.supported_features

    assert features == (
        VacuumEntityFeature.STATE
        | VacuumEntityFeature.START
        | VacuumEntityFeature.STOP
        | VacuumEntityFeature.FAN_SPEED
    )
    assert VacuumEntityFeature.PAUSE not in features
    assert VacuumEntityFeature.RETURN_HOME not in features
    assert entity.fan_speed_list == ["smart", "z_mode", "n_mode", "edge", "spot"]


async def test_supported_features_without_mode(hass: HomeAssistant) -> None:
    """A vacuum without a mode data point has no fan speed feature."""
    entity = _make_entity(hass, *build_context(hass), _plain_mapping())
    assert VacuumEntityFeature.FAN_SPEED not in entity.supported_features
    assert not entity.fan_speed_list
    assert entity.fan_speed is None


async def test_unique_id_and_legacy_key(hass: HomeAssistant) -> None:
    """The unique id follows the window_cleaner translation key."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    assert entity.unique_id == f"{device.device_id}-{VACUUM_KEY}"
    assert entity.translation_key == VACUUM_KEY


async def test_unique_id_adopts_fork_vacuum_key(hass: HomeAssistant) -> None:
    """An entity registered by the ha-tuya-ble fork keeps its unique id."""
    device, coordinator, product = build_context(hass)
    device._device_info = make_credentials(category=CATEGORY, product_id=PRODUCT_ID)
    er.async_get(hass).async_get_or_create(
        VACUUM_DOMAIN, DOMAIN, f"{device.device_id}-vacuum"
    )

    entity = _make_entity(hass, device, coordinator, product)

    assert entity.unique_id == f"{device.device_id}-vacuum"


@pytest.mark.parametrize(
    ("index", "activity"),
    [
        (0, VacuumActivity.IDLE),
        (1, VacuumActivity.CLEANING),
        (3, VacuumActivity.CLEANING),
        (7, VacuumActivity.PAUSED),
        (8, VacuumActivity.IDLE),
        (9, VacuumActivity.DOCKED),
        (10, None),
        (-1, None),
    ],
)
async def test_activity_from_enum_status(
    hass: HomeAssistant, index: int, activity: VacuumActivity | None
) -> None:
    """The enum index of the status data point selects the activity."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    add_dp(device, 1, TuyaBLEDataPointType.DT_BOOL, True)
    add_dp(device, 4, TuyaBLEDataPointType.DT_ENUM, index)
    assert entity.activity is activity


@pytest.mark.parametrize(
    ("status", "activity"),
    [("charge", VacuumActivity.DOCKED), ("mystery", None)],
)
async def test_activity_from_string_status(
    hass: HomeAssistant, status: str, activity: VacuumActivity | None
) -> None:
    """A string status value is looked up by name."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    add_dp(device, 4, TuyaBLEDataPointType.DT_STRING, status)
    assert entity.activity is activity


@pytest.mark.parametrize(
    ("running", "activity"),
    [(True, VacuumActivity.CLEANING), (False, VacuumActivity.IDLE)],
)
async def test_activity_falls_back_to_switch(
    hass: HomeAssistant, running: bool, activity: VacuumActivity
) -> None:
    """Before a status report, the start switch decides cleaning or idle."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    add_dp(device, 1, TuyaBLEDataPointType.DT_BOOL, running)
    assert entity.activity is activity


async def test_activity_without_status_dp(hass: HomeAssistant) -> None:
    """A vacuum with no status data point follows the start switch."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product, _plain_mapping())
    add_dp(device, 1, TuyaBLEDataPointType.DT_BOOL, True)
    assert entity.activity is VacuumActivity.CLEANING


async def test_activity_unknown_before_any_report(hass: HomeAssistant) -> None:
    """With no data point reported the activity is unknown."""
    entity = _make_entity(hass, *build_context(hass))
    assert entity.activity is None


@pytest.mark.parametrize(
    ("dp_type", "value", "expected"),
    [
        (TuyaBLEDataPointType.DT_ENUM, 0, "smart"),
        (TuyaBLEDataPointType.DT_ENUM, 4, "spot"),
        (TuyaBLEDataPointType.DT_ENUM, 5, None),
        (TuyaBLEDataPointType.DT_ENUM, -1, None),
        (TuyaBLEDataPointType.DT_STRING, "edge", "edge"),
        (TuyaBLEDataPointType.DT_STRING, "turbo", None),
        (TuyaBLEDataPointType.DT_RAW, b"\x01", None),
    ],
)
async def test_fan_speed(
    hass: HomeAssistant,
    dp_type: TuyaBLEDataPointType,
    value: Any,
    expected: str | None,
) -> None:
    """The mode data point is reported as the fan speed."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    add_dp(device, 2, dp_type, value)
    assert entity.fan_speed == expected


async def test_fan_speed_unknown_before_report(hass: HomeAssistant) -> None:
    """Without a mode report the fan speed is unknown."""
    entity = _make_entity(hass, *build_context(hass))
    assert entity.fan_speed is None


async def test_available_follows_coordinator(hass: HomeAssistant) -> None:
    """The entity is unavailable until the device connects."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    assert entity.available is False
    await connect(coordinator)
    assert entity.available is True


# --- Entity actions ------------------------------------------------------------


async def test_start_and_stop_write_switch(hass: HomeAssistant) -> None:
    """Start and stop write the bool start/stop data point."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)

    await entity.async_start()
    await hass.async_block_till_done()
    datapoint = device.datapoints[1]
    assert datapoint is not None
    assert datapoint.value is True
    assert datapoint.dp_type is TuyaBLEDataPointType.DT_BOOL

    await entity.async_stop()
    await hass.async_block_till_done()
    datapoint = device.datapoints[1]
    assert datapoint is not None
    assert datapoint.value is False


async def test_set_fan_speed_writes_mode_only(hass: HomeAssistant) -> None:
    """Choosing a mode writes its enum index and does not start cleaning."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)

    await entity.async_set_fan_speed("edge")
    await hass.async_block_till_done()

    datapoint = device.datapoints[2]
    assert datapoint is not None
    assert datapoint.value == 3
    assert datapoint.dp_type is TuyaBLEDataPointType.DT_ENUM
    assert device.datapoints[1] is None


async def test_set_fan_speed_writes_name_to_string_mode(hass: HomeAssistant) -> None:
    """A mode reported as a string is written back as the mode name."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    add_dp(device, 2, TuyaBLEDataPointType.DT_STRING, "smart")

    await entity.async_set_fan_speed("edge")
    await hass.async_block_till_done()

    datapoint = device.datapoints[2]
    assert datapoint is not None
    assert datapoint.value == "edge"
    assert datapoint.dp_type is TuyaBLEDataPointType.DT_STRING
    assert entity.fan_speed == "edge"


async def test_set_fan_speed_without_mode_dp_sends_nothing(hass: HomeAssistant) -> None:
    """A mapping with fan speeds but no mode data point writes nothing."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(
        hass, device, coordinator, product, _plain_mapping(fan_speeds=["smart"])
    )

    await entity.async_set_fan_speed("smart")
    await hass.async_block_till_done()

    assert device.datapoints[1] is None


async def test_set_fan_speed_rejects_unknown_mode(hass: HomeAssistant) -> None:
    """An unsupported mode is rejected without writing anything."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)

    with pytest.raises(ServiceValidationError) as err:
        await entity.async_set_fan_speed("turbo")

    assert err.value.translation_key == "invalid_fan_speed"
    assert err.value.translation_placeholders == {
        "fan_speed": "turbo",
        "fan_speeds": "smart, z_mode, n_mode, edge, spot",
    }
    assert device.datapoints[2] is None


# --- Entity services -----------------------------------------------------------


async def _add_to_platform(hass: HomeAssistant, entity: TuyaBLEVacuum) -> None:
    """Register the entity with Home Assistant so its services can be called."""
    assert await async_setup_component(hass, VACUUM_DOMAIN, {})
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    platform = MockEntityPlatform(hass, domain=VACUUM_DOMAIN, platform_name=DOMAIN)
    platform.config_entry = entry
    await platform.async_add_entities([entity])
    await hass.async_block_till_done()


async def test_services_drive_the_device(hass: HomeAssistant) -> None:
    """The vacuum start, stop and set_fan_speed services reach the device."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    await connect(coordinator)
    await _add_to_platform(hass, entity)
    target = {"entity_id": entity.entity_id}

    await hass.services.async_call(VACUUM_DOMAIN, "start", target, blocking=True)
    await hass.async_block_till_done()
    datapoint = device.datapoints[1]
    assert datapoint is not None
    assert datapoint.value is True

    await hass.services.async_call(
        VACUUM_DOMAIN, "set_fan_speed", {**target, "fan_speed": "spot"}, blocking=True
    )
    await hass.async_block_till_done()
    datapoint = device.datapoints[2]
    assert datapoint is not None
    assert datapoint.value == 4

    await hass.services.async_call(VACUUM_DOMAIN, "stop", target, blocking=True)
    await hass.async_block_till_done()
    datapoint = device.datapoints[1]
    assert datapoint is not None
    assert datapoint.value is False

    # The device reports it has stopped; the coordinator notifies the entity.
    add_dp(device, 4, TuyaBLEDataPointType.DT_ENUM, 8)
    coordinator.async_update_listeners()
    state = hass.states.get(entity.entity_id)
    assert state is not None
    assert state.state == VacuumActivity.IDLE
    assert state.attributes["fan_speed"] == "spot"


@pytest.mark.parametrize("service", ["pause", "return_to_base"])
async def test_unsupported_services_are_rejected(
    hass: HomeAssistant, service: str
) -> None:
    """Pause and return-to-base are not offered by the window cleaner."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    await connect(coordinator)
    await _add_to_platform(hass, entity)

    with pytest.raises(ServiceNotSupported):
        await hass.services.async_call(
            VACUUM_DOMAIN, service, {"entity_id": entity.entity_id}, blocking=True
        )
    assert device.datapoints[1] is None


async def test_services_skip_unavailable_device(hass: HomeAssistant) -> None:
    """A disconnected device is unavailable and receives no command."""
    device, coordinator, product = build_context(hass)
    entity = _make_entity(hass, device, coordinator, product)
    await _add_to_platform(hass, entity)

    state = hass.states.get(entity.entity_id)
    assert state is not None
    assert state.state == "unavailable"
    await hass.services.async_call(
        VACUUM_DOMAIN, "start", {"entity_id": entity.entity_id}, blocking=True
    )
    await hass.async_block_till_done()
    assert device.datapoints[1] is None


# --- Platform setup ------------------------------------------------------------


async def test_setup_entry_adds_window_cleaner(hass: HomeAssistant) -> None:
    """A CHYW200 config entry gets exactly one vacuum entity."""
    device, coordinator, product = build_context(hass)
    device._device_info = make_credentials(category=CATEGORY, product_id=PRODUCT_ID)
    entry = MockConfigEntry(domain=DOMAIN)
    hass.data[DOMAIN] = {
        entry.entry_id: SimpleNamespace(
            device=device, coordinator=coordinator, product=product
        )
    }
    added: list[TuyaBLEVacuum] = []

    await vacuum.async_setup_entry(hass, entry, cast(Any, added.extend))

    assert len(added) == 1
    assert added[0].unique_id == f"{device.device_id}-{VACUUM_KEY}"


@pytest.mark.parametrize(("reported", "count"), [(False, 0), (True, 1)])
async def test_setup_entry_waits_for_datapoint_without_force_add(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    reported: bool,
    count: int,
) -> None:
    """With force_add off the vacuum appears once the device reports dp 1."""
    device, coordinator, product = build_context(hass)
    if reported:
        add_dp(device, 1, TuyaBLEDataPointType.DT_BOOL, False)
    monkeypatch.setattr(
        vacuum,
        "get_mapping_by_device",
        lambda _device: [_plain_mapping(force_add=False)],
    )
    entry = MockConfigEntry(domain=DOMAIN)
    hass.data[DOMAIN] = {
        entry.entry_id: SimpleNamespace(
            device=device, coordinator=coordinator, product=product
        )
    }
    added: list[TuyaBLEVacuum] = []

    await vacuum.async_setup_entry(hass, entry, cast(Any, added.extend))

    assert len(added) == count
