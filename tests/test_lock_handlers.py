"""Shared smart-lock handlers, driven through fixture descriptors.

The fixture products below are test-only: they reproduce the lock semantics
ported from ha-tuya-ble/ha_tuya_ble#315, #324, #327, #339, #353 and #360 with
made-up product ids, so no shipping descriptor is involved. Each is loaded
through ``DeviceRegistry.load`` and built into entities by the platform
modules, then driven through the entity service methods.
"""

# pylint: disable=protected-access
from __future__ import annotations

import base64
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

from bleak.exc import BleakError
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.lock.const import LockEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.tuya_ble import button, device_registry, lock
from custom_components.tuya_ble.device_registry import (
    DeviceRegistry,
    DeviceRegistryError,
    get_mapped_dp_ids,
)
from custom_components.tuya_ble.devices import (
    TuyaBLECoordinator,
    get_product_info_by_ids,
)
from custom_components.tuya_ble.tuya_ble import TuyaBLEDataPointType
from custom_components.tuya_ble.tuya_ble.manager import TuyaBLEDeviceCredentials
from tests.conftest import EntityDevice, FakeBLEManager, add_dp, make_status_device

# member id 1 | FFFF | check code | 01 | time | 0000 (synthetic)
MEMBER1_CHECK = base64.b64encode(
    b"\x00\x01\xff\xff12345678\x01\x01\x02\x03\x04\x00\x00"
).decode()
# The same check issued to app member 3 (ha-tuya-ble/ha_tuya_ble#360).
MEMBER3_CHECK = base64.b64encode(
    b"\x00\x03\xff\xff87654321\x01\x01\x02\x03\x04\x00\x00"
).decode()
NOW = datetime.fromtimestamp(0x6AD8EB64, tz=UTC)

FIXTURES = {
    # Lever lock: passage mode holds it open, DP 6 opens it momentarily.
    "jtmspro_testpass.yaml": """
category: jtmspro
product_id: testpass
entities:
  lock:
    - dp_id: 47
      translation_key: lock
      passage_dp_id: 33
      manual_lock_dp_id: 46
      open_dp_id: 6
      handlers:
        read: lock.state.passage_or_bolt
        write: lock.command.manual_lock_or_passage
        open: lock.command.momentary_open
  button:
    - dp_id: 6
      translation_key: bluetooth_unlock
      press_value: true
  switch:
    - dp_id: 33
      translation_key: free_passage_mode
""",
    # Handle lock: DP 33 is automatic locking, DP 71 the timestamped open.
    "jtmspro_testauto.yaml": """
category: jtmspro
product_id: testauto
sensitive_dp_ids: [71]
entities:
  lock:
    - dp_id: 33
      translation_key: lock
      open_dp_id: 71
      handlers:
        read: lock.state.automatic_lock
        write: lock.command.automatic_lock
        open: lock.command.timestamped_open
""",
    # Knob lock: manual_lock locks, an empty DP 71 write unlocks.
    "jtmspro_testraw.yaml": """
category: jtmspro
product_id: testraw
entities:
  lock:
    - dp_id: 47
      translation_key: lock
      manual_lock_dp_id: 46
      unlock_dp_id: 71
      handlers:
        write: lock.command.manual_lock_or_raw_unlock
  button:
    - dp_id: 71
      translation_key: bluetooth_unlock
      handlers:
        write: lock.command.press_raw_unlock
""",
    # Drawer lock: no truthful lock state, only a timestamped unlock button.
    "jtmspro_testbutton.yaml": """
category: jtmspro
product_id: testbutton
sensitive_dp_ids: [71]
entities:
  button:
    - dp_id: 71
      translation_key: bluetooth_unlock
      handlers:
        write: lock.command.press_timestamped_unlock
""",
}


def _load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **files: str) -> Any:
    """Load *files* as the descriptor registry and rebuild the platform maps."""
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    monkeypatch.setattr(device_registry, "_DEVICES_DIR", tmp_path)
    registry = DeviceRegistry.load()
    for module in (lock, button):
        monkeypatch.setattr(module, "get_registry", lambda: registry)
        monkeypatch.setattr(module, "mapping", module._build_mapping())
    monkeypatch.setattr(device_registry, "get_registry", lambda: registry)
    monkeypatch.setattr(
        "custom_components.tuya_ble.entity.get_registry", lambda: registry
    )
    return registry


