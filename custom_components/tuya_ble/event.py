"""Registry-backed, report-driven Tuya BLE event entities.

Adapted from ha-tuya-ble/ha_tuya_ble PR 228 by Daniel O'Connor (@CloCkWeRX).
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.event import (
    EventDeviceClass,
    EventEntity,
    EventEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import TuyaBLECoordinator
from .device_registry import EntityDescriptor, get_registry
from .devices import TuyaBLEData
from .entity import TuyaBLEEntity, TuyaBLEProductInfo
from .tuya_ble import TuyaBLEDataPointType, TuyaBLEDevice, TuyaBLEReport


@dataclass
class TuyaBLEEventMapping:
    """Explicit wire-value mapping for a stateless data point."""

    dp_id: int
    description: EventEntityDescription
    dp_type: TuyaBLEDataPointType
    event_values: dict[bool | int | str, str]
    force_add: bool = True


@dataclass
class TuyaBLECategoryEventMapping:
    """Product-specific event mappings within a category."""

    products: dict[str, list[TuyaBLEEventMapping]]


def _build_event_mapping(desc: EntityDescriptor) -> TuyaBLEEventMapping:
    """Build a validated event mapping from a descriptor."""
    assert desc.dp_type is not None
    assert desc.device_class is not None
    return TuyaBLEEventMapping(
        dp_id=desc.dp_id,
        description=EventEntityDescription(
            key=desc.translation_key or str(desc.dp_id),
            name=desc.name,
            icon=desc.icon,
            device_class=EventDeviceClass(desc.device_class),
            entity_category=desc.resolved_entity_category(),
            entity_registry_enabled_default=desc.enabled_by_default is not False,
        ),
        dp_type=TuyaBLEDataPointType(desc.dp_type),
        event_values=dict(desc.extra["event_values"]),
        force_add=desc.force_add,
    )


def _build_mapping() -> dict[str, TuyaBLECategoryEventMapping]:
    """Build per-product mappings from the registry, including defaults."""
    result: dict[str, TuyaBLECategoryEventMapping] = {}
    for product in get_registry().products.values():
        descriptors = product.get("event")
        if descriptors:
            category = result.setdefault(
                product.category, TuyaBLECategoryEventMapping(products={})
            )
            category.products[product.product_id] = [
                _build_event_mapping(desc) for desc in descriptors
            ]
    return result


mapping = _build_mapping()


def get_mapping_by_device(device: TuyaBLEDevice) -> list[TuyaBLEEventMapping]:
    """Return only the event mappings registered for this product."""
    category = mapping.get(device.category)
    if category is None:
        return []
    return category.products.get(device.product_id, [])


class TuyaBLEEvent(TuyaBLEEntity, EventEntity):
    """Emit fresh reports, not changes in a cached data point's value."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: TuyaBLECoordinator,
        device: TuyaBLEDevice,
        product: TuyaBLEProductInfo,
        event_mapping: TuyaBLEEventMapping,
    ) -> None:
        super().__init__(hass, coordinator, device, product, event_mapping.description)
        self._mapping = event_mapping
        self._attr_event_types = list(
            dict.fromkeys(event_mapping.event_values.values())
        )
        self._generation = coordinator.report_generation
        dp = device.datapoints[event_mapping.dp_id]
        self._report_revision = dp.report_revision if dp is not None else 0
        self._baseline_pending = True

    async def async_added_to_hass(self) -> None:
        """Baseline cached reports before subscribing; never replay them."""
        dp = self.device.datapoints[self._mapping.dp_id]
        self._generation = self._coordinator.report_generation
        self._report_revision = dp.report_revision if dp is not None else 0
        self._baseline_pending = True
        await super().async_added_to_hass()

    def _mapped_event_type(self, report: TuyaBLEReport) -> str | None:
        """Return the event type for a fresh report, consuming the baseline.

        The protocol cannot distinguish an initial status from a press, so the
        first report per session is silently baselined. Later equal-valued
        reports are real events and use the snapshot taken at ingestion, never
        an optimistic local write applied during dispatch.
        """
        if report.report_revision <= self._report_revision:
            return None
        self._report_revision = report.report_revision
        if self._baseline_pending:
            self._baseline_pending = False
            return None
        if report.dp_type != self._mapping.dp_type:
            return None
        value = report.reported_value
        # Only decoded scalar wire values can be dictionary keys.
        if not isinstance(value, bool | int | str):
            return None
        return self._mapping.event_values.get(value)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Consume only fresh reports in the current dispatch batch."""
        if self._generation != self._coordinator.report_generation:
            self._generation = self._coordinator.report_generation
            self._baseline_pending = True
        for dp in self._coordinator.last_updates:
            if dp.dp_id != self._mapping.dp_id:
                continue
            # A packet may carry several reports for this data point; each has
            # its own immutable snapshot, so none is lost to a later overwrite.
            for report in dp.reports:
                if event_type := self._mapped_event_type(report):
                    self._trigger_event(event_type)
                    # HA only records the last-event fields in _trigger_event;
                    # publish each one so automations see every click.
                    self.async_write_ha_state()
        self.async_write_ha_state()


async def async_setup_entry(  # noqa: S7503
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the event entities described for this product."""
    data: TuyaBLEData = hass.data[DOMAIN][entry.entry_id]
    entities = [
        TuyaBLEEvent(hass, data.coordinator, data.device, data.product, event_mapping)
        for event_mapping in get_mapping_by_device(data.device)
        if event_mapping.force_add
        or data.device.datapoints.has_id(event_mapping.dp_id, event_mapping.dp_type)
    ]
    async_add_entities(entities)
