"""Shared ownership of bounded configuration-time BLE verification."""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.core import HomeAssistant

from .const import DOMAIN

PROBE_CLAIM_TTL = 120
_PROBE_STATE = f"{DOMAIN}_probe_state"
ProbeClaim = tuple[object, str, float]


def _consume_probe_result(task: asyncio.Task[Any]) -> None:
    """Observe probe failures without logging exception text after flow removal."""
    if not task.cancelled():
        task.exception()


def probe_state(
    hass: HomeAssistant,
) -> tuple[asyncio.Lock, dict[str, ProbeClaim]]:
    """Serialise probes and reserve matched cloud IDs until entry creation or TTL."""
    state = hass.data.setdefault(_PROBE_STATE, {"lock": asyncio.Lock(), "claims": {}})
    return state["lock"], state["claims"]