@pytest.fixture(name="registry")
def fixture_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Load every fixture descriptor."""
    return _load(tmp_path, monkeypatch, **FIXTURES)


def _device(
    hass: HomeAssistant, product_id: str, ble_unlock_check: str | None = None
) -> tuple[EntityDevice, TuyaBLECoordinator]:
    """Return a device double for a fixture product, and its coordinator.

    Its manager hands out the credentials, as the offline manager does at
    runtime with the ones stored on the config entry.
    """
    credentials = TuyaBLEDeviceCredentials(
        uuid="u",
        local_key="k",
        device_id="dev",
        category="jtmspro",
        product_id=product_id,
        device_name="n",
        product_model="m",
        product_name="pm",
        ble_unlock_check=ble_unlock_check,
    )
    device = make_status_device(FakeBLEManager(credentials), device_cls=EntityDevice)
    device._device_info = credentials
    return device, TuyaBLECoordinator(hass, device)


def _lock(
    hass: HomeAssistant, product_id: str, ble_unlock_check: str | None = None
) -> tuple[lock.TuyaBLELock, EntityDevice]:
    """Build the fixture product's lock entity."""
    device, coordinator = _device(hass, product_id, ble_unlock_check)
    (mapping,) = lock.get_mapping_by_device(device)
    product = get_product_info_by_ids("jtmspro", product_id)
    entity = lock.TuyaBLELock(hass, coordinator, device, product, mapping)
    entity.hass = hass
    return entity, device


def _button(
    hass: HomeAssistant, product_id: str, ble_unlock_check: str | None = None
) -> tuple[button.TuyaBLEButton, EntityDevice]:
    """Build the fixture product's button entity."""
    device, coordinator = _device(hass, product_id, ble_unlock_check)
    (mapping,) = button.get_mapping_by_device(device)
    product = get_product_info_by_ids("jtmspro", product_id)
    entity = button.TuyaBLEButton(hass, coordinator, device, product, mapping)
    entity.hass = hass
    return entity, device


def _value(device: EntityDevice, dp_id: int) -> Any:
    datapoint = device.datapoints[dp_id]
    assert datapoint is not None
    return datapoint.value


def _command(member: bytes, code: bytes) -> bytes:
    return b"\xff\xff" + member + code + bytes.fromhex("016ad8eb640001")


# --- passage mode, bolt state and momentary open (#324, #327) ---------------


@pytest.mark.usefixtures("registry")
async def test_passage_lock_state_follows_passage_and_bolt(
    hass: HomeAssistant,
) -> None:
    """Unknown until reported, then unlocked while passage or the bolt is on."""
    entity, device = _lock(hass, "testpass")
    assert entity.is_locked is None
    add_dp(device, 33, TuyaBLEDataPointType.DT_BOOL, True)
    assert entity.is_locked is False
    add_dp(device, 33, TuyaBLEDataPointType.DT_BOOL, False)
    assert entity.is_locked is True
    add_dp(device, 47, TuyaBLEDataPointType.DT_BOOL, True)
    assert entity.is_locked is False
    add_dp(device, 47, TuyaBLEDataPointType.DT_BOOL, False)
    assert entity.is_locked is True


@pytest.mark.usefixtures("registry")
async def test_passage_lock_bolt_report_alone(hass: HomeAssistant) -> None:
    """Without a passage report the bolt report alone decides."""
    entity, device = _lock(hass, "testpass")
    add_dp(device, 47, TuyaBLEDataPointType.DT_BOOL, False)
    assert entity.is_locked is True
    add_dp(device, 47, TuyaBLEDataPointType.DT_BOOL, True)
    assert entity.is_locked is False


@pytest.mark.usefixtures("registry")
async def test_lock_state_ignores_unconfirmed_writes(hass: HomeAssistant) -> None:
    """A command the lock has not reported back does not change the state."""
    passage, passage_device = _lock(hass, "testpass")
    await passage.async_unlock()
    assert _value(passage_device, 33) is True
    assert passage.is_locked is None
    add_dp(passage_device, 47, TuyaBLEDataPointType.DT_BOOL, False)
    assert passage.is_locked is True

    automatic, automatic_device = _lock(hass, "testauto")
    await automatic.async_lock()
    assert _value(automatic_device, 33) is True
    assert automatic.is_locked is None
    add_dp(automatic_device, 33, TuyaBLEDataPointType.DT_BOOL, True)
    await automatic.async_unlock()
    assert automatic.is_locked is True


@pytest.mark.usefixtures("registry")
async def test_passage_lock_commands(hass: HomeAssistant) -> None:
    """Lock writes manual_lock, unlock holds passage mode, open is momentary."""
    entity, device = _lock(hass, "testpass")
    assert entity.supported_features == LockEntityFeature.OPEN

    await entity.async_lock()
    assert device.sent == [[46]]
    assert _value(device, 46) is True

    await entity.async_unlock()
    assert device.sent[-1] == [33]
    assert _value(device, 33) is True

    for _ in range(2):
        await entity.async_open()
        assert device.sent[-1] == [6]
    assert _value(device, 6) is True
    assert device.datapoints[47] is None


