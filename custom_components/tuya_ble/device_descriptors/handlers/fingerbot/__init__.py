"""Fingerbot handlers for YAML-referenced device behavior."""

from __future__ import annotations

from .mode import (
    in_program_mode,
    in_push_mode,
    in_switch_mode,
    not_in_program_mode,
    repeat_count_available,
)
from .program import (
    get_position,
    get_program,
    get_repeat_count,
    get_repeat_forever,
    set_position,
    set_program,
    set_repeat_count,
    set_repeat_forever,
)

__all__ = [
    "get_position",
    "get_program",
    "get_repeat_count",
    "get_repeat_forever",
    "in_program_mode",
    "in_push_mode",
    "in_switch_mode",
    "not_in_program_mode",
    "repeat_count_available",
    "set_position",
    "set_program",
    "set_repeat_count",
    "set_repeat_forever",
]
