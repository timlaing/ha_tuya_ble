"""Tests for tuya_ble.util module."""

from homeassistant.const import PERCENTAGE, UnitOfTemperature, UnitOfTime

from custom_components.tuya_ble.util import remap_value, resolve_unit, to_bool


def test_resolve_unit_none() -> None:
    """A missing unit stays missing."""
    assert resolve_unit(None) is None


def test_resolve_unit_known_aliases() -> None:
    """Known descriptor aliases map to the canonical HA unit."""
    assert resolve_unit("%") == PERCENTAGE
    assert resolve_unit("s") == UnitOfTime.SECONDS
    assert resolve_unit("min") == UnitOfTime.MINUTES
    assert resolve_unit("h") == UnitOfTime.HOURS
    assert resolve_unit("°C") == UnitOfTemperature.CELSIUS
    assert resolve_unit("℃") == UnitOfTemperature.CELSIUS


def test_resolve_unit_unknown_passthrough() -> None:
    """An unrecognised unit is returned unchanged."""
    assert resolve_unit("kPa") == "kPa"


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


def test_to_bool_passes_through_booleans() -> None:
    """Booleans are returned unchanged."""
    assert to_bool(True) is True
    assert to_bool(False) is False


def test_to_bool_numbers() -> None:
    """Zero is off and any other number is on."""
    assert to_bool(0) is False
    assert to_bool(1) is True
    assert to_bool(-1) is True
    assert to_bool(0.0) is False


def test_to_bool_empty_bytes_is_off() -> None:
    """An empty payload reads as off, unlike bool(b"")."""
    assert to_bool(b"") is False


def test_to_bool_zero_bitmap_is_off() -> None:
    """A zeroed bitmap is no set flag, unlike bool(b"\x00")."""
    assert to_bool(b"\x00") is False
    assert to_bool(b"\x00\x00\x00") is False


def test_to_bool_set_bit_is_on() -> None:
    """Any set bit in a payload means the flag is raised."""
    assert to_bool(b"\x01") is True
    assert to_bool(b"\x00\x02") is True


def test_to_bool_strings_keep_truthiness() -> None:
    """Strings keep Python truthiness, as before."""
    assert to_bool("") is False
    assert to_bool("1") is True
    assert to_bool("0") is True
