"""Candidate selection only uses complete, available Tuya BLE cloud records."""

# pylint: disable=protected-access

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tuya_ble.cloud import (
    HASSTuyaBLEDeviceManager,
    _build_credentials,
)
from custom_components.tuya_ble.const import CONF_DEVICE_ID, DOMAIN
from custom_components.tuya_ble.tuya_ble import ProbeOutcome
from tests.test_cloud import make_device
from tests.test_config_flow_probe import _flow


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
    with patch(
        "custom_components.tuya_ble.cloud._build_credentials",
        wraps=_build_credentials,
    ) as build:
        count, candidates = await manager.get_probe_candidates("ble-uuid", "target")
        assert count == 2
        build.assert_not_called()
        assert next(candidates).device_id == "standard"
        assert build.call_count == 1
        assert [d.device_id for d in candidates] == ["other"]


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
    count, candidates = await manager.get_probe_candidates("ble", None)
    assert count == 1
    assert [c.device_id for c in candidates] == ["current"]


async def test_malformed_candidate_metadata_does_not_close_lazy_iterator(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """One malformed SDK record cannot hide subsequent valid cloud credentials."""
    malformed = make_device(
        id="a-bad",
        uuid="cloud-a",
        local_key="private-local-key",
        local_strategy={1: "private-metadata"},
    )
    valid = make_device(id="b-good", uuid="cloud-b", local_key="123456")
    manager = HASSTuyaBLEDeviceManager(hass, {})
    manager._manager = cast(
        Any,
        SimpleNamespace(
            device_map={device.id: device for device in (malformed, valid)}
        ),
    )
    with (
        patch(
            "custom_components.tuya_ble.cloud._build_credentials",
            wraps=_build_credentials,
        ) as build,
        caplog.at_level(logging.DEBUG),
    ):
        count, candidates = await manager.get_probe_candidates("ble-uuid", "pid")
        assert count == 2
        build.assert_not_called()
        assert [candidate.device_id for candidate in candidates] == ["b-good"]
        assert build.call_count == 2
    assert "Skipping malformed Tuya BLE probe candidate" in caplog.text
    assert "private-local-key" not in caplog.text
    assert "private-metadata" not in caplog.text


async def test_real_cloud_manager_skips_bad_metadata_during_public_setup(
    hass: HomeAssistant,
) -> None:
    """Public device selection can authenticate the valid record after a bad one."""
    malformed = make_device(
        id="a-bad",
        uuid="cloud-a",
        local_key="123456",
        product_id="prod",
        local_strategy={1: None},
    )
    valid = make_device(
        id="b-good", uuid="cloud-b", local_key="123456", product_id="prod"
    )
    manager = HASSTuyaBLEDeviceManager(hass, {})
    manager._manager = cast(
        Any,
        SimpleNamespace(
            device_map={device.id: device for device in (malformed, valid)},
            update_device_cache=Mock(),
        ),
    )
    flow, discovery = _flow(hass, [])
    flow._manager = manager
    with (
        patch.object(flow, "_async_scan_device", return_value=discovery),
        patch(
            "custom_components.tuya_ble.config_flow.async_setup_registry",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.tuya_ble.config_flow.get_mapped_dp_ids",
            return_value=frozenset({1}),
        ),
        patch(
            "custom_components.tuya_ble.config_flow.async_probe_authenticated_status",
            new_callable=AsyncMock,
            return_value=ProbeOutcome.MATCHED,
        ) as probe,
    ):
        progress = await flow.async_step_device({"address": discovery.address})
        assert progress["type"] == FlowResultType.SHOW_PROGRESS
        assert flow._probe_task is not None
        await flow._probe_task
        result = await flow.async_step_probe_finish()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_DEVICE_ID] == "b-good"
    probe.assert_awaited_once()
