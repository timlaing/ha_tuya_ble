"""The options flow sets the Bluetooth connection policy (#167)."""

from __future__ import annotations

import logging
from typing import Any, cast

from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tuya_ble.config_flow_options import TuyaBLEOptionsFlow
from custom_components.tuya_ble.const import (
    CONF_IDLE_DISCONNECT_DELAY,
    CONF_KEEP_CONNECTION,
    CONF_STATUS_POLL_INTERVAL,
    CONF_USER_CODE,
    DOMAIN,
)

ADDRESS = "AA:BB:CC:DD:EE:FF"


def options_flow(
    hass: HomeAssistant, options: dict[str, Any] | None = None
) -> TuyaBLEOptionsFlow:
    """Build an options flow for an entry with the given options."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Fingerbot",
        unique_id=ADDRESS,
        data={CONF_ADDRESS: ADDRESS},
        options={CONF_USER_CODE: "user1", **(options or {})},
    )
    entry.add_to_hass(hass)
    flow = TuyaBLEOptionsFlow(entry)
    flow.hass = hass
    flow.handler = entry.entry_id
    return flow


def defaults(result: Any) -> dict[str, Any]:
    """Return the form's prefilled values, as the frontend would submit them."""
    return cast("dict[str, Any]", cast(Any, result["data_schema"])({}))


def prefilled(result: Any) -> dict[str, Any]:
    """Return the form's defaults without validating them."""
    return {str(key): key.default() for key in result["data_schema"].schema}


async def submit(flow: TuyaBLEOptionsFlow, user_input: dict[str, Any]) -> Any:
    """Submit the connection form after applying its schema."""
    form = await flow.async_step_connection()
    return await flow.async_step_connection(cast(Any, form["data_schema"])(user_input))


async def test_form_defaults_keep_the_connection(hass: HomeAssistant) -> None:
    """An entry without the options shows today's behaviour as the default."""
    result = await options_flow(hass).async_step_connection()
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "connection"
    assert result["errors"] == {}
    assert defaults(result) == {
        CONF_KEEP_CONNECTION: True,
        CONF_IDLE_DISCONNECT_DELAY: 30,
        CONF_STATUS_POLL_INTERVAL: 900,
    }


async def test_form_shows_the_stored_policy(hass: HomeAssistant) -> None:
    """The form is prefilled with the saved policy."""
    stored = {
        CONF_KEEP_CONNECTION: False,
        CONF_IDLE_DISCONNECT_DELAY: 120,
        CONF_STATUS_POLL_INTERVAL: 0,
    }
    result = await options_flow(hass, stored).async_step_connection()
    assert defaults(result) == stored


async def test_saving_keeps_the_other_options(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """The policy is stored as whole seconds next to the cloud login."""
    caplog.set_level(logging.DEBUG)
    result = await submit(
        options_flow(hass),
        {
            CONF_KEEP_CONNECTION: False,
            CONF_IDLE_DISCONNECT_DELAY: 45.0,
            CONF_STATUS_POLL_INTERVAL: 0.0,
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        CONF_USER_CODE: "user1",
        CONF_KEEP_CONNECTION: False,
        CONF_IDLE_DISCONNECT_DELAY: 45,
        CONF_STATUS_POLL_INTERVAL: 0,
    }
    assert isinstance(result["data"][CONF_IDLE_DISCONNECT_DELAY], int)
    assert f"{ADDRESS}: connection policy set (keep connection: False)" in (caplog.text)


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        (CONF_IDLE_DISCONNECT_DELAY, 4, "invalid_idle_disconnect_delay"),
        (CONF_IDLE_DISCONNECT_DELAY, 3601, "invalid_idle_disconnect_delay"),
        (CONF_STATUS_POLL_INTERVAL, 59, "invalid_status_poll_interval"),
        (CONF_STATUS_POLL_INTERVAL, 86401, "invalid_status_poll_interval"),
    ],
)
async def test_out_of_range_values_are_rejected(
    hass: HomeAssistant, field: str, value: int, error: str
) -> None:
    """Values outside the allowed range show an error and keep the input."""
    flow = options_flow(hass)
    user_input = {
        CONF_KEEP_CONNECTION: False,
        CONF_IDLE_DISCONNECT_DELAY: 30,
        CONF_STATUS_POLL_INTERVAL: 900,
        field: value,
    }
    result = await flow.async_step_connection(user_input)
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {field: error}
    assert prefilled(result)[field] == value
    assert flow.config_entry.options == {CONF_USER_CODE: "user1"}


@pytest.mark.parametrize(
    ("idle", "poll"), [(5, 60), (3600, 86400), (30, 0)], ids=["min", "max", "off"]
)
async def test_boundary_values_are_accepted(
    hass: HomeAssistant, idle: int, poll: int
) -> None:
    """The range limits and a zero poll interval are valid."""
    result = await submit(
        options_flow(hass),
        {
            CONF_KEEP_CONNECTION: False,
            CONF_IDLE_DISCONNECT_DELAY: idle,
            CONF_STATUS_POLL_INTERVAL: poll,
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_IDLE_DISCONNECT_DELAY] == idle
    assert result["data"][CONF_STATUS_POLL_INTERVAL] == poll
