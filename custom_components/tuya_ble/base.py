"""Base data types for Tuya BLE device specs."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from .const import DPCode
from .util import remap_value


@dataclass
class IntegerTypeData:
    """Integer type data from a Tuya cloud device spec."""

    dpcode: DPCode
    min: int
    max: int
    scale: float
    step: float
    unit: str | None = None
    type: str | None = None

    @property
    def max_scaled(self) -> float:
        """Return the max scaled."""
        return self.scale_value(self.max)

    @property
    def min_scaled(self) -> float:
        """Return the min scaled."""
        return self.scale_value(self.min)

    @property
    def step_scaled(self) -> float:
        """Return the step scaled."""
        return self.step / (10**self.scale)

    def scale_value(self, value: float | int) -> float:
        """Scale a value."""
        return value / (10**self.scale)

    def scale_value_back(self, value: float | int) -> int:
        """Return raw value for scaled."""
        return int(value * (10**self.scale))

    def remap_value_to(
        self,
        value: float,
        to_min: float | int = 0,
        to_max: float | int = 255,
        reverse: bool = False,
    ) -> float:
        """Remap a value from this range to a new range."""
        return remap_value(value, self.min, self.max, to_min, to_max, reverse)

    def remap_value_from(
        self,
        value: float,
        from_min: float | int = 0,
        from_max: float | int = 255,
        reverse: bool = False,
    ) -> float:
        """Remap a value from its current range to this range."""
        return remap_value(value, from_min, from_max, self.min, self.max, reverse)

    @classmethod
    def from_json(
        cls, dpcode: DPCode, data: str | dict[str, Any]
    ) -> IntegerTypeData | None:
        """Load JSON string and return an IntegerTypeData object."""
        parsed: dict[str, Any] | None = (
            json.loads(data) if isinstance(data, str) else data
        )

        if parsed is None:
            return None

        return cls(
            dpcode,
            min=int(parsed["min"]),
            max=int(parsed["max"]),
            scale=float(parsed["scale"]),
            step=max(float(parsed["step"]), 1),
            unit=str(parsed.get("unit")) if parsed.get("unit") else None,
            type=str(parsed.get("type")) if parsed.get("type") else None,
        )

    @classmethod
    def from_dict(
        cls, dpcode: DPCode, data: dict[str, Any] | None
    ) -> IntegerTypeData | None:
        """Load dict and return an IntegerTypeData object."""
        if not data:
            return None

        return cls(
            dpcode,
            min=int(data.get("min", 0)),
            max=int(data.get("max", 0)),
            scale=float(data.get("scale", 0)),
            step=max(float(data.get("step", 0)), 1),
            unit=data.get("unit"),
            type=data.get("type"),
        )


@dataclass
class EnumTypeData:
    """Enum type data from a Tuya cloud device spec."""

    dpcode: DPCode
    range: list[str]

    @classmethod
    def from_json(
        cls, dpcode: DPCode, data: str | dict[str, Any] | None
    ) -> EnumTypeData | None:
        """Load JSON string/dict and return an EnumTypeData object."""
        if data is None:
            return None
        parsed = data if isinstance(data, dict) else json.loads(data)
        if not parsed:
            return None
        return cls(dpcode, **parsed)
