"""Tests for tuya_ble.base module (IntegerTypeData and EnumTypeData)."""

from __future__ import annotations

import json

from custom_components.tuya_ble.base import EnumTypeData, IntegerTypeData
from custom_components.tuya_ble.const import DPCode


def _sample_int_data() -> dict[str, object]:
    """Return sample integer type data."""
    return {"min": 0, "max": 100, "scale": 1.0, "step": 1.0, "unit": "°C"}


def test_integer_from_json_string() -> None:
    """Test creating IntegerTypeData from a JSON string."""
    data = json.dumps(_sample_int_data())
    result = IntegerTypeData.from_json(DPCode.TEMP_SET, data)
    assert result is not None
    assert result.dpcode == DPCode.TEMP_SET
    assert result.min == 0
    assert result.max == 100
    assert result.scale == 1.0
    assert result.step == 1.0
    assert result.unit == "°C"


def test_integer_from_json_dict() -> None:
    """Test creating IntegerTypeData from a dict."""
    result = IntegerTypeData.from_json(DPCode.TEMP_SET, _sample_int_data())
    assert result is not None
    assert result.min == 0


def test_integer_from_json_none() -> None:
    """Test creating IntegerTypeData from null JSON returns None."""
    result = IntegerTypeData.from_json(DPCode.TEMP_SET, "null")
    assert result is None


def test_integer_from_json_step_clamped() -> None:
    """Test that step is clamped to minimum 1."""
    data = json.dumps({"min": 0, "max": 100, "scale": 0, "step": 0})
    result = IntegerTypeData.from_json(DPCode.TEMP_SET, data)
    assert result is not None
    assert result.step == 1


def test_integer_from_dict() -> None:
    """Test creating IntegerTypeData from a dict via from_dict."""
    result = IntegerTypeData.from_dict(DPCode.TEMP_SET, _sample_int_data())
    assert result is not None
    assert result.min == 0
    assert result.max == 100


def test_integer_from_dict_none() -> None:
    """Test creating IntegerTypeData from None returns None."""
    result = IntegerTypeData.from_dict(DPCode.TEMP_SET, None)
    assert result is None


def test_integer_from_dict_defaults() -> None:
    """Test creating IntegerTypeData from empty dict returns None."""
    result = IntegerTypeData.from_dict(DPCode.TEMP_SET, {})
    assert result is None


def test_integer_scale_value() -> None:
    """Test scale_value and scale_value_back roundtrip."""
    itd = IntegerTypeData(DPCode.TEMP_SET, min=0, max=1000, scale=1.0, step=1.0)
    assert itd.scale_value(100) == 10.0
    assert itd.scale_value_back(10.0) == 100


def test_integer_scaled_properties() -> None:
    """Test min_scaled, max_scaled, and step_scaled properties."""
    itd = IntegerTypeData(DPCode.TEMP_SET, min=0, max=1000, scale=1.0, step=2.0)
    assert itd.min_scaled == 0.0
    assert itd.max_scaled == 100.0
    assert itd.step_scaled == 0.2


def test_remap_value_to() -> None:
    """Test remapping a value from this range to a new range."""
    itd = IntegerTypeData(DPCode.TEMP_SET, min=0, max=100, scale=0, step=1.0)
    result = itd.remap_value_to(50, 0, 255)
    assert result == 127.5


def test_remap_value_from() -> None:
    """Test remapping a value from its current range to this range."""
    itd = IntegerTypeData(DPCode.TEMP_SET, min=0, max=100, scale=0, step=1.0)
    result = itd.remap_value_from(127.5, 0, 255)
    assert result == 50.0


def test_remap_value_to_reverse() -> None:
    """Test remapping to a new range in reverse direction."""
    itd = IntegerTypeData(DPCode.TEMP_SET, min=0, max=100, scale=0, step=1.0)
    result = itd.remap_value_to(0, 0, 255, reverse=True)
    assert result == 255.0


def test_remap_value_from_reverse() -> None:
    """Test remapping from its current range in reverse direction."""
    itd = IntegerTypeData(DPCode.TEMP_SET, min=0, max=100, scale=0, step=1.0)
    result = itd.remap_value_from(0, 0, 255, reverse=True)
    assert result == 100.0


def test_enum_from_json() -> None:
    """Test creating EnumTypeData from a JSON string."""
    data = json.dumps({"range": ["off", "on", "auto"]})
    result = EnumTypeData.from_json(DPCode.SWITCH_1, data)
    assert result is not None
    assert result.dpcode == DPCode.SWITCH_1
    assert result.range == ["off", "on", "auto"]


def test_enum_from_json_empty() -> None:
    """Test creating EnumTypeData from JSON with empty range."""
    data = json.dumps({"range": []})
    result = EnumTypeData.from_json(DPCode.SWITCH_1, data)
    assert result is not None
    assert not result.range


def test_enum_from_json_invalid() -> None:
    """Test creating EnumTypeData from null JSON returns None."""
    result = EnumTypeData.from_json(DPCode.SWITCH_1, "null")
    assert result is None
