"""Unit tests for the Tuya BLE base entity."""
# pylint: disable=protected-access,abstract-method
# The tests deliberately set up device/coordinator internals as test setup
# state, which is sanctioned by the project's test conventions.

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityDescription
import pytest

from custom_components.tuya_ble.const import DOMAIN
from custom_components.tuya_ble.entity import (
    TuyaBLEEntity,
    TuyaBLERestoreEntity,
    _resolve_unique_id,
)
from custom_components.tuya_ble.tuya_ble import TuyaBLEDataPointType
from tests.conftest import make_credentials, make_device

ADDRESS = "AA:BB:CC:DD:EE:FF"
DEVICE_ID = "device123"


def _make_entity(hass: HomeAssistant) -> TuyaBLEEntity:
    """Build a base entity wired to a fake device and coordinator."""
    device = make_device()
    device._device_info = make_credentials()

    async def _record_send(payload: list[int] | dict[int, Any]) -> None:
        """Record the payload sent via send_datapoints or set_multiple_values."""
        device._sent = payload  # type: ignore[attr-defined]

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
# TuyaBLERestoreEntity
# --------------------------------------------------------------------------


class _IncompleteRestoreEntity(TuyaBLERestoreEntity):
    """Restore entity that leaves the abstract hooks to the base class."""


def _make_restore_entity(
    hass: HomeAssistant, dp_id: int | None = None
) -> TuyaBLERestoreEntity:
    """Build a restore mixin entity whose hook is left unimplemented."""
    device = make_device()
    device._device_info = make_credentials()
    if dp_id is not None:
        device.datapoints.get_or_create(dp_id, TuyaBLEDataPointType.DT_VALUE, 1)

    coordinator = MagicMock()
    coordinator.connected = True
    coordinator.async_request_refresh = MagicMock(return_value=MagicMock())

    entity = _IncompleteRestoreEntity(
        hass, coordinator, device, MagicMock(), EntityDescription(key="restore")
    )
    entity.hass = hass
    return entity


def test_restore_defaults_to_disabled() -> None:
    """Entities are not restored unless the descriptor opts in."""
    assert TuyaBLERestoreEntity._attr_restore is False


async def test_restore_hooks_must_be_implemented(hass: HomeAssistant) -> None:
    """The mixin leaves the data point id and restore step to the platform."""
    entity = _make_restore_entity(hass)

    with pytest.raises(NotImplementedError):
        _ = entity._restore_dp_id

    with pytest.raises(NotImplementedError):
        await entity._async_restore_state()