@pytest.mark.usefixtures("registry")
async def test_momentary_button_never_toggles_off(hass: HomeAssistant) -> None:
    """Each press sends the trigger on, even when it is already on."""
    entity, device = _button(hass, "testpass")
    for _ in range(2):
        await entity.async_press()
        await hass.async_block_till_done()
    assert device.sent == [[6], [6]]
    assert _value(device, 6) is True


# --- automatic locking and timestamped DP 71 (#353, #360) -------------------


@pytest.mark.usefixtures("registry")
async def test_automatic_lock_state_and_commands(hass: HomeAssistant) -> None:
    """DP 33 on is locked; lock/unlock toggle automatic locking."""
    entity, device = _lock(hass, "testauto")
    assert entity.is_locked is None
    add_dp(device, 33, TuyaBLEDataPointType.DT_BOOL, True)
    assert entity.is_locked is True
    add_dp(device, 33, TuyaBLEDataPointType.DT_BOOL, False)
    assert entity.is_locked is False

    await entity.async_lock()
    assert _value(device, 33) is True
    await entity.async_unlock()
    assert _value(device, 33) is False
    assert device.sent == [[33], [33]]


@pytest.mark.usefixtures("registry")
@pytest.mark.parametrize(
    ("check", "expected"),
    [
        (MEMBER1_CHECK, _command(b"\x00\x01", b"12345678")),
        (MEMBER3_CHECK, _command(b"\x00\x03", b"87654321")),
    ],
)
async def test_timestamped_open(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, check: str, expected: bytes
) -> None:
    """Open writes the timestamped command for the check's own member id."""
    freezer.move_to(NOW)
    entity, device = _lock(hass, "testauto", check)
    assert entity.supported_features == LockEntityFeature.OPEN
    await entity.async_open()
    assert device.sent == [[71]]
    datapoint = device.datapoints[71]
    assert datapoint is not None
    assert datapoint.dp_type == TuyaBLEDataPointType.DT_RAW
    assert datapoint.value == expected


@pytest.mark.usefixtures("registry")
@pytest.mark.parametrize(
    ("check", "key"),
    [
        (None, "ble_unlock_check_missing"),
        ("", "ble_unlock_check_missing"),
        ("not-base64!", "ble_unlock_check_invalid"),
        (
            base64.b64encode(
                b"\x00\x00" + base64.b64decode(MEMBER1_CHECK)[2:]
            ).decode(),
            "ble_unlock_check_invalid",
        ),
    ],
)
async def test_timestamped_open_rejects_bad_check(
    hass: HomeAssistant, check: str | None, key: str
) -> None:
    """A missing or malformed check fails the call before anything is written."""
    entity, device = _lock(hass, "testauto", check)
    with pytest.raises(HomeAssistantError) as err:
        await entity.async_open()
    assert err.value.translation_key == key
    assert device.datapoints[71] is None
    assert not device.sent
    if check:
        assert check not in str(err.value)


