"""Unit tests for the Tuya BLE base entity."""
# pylint: disable=protected-access
# The tests deliberately set up device/coordinator internals as test setup
# state, which is sanctioned by the project's test conventions.

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityDescription
import pytest

from custom_components.tuya_ble.const import DOMAIN, DPCode, DPType
from custom_components.tuya_ble.entity import TuyaBLEEntity, _resolve_unique_id
from custom_components.tuya_ble.tuya_ble import TuyaBLEDataPointType
from tests.conftest import make_credentials, make_device

ADDRESS = "AA:BB:CC:DD:EE:FF"
DEVICE_ID = "device123"


def _make_entity(hass: HomeAssistant) -> TuyaBLEEntity:
    """Build a base entity wired to a fake device and coordinator."""
    device = make_device()
    device._device_info = make_credentials()

    async def _record_send(dp_ids: list[int]) -> None:
        """Record which datapoint ids the entity asked the device to send."""
        device._sent = dp_ids  # type: ignore[attr-defined]

    device.send_datapoints = _record_send  # type: ignore[assignment]
    device.set_multiple_values = _record_send  # type: ignore[assignment]

    coordinator = MagicMock()
    coordinator.connected = True
    coordinator.async_request_refresh = MagicMock(return_value=MagicMock())

    class _Description(EntityDescription):
        """Concrete description for the test entity."""

    entity = TuyaBLEEntity(
        hass,
        coordinator,
        device,
        MagicMock(),
        _Description(key="test_key"),
    )
    entity.hass = hass
    return entity


# --------------------------------------------------------------------------
# _resolve_unique_id
# --------------------------------------------------------------------------


def test_resolve_unique_id_without_legacy_key(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A device with no registered legacy alias keeps the descriptor key."""
    device = make_device()
    device._device_info = make_credentials()

    with caplog.at_level(logging.DEBUG):
        uid = _resolve_unique_id(hass, device, "switch")

    assert uid == f"{DEVICE_ID}-switch"
    assert f"resolved unique id {DEVICE_ID}-switch" in caplog.text


def test_resolve_unique_id_adopts_legacy_key(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """An entity already registered under a legacy key keeps that unique id."""
    device = make_device()
    device._device_info = make_credentials()

    # Seed the cached legacy suffixes the way a previous setup would have.
    hass.data[DOMAIN] = {"legacy_unique_id_suffixes": {DEVICE_ID: {"legacy_key"}}}

    with (
        patch(
            "custom_components.tuya_ble.entity._find_legacy_keys",
            return_value=["legacy_key"],
        ),
        caplog.at_level(logging.DEBUG),
    ):
        uid = _resolve_unique_id(hass, device, "switch")

    assert uid == f"{DEVICE_ID}-legacy_key"
    assert "resolved to legacy key legacy_key" in caplog.text


# --------------------------------------------------------------------------
# send_dp_value / send_multiple_dp_values
# --------------------------------------------------------------------------


async def test_send_dp_value_skips_missing_key(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A None dp id is skipped instead of being sent to the device."""
    entity = _make_entity(hass)

    with caplog.at_level(logging.DEBUG):
        entity.send_dp_value(None, TuyaBLEDataPointType.DT_BOOL, True)

    assert "skipping send of test_key, key: None" in caplog.text


async def test_send_dp_value_skips_missing_value(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A None value is skipped instead of being sent to the device."""
    entity = _make_entity(hass)

    with caplog.at_level(logging.DEBUG):
        entity.send_dp_value(1, TuyaBLEDataPointType.DT_BOOL, None)

    assert "skipping send of test_key, key: 1, value: None" in caplog.text


async def test_send_dp_value_sends_value(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A complete send is traced and queued as a task."""
    entity = _make_entity(hass)

    with caplog.at_level(logging.DEBUG):
        entity.send_dp_value(2, TuyaBLEDataPointType.DT_BOOL, True)
        await hass.async_block_till_done()

    assert f"{ADDRESS}: sending data point 2 = True" in caplog.text


async def test_send_multiple_dp_values_is_logged(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A multi-data-point send reports how many data points are queued."""
    entity = _make_entity(hass)

    with caplog.at_level(logging.DEBUG):
        entity.send_multiple_dp_values([
            (1, TuyaBLEDataPointType.DT_BOOL, True),
            (2, TuyaBLEDataPointType.DT_VALUE, 5),
        ])
        await hass.async_block_till_done()

    assert f"{ADDRESS}: sending 2 data point(s)" in caplog.text


# --------------------------------------------------------------------------
# find_dpcode
# --------------------------------------------------------------------------


def test_find_dpcode_without_match_is_logged(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A DP-code miss is the main "entity never appeared" diagnostic."""
    entity = _make_entity(hass)

    with caplog.at_level(logging.DEBUG):
        assert (
            entity.find_dpcode(
                (DPCode.COUNTDOWN, DPCode.COUNTDOWN_SET), dptype=DPType.BOOLEAN
            )
            is None
        )

    assert (
        f"{ADDRESS}: no matching DP code found for countdown, countdown_set"
        in caplog.text
    )
    assert "entity test_key will be unavailable" in caplog.text


# --------------------------------------------------------------------------
# _send_command
# --------------------------------------------------------------------------


async def test_send_command_logs_incomplete_commands(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Commands missing dp_id/dp_type/value are traced and skipped."""
    entity = _make_entity(hass)

    with caplog.at_level(logging.DEBUG):
        entity._send_command([
            {"dp_id": None, "dp_type": TuyaBLEDataPointType.DT_BOOL, "value": True},
            {"dp_id": 3, "dp_type": None, "value": True},
            {"dp_id": 3, "dp_type": TuyaBLEDataPointType.DT_BOOL, "value": None},
        ])
        await hass.async_block_till_done()

    assert f"{ADDRESS}: sending 3 command(s)" in caplog.text
    assert "skipping command, dp_id: None" in caplog.text
    assert "skipping command, dp_id: 3, dp_type: None" in caplog.text
    assert "skipping command, dp_id: 3" in caplog.text


async def test_send_command_sends_complete_commands(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A complete command reaches the data point send path."""
    entity = _make_entity(hass)

    with caplog.at_level(logging.DEBUG):
        entity._send_command([
            {"dp_id": 4, "dp_type": TuyaBLEDataPointType.DT_BOOL, "value": False}
        ])
        await hass.async_block_till_done()

    assert "skipping command" not in caplog.text
    assert f"{ADDRESS}: sending data point 4 = False" in caplog.text
