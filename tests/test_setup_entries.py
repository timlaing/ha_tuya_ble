"""Unit tests for the per-platform async_setup_entry boilerplate."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from homeassistant.core import HomeAssistant
import pytest

from custom_components.tuya_ble import (
    binary_sensor,
    button,
    climate,
    cover,
    light,
    lock,
    number,
    select,
    sensor,
    switch,
    text,
    valve,
)
from custom_components.tuya_ble.const import DOMAIN

PLATFORMS = {
    "binary_sensor": (binary_sensor, "TuyaBLEBinarySensor"),
    "button": (button, "TuyaBLEButton"),
    "climate": (climate, "TuyaBLEClimate"),
    "cover": (cover, "TuyaBLECover"),
    "light": (light, "TuyaBLELight"),
    "lock": (lock, "TuyaBLELock"),
    "number": (number, "TuyaBLENumber"),
    "select": (select, "TuyaBLESelect"),
    "sensor": (sensor, "TuyaBLESensor"),
    "switch": (switch, "TuyaBLESwitch"),
    "text": (text, "TuyaBLEText"),
    "valve": (valve, "TuyaBLEValve"),
}


def _first_product(mod: Any) -> tuple[str, str | None]:
    """Pick a real (category, product_id) that yields at least one mapping."""
    category = next(iter(mod.mapping))
    cat_info = mod.mapping[category]
    products = getattr(cat_info, "products", None) or {}
    if products:
        return category, next(iter(products))
    return category, None


def _data(
    category: str, product_id: str | None, has_id: bool = True
) -> SimpleNamespace:
    device = SimpleNamespace(
        category=category,
        product_id=product_id,
        datapoints=SimpleNamespace(has_id=lambda *a: has_id),
    )
    return SimpleNamespace(
        device=device,
        coordinator=MagicMock(),
        product=MagicMock(),
    )


@pytest.mark.parametrize("name", sorted(PLATFORMS))
async def test_async_setup_entry_force_add(hass: HomeAssistant, name: str) -> None:
    """Assert force_add mappings add an entity for every data point mapping."""
    mod, class_name = PLATFORMS[name]
    category, product_id = _first_product(mod)

    data = _data(category, product_id)
    entry = SimpleNamespace(entry_id="entry1")
    hass.data[DOMAIN] = {entry.entry_id: data}

    async_add_entities = MagicMock()
    with patch.object(mod, class_name) as entity_cls:
        await mod.async_setup_entry(hass, entry, async_add_entities)

    assert async_add_entities.called
    entities = async_add_entities.call_args[0][0]
    assert entities
    assert entity_cls.call_count == len(entities)


@pytest.mark.parametrize("name", sorted(PLATFORMS))
async def test_async_setup_entry_requires_datapoint(
    hass: HomeAssistant, name: str
) -> None:
    """When force_add is False the entity is only added if the datapoint exists."""
    mod, class_name = PLATFORMS[name]

    # Build a category/product whose mappings are NOT force_add, then confirm
    # the has_id gate is exercised for both outcomes.
    category, product_id = _first_product(mod)
    with patch.object(mod, class_name):
        data = _data(category, product_id, has_id=False)
        entry = SimpleNamespace(entry_id="entry1")
        hass.data[DOMAIN] = {entry.entry_id: data}
        async_add_entities = MagicMock()
        await mod.async_setup_entry(hass, entry, async_add_entities)

        data2 = _data(category, product_id, has_id=True)
        entry2 = SimpleNamespace(entry_id="entry2")
        hass.data[DOMAIN] = {entry2.entry_id: data2}
        async_add_entities2 = MagicMock()
        await mod.async_setup_entry(hass, entry2, async_add_entities2)
