"""Tests for tuya_ble.util module."""

from custom_components.tuya_ble.util import remap_value


def test_remap_value_basic() -> None:
    """Test remapping value from one range to another."""
    assert remap_value(128, 0, 255, 0, 100) == 50.19607843137255


def test_remap_value_same_range() -> None:
    """Test remapping value within same range returns same value."""
    assert remap_value(50, 0, 100, 0, 100) == 50.0


def test_remap_value_reverse() -> None:
    """Test remapping value in reverse direction."""
    assert remap_value(0, 0, 255, 0, 100, reverse=True) == 100.0
    assert remap_value(255, 0, 255, 0, 100, reverse=True) == 0.0


def test_remap_value_negative_range() -> None:
    """Test remapping with negative input range."""
    result = remap_value(0, -10, 10, 0, 255)
    assert result == 127.5


def test_remap_value_negative_output_range() -> None:
    """Test remapping to negative output range."""
    result = remap_value(128, 0, 255, -100, 100)
    assert abs(result - 0.39215686274509665) < 1e-10


def test_remap_value_integer_types() -> None:
    """Test remapping with integer type hints."""
    result = remap_value(5, 0, 10, 0, 100)
    assert result == 50.0
