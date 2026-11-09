"""Candidate selection only uses complete, available Tuya BLE cloud records."""

# pylint: disable=protected-access

from __future__ import annotations

from typing import Any, cast

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tuya_ble.cloud import HASSTuyaBLEDeviceManager
from custom_components.tuya_ble.const import CONF_DEVICE_ID, DOMAIN
from tests.test_cloud import make_device


async def test_probe_candidates_filter_and_order(hass: HomeAssistant) -> None:
    """Skip claimed, malformed and exact records; prefer matching product."""
    MockConfigEntry(domain=DOMAIN, data={CONF_DEVICE_ID: "used"}).add_to_hass(hass)
    MockConfigEntry(domain="tuya", data={CONF_DEVICE_ID: "standard"}).add_to_hass(hass)
    cloud_devices = [
        make_device(
            id="other", uuid="other-uuid", product_id="different", local_key="123456"
        ),
        make_device(
            id="used", uuid="used-uuid", product_id="target", local_key="123456"
        ),
        make_device(
            id="standard", uuid="standard-uuid", product_id="target", local_key="123456"
        ),
        make_device(
            id="exact", uuid="ble-uuid", product_id="target", local_key="123456"
        ),
        make_device(
            id="short", uuid="short-uuid", product_id="target", local_key="short"
        ),
        make_device(id="missing", uuid=None, product_id="target", local_key="123456"),
    ]
    manager = HASSTuyaBLEDeviceManager(hass, {})
    manager._manager = cast(
        Any,
        type(
            "FakeManager",
            (),
            {
                "device_map": {d.id: d for d in cloud_devices},
            },
        )(),
    )
    candidates = await manager.get_probe_candidates("ble-uuid", "target")
    assert [d.device_id for d in candidates] == ["standard", "other"]


async def test_probe_candidates_options_claim(hass: HomeAssistant) -> None:
    """Device ownership comes from entry data, not QR reauth options."""
    MockConfigEntry(
        domain=DOMAIN,
        data={CONF_DEVICE_ID: "former"},
        options={CONF_DEVICE_ID: "current"},
    ).add_to_hass(hass)
    cloud_devices = [
        make_device(id="current", uuid="a", local_key="123456"),
        make_device(id="former", uuid="b", local_key="123456"),
    ]
    manager = HASSTuyaBLEDeviceManager(hass, {})
    manager._manager = cast(
        Any,
        type(
            "FakeManager",
            (),
            {
                "device_map": {d.id: d for d in cloud_devices},
            },
        )(),
    )
    assert [c.device_id for c in await manager.get_probe_candidates("ble", None)] == [
        "current"
    ]