@pytest.mark.usefixtures("registry")
async def test_timestamped_button(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """A lock with no lock entity unlocks from a button with the same command."""
    freezer.move_to(NOW)
    assert lock.get_mapping_by_device(_device(hass, "testbutton")[0]) == []
    entity, device = _button(hass, "testbutton", MEMBER3_CHECK)
    await entity.async_press()
    assert device.sent == [[71]]
    assert _value(device, 71) == _command(b"\x00\x03", b"87654321")


@pytest.mark.usefixtures("registry")
async def test_timestamped_open_without_credentials(hass: HomeAssistant) -> None:
    """A manager with no credentials counts as a missing check."""
    entity, device = _lock(hass, "testauto", MEMBER1_CHECK)
    device.device_manager = FakeBLEManager(None)
    with pytest.raises(HomeAssistantError) as err:
        await entity.async_open()
    assert err.value.translation_key == "ble_unlock_check_missing"
    assert not device.sent


@pytest.mark.usefixtures("registry")
async def test_timestamped_button_without_check(hass: HomeAssistant) -> None:
    """The button raises and writes nothing without a stored check."""
    entity, device = _button(hass, "testbutton")
    with pytest.raises(HomeAssistantError) as err:
        await entity.async_press()
    assert err.value.translation_key == "ble_unlock_check_missing"
    assert device.datapoints[71] is None


# --- manual lock with a raw unlock trigger (#315, #339) ---------------------


@pytest.mark.usefixtures("registry")
async def test_raw_unlock_lock(hass: HomeAssistant) -> None:
    """Lock writes manual_lock on, unlock an empty raw DP 71; no open feature."""
    entity, device = _lock(hass, "testraw")
    assert entity.supported_features == 0
    add_dp(device, 47, TuyaBLEDataPointType.DT_BOOL, False)
    assert entity.is_locked is True

    await entity.async_lock()
    assert device.sent == [[46]]
    assert _value(device, 46) is True

    await entity.async_unlock()
    assert device.sent[-1] == [71]
    datapoint = device.datapoints[71]
    assert datapoint is not None
    assert datapoint.dp_type == TuyaBLEDataPointType.DT_RAW
    assert datapoint.value == b""
    assert _value(device, 46) is True

    await entity.async_open()
    assert len(device.sent) == 2


@pytest.mark.usefixtures("registry")
async def test_raw_unlock_button(hass: HomeAssistant) -> None:
    """The unlock button writes an empty raw DP 71 on every press."""
    entity, device = _button(hass, "testraw")
    await entity.async_press()
    await entity.async_press()
    assert device.sent == [[71], [71]]
    assert _value(device, 71) == b""


@pytest.mark.usefixtures("registry")
async def test_handler_press_failure_is_reported(hass: HomeAssistant) -> None:
    """A BLE failure inside a write handler fails the press, value rolled back."""
    entity, device = _button(hass, "testraw")
    with (
        patch.object(device, "send_datapoints", side_effect=BleakError("x")),
        pytest.raises(HomeAssistantError) as err,
    ):
        await entity.async_press()
    assert err.value.translation_key == "button_press_failed"


# --- registry ---------------------------------------------------------------


def test_fixture_registry_maps_aux_dp_ids_and_sensitive_ids(registry: Any) -> None:
    """Handler data points count as mapped; sensitive ids reach product info."""
    assert {6, 33, 46, 47} <= get_mapped_dp_ids("jtmspro", "testpass")
    assert get_mapped_dp_ids("jtmspro", "testauto") >= {33, 71}
    assert get_product_info_by_ids("jtmspro", "testauto").sensitive_dp_ids == {71}
    assert get_product_info_by_ids("jtmspro", "testpass").sensitive_dp_ids == set()
    assert registry.get("jtmspro", "testbutton").sensitive_dp_ids == {71}


def test_unknown_product_has_no_sensitive_ids() -> None:
    """A product without a descriptor marks nothing sensitive."""
    assert get_product_info_by_ids("jtmspro", "missing").sensitive_dp_ids == set()


@pytest.mark.parametrize("value", ["33", 0, True, None])
def test_handler_without_its_dp_id_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: Any
) -> None:
    """A handler's declared ``*_dp_id`` field must hold a data point id."""
    passage = "" if value is None else f"      passage_dp_id: {value!r}\n"
    passage = passage.replace("'", '"')
    text = (
        "category: jtmspro\nproduct_id: testbad\nentities:\n  lock:\n"
        "    - dp_id: 47\n      translation_key: lock\n" + passage + "      handlers:\n"
        "        read: lock.state.passage_or_bolt\n"
    )
    with pytest.raises(DeviceRegistryError, match="passage_dp_id"):
        _load(tmp_path, monkeypatch, **{"jtmspro_testbad.yaml": text})


def test_button_rejects_press_value_with_write_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A button either writes a fixed value or runs a handler, not both."""
    text = (
        "category: jtmspro\nproduct_id: testbad\nentities:\n  button:\n"
        "    - dp_id: 71\n      press_value: true\n      handlers:\n"
        "        write: lock.command.press_raw_unlock\n"
    )
    with pytest.raises(DeviceRegistryError, match="press_value and a write"):
        _load(tmp_path, monkeypatch, **{"jtmspro_testbad.yaml": text})


def test_open_role_is_lock_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``open`` is rejected outside the lock platform."""
    text = (
        "category: jtmspro\nproduct_id: testbad\nentities:\n  button:\n"
        "    - dp_id: 6\n      handlers:\n"
        "        open: lock.command.momentary_open\n"
    )
    with pytest.raises(DeviceRegistryError, match="cannot use handler roles"):
        _load(tmp_path, monkeypatch, **{"jtmspro_testbad.yaml": text})


@pytest.mark.parametrize("value", ['"71"', "[0]", "[true]", '["71"]', "71"])
def test_invalid_sensitive_dp_ids_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """``sensitive_dp_ids`` must be a list of data point ids."""
    text = f"category: jtmspro\nproduct_id: testbad\nsensitive_dp_ids: {value}\n"
    with pytest.raises(DeviceRegistryError, match="sensitive_dp_ids"):
        _load(tmp_path, monkeypatch, **{"jtmspro_testbad.yaml": text})
