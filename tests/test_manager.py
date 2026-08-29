"""Unit tests for the Tuya BLE device manager module."""

from __future__ import annotations

import pytest

from custom_components.tuya_ble.tuya_ble.manager import (
    AbstractTuyaBLEDeviceManager,
    TuyaBLEDeviceCredentials,
)


def test_credentials_str_hides_secrets() -> None:
    """Assert the string form of credentials hides secret fields."""
    creds = TuyaBLEDeviceCredentials(
        uuid="1234567890abcdef",
        local_key="abcdef",
        device_id="device123",
        category="wk",
        product_id="drlajpqc",
        device_name="Device",
        product_model="Model",
        product_name="Product",
    )
    text = str(creds)
    assert "1234567890abcdef" not in text
    assert "device123" not in text
    assert "category: wk" in text
    assert "product_id: drlajpqc" in text
    assert "device_name: Device" in text
    assert "product_model: Model" in text
    assert "product_name: Product" in text


def test_credentials_defaults_none() -> None:
    """Assert the functions and status_range fields default to None."""
    creds = TuyaBLEDeviceCredentials(
        uuid="u",
        local_key="k",
        device_id="d",
        category="c",
        product_id="p",
        device_name=None,
        product_model=None,
        product_name=None,
    )
    assert creds.functions is None
    assert creds.status_range is None


def test_all_fields_present() -> None:
    """Assert credentials are created when every required field is set."""
    creds = AbstractTuyaBLEDeviceManager.check_and_create_device_credentials(
        uuid="u",
        local_key="k",
        device_id="d",
        category="c",
        product_id="p",
        device_name="Device",
        product_model="Model",
        product_name="Product",
    )
    assert creds is not None
    assert creds.uuid == "u"
    assert creds.local_key == "k"
    assert creds.device_id == "d"
    assert creds.category == "c"
    assert creds.product_id == "p"
    assert creds.device_name == "Device"
    assert creds.product_model == "Model"
    assert creds.product_name == "Product"


@pytest.mark.parametrize(
    "missing",
    [
        {"uuid": None},
        {"local_key": None},
        {"device_id": None},
        {"category": None},
        {"product_id": None},
    ],
)
def test_missing_required_field_returns_none(missing: dict[str, str | None]) -> None:
    """Assert credentials are refused when a required field is missing."""
    kwargs: dict[str, str | None] = {
        "uuid": "u",
        "local_key": "k",
        "device_id": "d",
        "category": "c",
        "product_id": "p",
        "device_name": "Device",
        "product_model": "Model",
        "product_name": "Product",
    }
    kwargs.update(missing)
    result = AbstractTuyaBLEDeviceManager.check_and_create_device_credentials(**kwargs)
    assert result is None
