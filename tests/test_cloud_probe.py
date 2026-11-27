"""Candidate selection only uses complete, available Tuya BLE cloud records."""

# pylint: disable=protected-access

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ConfigEntryNotReady
import pytest
from pytest_homeassistant.common import MockConfigEntry

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


@pytest.mark.parametrize(
    ("advertised_uuid", "product_id", "expected_count", "expected"),
    [
        # No decoded identity (#155): the readable product is probed first and
        # bounds the count; other products stay as fallbacks (#182).
        ("", "target", 2, ["a-target", "c-target", "b-other"]),
        # No identity and no product hint: every eligible record.
        ("", None, 3, ["a-target", "b-other", "c-target"]),
        # Printable bound bytes that match no record are not a product filter.
        ("", "unmapped", 3, ["a-target", "b-other", "c-target"]),
        # A decoded identity counts other products, ordered after the match.
        ("ble-uuid", "target", 3, ["a-target", "c-target", "b-other"]),
    ],
)
async def test_probe_candidates_without_identity_order_by_product(
    hass: HomeAssistant,
    advertised_uuid: str,
    product_id: str | None,
    expected_count: int,
    expected: list[str],
) -> None:
    """Order by the product hint; bound the count by it only without a UUID."""
    cloud_devices = [
        make_device(id="a-target", uuid="a", product_id="target", local_key="123456"),
        make_device(id="b-other", uuid="b", product_id="other", local_key="123456"),
        make_device(id="c-target", uuid="c", product_id="target", local_key="123456"),
    ]
    manager = HASSTuyaBLEDeviceManager(hass, {})
    manager._manager = cast(
        Any, SimpleNamespace(device_map={d.id: d for d in cloud_devices})
    )
    count, candidates = await manager.get_probe_candidates(advertised_uuid, product_id)
    assert count == expected_count
    assert [c.device_id for c in candidates] == expected


async def test_probe_candidates_hint_keeps_fallbacks_beyond_cap(
    hass: HomeAssistant,
) -> None:
    """A matching hint counts only its records but still yields every fallback."""
    cloud_devices = [
        make_device(id=f"z-other-{index}", uuid=f"o{index}", product_id="other")
        for index in range(5)
    ] + [make_device(id="target", uuid="t", product_id="target")]
    for device in cloud_devices:
        device.local_key = "123456"
    manager = HASSTuyaBLEDeviceManager(hass, {})
    manager._manager = cast(
        Any, SimpleNamespace(device_map={d.id: d for d in cloud_devices})
    )
    count, candidates = await manager.get_probe_candidates("", "target")
    assert count == 1
    assert [c.device_id for c in candidates] == ["target"] + [
        f"z-other-{index}" for index in range(5)
    ]


async def test_async_refresh_reloads_device_cache(hass: HomeAssistant) -> None:
    """Refresh an initialised cloud list off the event loop."""
    manager = HASSTuyaBLEDeviceManager(hass, {})
    update = Mock()
    manager._manager = cast(Any, SimpleNamespace(update_device_cache=update))
    await manager.async_refresh()
    update.assert_called_once_with()


async def test_async_refresh_initialises_missing_manager(hass: HomeAssistant) -> None:
    """Initialising loads the device list, so it is not refreshed twice."""
    manager = HASSTuyaBLEDeviceManager(hass, {})
    with patch.object(manager, "initialize", new_callable=AsyncMock) as initialize:
        await manager.async_refresh()
    initialize.assert_awaited_once_with()


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


async def test_probe_candidates_initialise_missing_manager(
    hass: HomeAssistant,
) -> None:
    """Load the cloud list first, and fail setup if it still cannot load."""
    manager = HASSTuyaBLEDeviceManager(hass, {})
    with (
        patch.object(manager, "initialize", new_callable=AsyncMock) as initialize,
        pytest.raises(ConfigEntryNotReady),
    ):
        await manager.get_probe_candidates("", None)
    initialize.assert_awaited_once_with()

    async def load() -> None:
        manager._manager = cast(
            Any, SimpleNamespace(device_map={"a": make_device(local_key="123456")})
        )

    with patch.object(manager, "initialize", side_effect=load):
        count, candidates = await manager.get_probe_candidates("", None)
    assert count == 1
    assert [c.device_id for c in candidates] == ["deviceid"]
