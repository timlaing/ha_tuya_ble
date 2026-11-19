"""Refresh a lock credential through public options steps; no BLE/cloud I/O."""

from __future__ import annotations

import asyncio
from contextlib import ExitStack
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
import pytest

from custom_components.tuya_ble import (
    _build_credentials_from_entry,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.tuya_ble.const import (
    CONF_BLE_UNLOCK_CHECK,
    CONF_CLOUD_UUID,
    CONF_DEVICE_ID,
    CONF_TOKEN_INFO,
    CONF_USER_CODE,
    CONF_UUID,
    DOMAIN,
)
from custom_components.tuya_ble.diagnostics import async_get_config_entry_diagnostics
from tests.test_config_flow import FakeLogin
from tests.test_config_flow_options_credentials import options_flow
from tests.test_init import _patch_deps
from tests.test_lock_handlers import (
    FIXTURES,
    MEMBER1_CHECK,
    MEMBER3_CHECK,
    NOW,
    _button,
    _load,
    _lock,
    _value,
)

CLOUD = "custom_components.tuya_ble.config_flow_options.HASSTuyaBLEDeviceManager"


@pytest.fixture(name="registry")
def lock_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Load the test-only lock descriptors through the shared helper."""
    return _load(tmp_path, monkeypatch, **FIXTURES)


async def test_manual_entry_requires_existing_qr_link(hass: HomeAssistant) -> None:
    """Manual entries never submit a check or credentials to an undocumented API."""
    flow, entry = options_flow(hass)
    before = dict(entry.data)
    with patch(CLOUD) as manager:
        result = await flow.async_step_refresh_unlock_check({})
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "unlock_check_login_required"
    manager.assert_not_called()
    assert dict(entry.data) == before
    # The supported QR route remains reachable, including for manual entries.
    assert (await flow.async_step_user())["step_id"] == "user"


async def test_manual_entry_can_link_then_refresh(hass: HomeAssistant) -> None:
    """Existing QR login links a manual entry without touching its BLE credentials."""
    flow, entry = options_flow(hass)
    before = dict(entry.data)
    # pylint: disable=protected-access
    flow._qr_login_control = cast(Any, FakeLogin())
    assert (await flow.async_step_user({CONF_USER_CODE: "synthetic"}))[
        "step_id"
    ] == "qr"
    login = await flow.async_step_qr({})
    assert login["type"] == FlowResultType.CREATE_ENTRY
    hass.config_entries.async_update_entry(entry, options=login["data"])
    assert dict(entry.data) == before
    manager = MagicMock()
    manager.get_cloud_device_by_uuid = AsyncMock(
        return_value=SimpleNamespace(
            id=entry.data[CONF_DEVICE_ID],
            status={CONF_BLE_UNLOCK_CHECK: MEMBER3_CHECK},
        )
    )
    with (
        patch(CLOUD, return_value=manager),
        patch.object(hass.config_entries, "async_schedule_reload"),
    ):
        result = await flow.async_step_refresh_unlock_check({})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert dict(entry.data) == {**before, CONF_BLE_UNLOCK_CHECK: MEMBER3_CHECK}


@pytest.mark.parametrize("cloud_uuid", ["cloud-record", None, ""])
@pytest.mark.parametrize("old_check", [MEMBER1_CHECK, MEMBER3_CHECK, None])
async def test_valid_refresh_is_atomic_and_preserves_identity(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
    cloud_uuid: str | None,
    old_check: str | None,
) -> None:
    """A refresh restores old entries too, never replacing the keys or schema."""
    flow, entry = options_flow(hass)
    data = {**entry.data, CONF_CLOUD_UUID: cloud_uuid, "local_schema": {"dp": 1}}
    if old_check is not None:
        data[CONF_BLE_UNLOCK_CHECK] = old_check
    hass.config_entries.async_update_entry(
        entry, data=data, options={CONF_TOKEN_INFO: {"access_token": "synthetic"}}
    )
    original_options = dict(entry.options)
    registry = er.async_get(hass)
    entity = registry.async_get_or_create(
        "lock", DOMAIN, "existing-lock", config_entry=entry
    )
    manager = MagicMock()
    manager.get_cloud_device_by_uuid = AsyncMock(
        return_value=SimpleNamespace(
            id=entry.data[CONF_DEVICE_ID],
            status={CONF_BLE_UNLOCK_CHECK: MEMBER3_CHECK},
        )
    )
    assert (await flow.async_step_refresh_unlock_check())["type"] == FlowResultType.FORM
    with (
        caplog.at_level(logging.DEBUG),
        patch(CLOUD, return_value=manager),
        patch.object(
            hass.config_entries,
            "async_update_entry",
            wraps=hass.config_entries.async_update_entry,
        ) as update,
        patch.object(hass.config_entries, "async_schedule_reload") as reload,
    ):
        result = await flow.async_step_refresh_unlock_check({})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"] == original_options
    assert dict(entry.options) == original_options
    assert dict(entry.data) == {**data, CONF_BLE_UNLOCK_CHECK: MEMBER3_CHECK}
    update.assert_called_once_with(
        entry, data={**data, CONF_BLE_UNLOCK_CHECK: MEMBER3_CHECK}
    )
    reload.assert_called_once_with(entry.entry_id)
    manager.get_cloud_device_by_uuid.assert_awaited_once_with(
        cloud_uuid or entry.data[CONF_UUID], force_update=True
    )
    assert entry.unique_id == entry.data["address"]
    assert registry.async_get(entity.entity_id) is entity
    assert _build_credentials_from_entry(entry).ble_unlock_check == MEMBER3_CHECK
    for secret in (MEMBER1_CHECK, MEMBER3_CHECK, "87654321"):
        assert secret not in repr(result)
        assert secret not in caplog.text


async def test_refreshed_check_is_redacted_in_diagnostics(hass: HomeAssistant) -> None:
    """Refreshed credentials are still only a stored-presence flag."""
    flow, entry = options_flow(hass)
    hass.config_entries.async_update_entry(
        entry, options={CONF_TOKEN_INFO: {"access_token": "synthetic"}}
    )
    manager = MagicMock()
    manager.get_cloud_device_by_uuid = AsyncMock(
        return_value=SimpleNamespace(
            id=entry.data[CONF_DEVICE_ID],
            status={CONF_BLE_UNLOCK_CHECK: MEMBER3_CHECK},
        )
    )
    with (
        patch(CLOUD, return_value=manager),
        patch.object(hass.config_entries, "async_schedule_reload"),
        patch(
            "custom_components.tuya_ble.diagnostics.HASSTuyaBLEDeviceManager",
            return_value=manager,
        ),
    ):
        await flow.async_step_refresh_unlock_check({})
        diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["identity"]["ble_unlock_check_stored"]
    assert MEMBER3_CHECK not in repr(diagnostics)
    assert "87654321" not in repr(diagnostics)


async def test_refresh_preserves_updates_made_during_cloud_request(
    hass: HomeAssistant,
) -> None:
    """Merging after the await does not overwrite newer keys or settings."""
    flow, entry = options_flow(hass)
    hass.config_entries.async_update_entry(
        entry, options={CONF_TOKEN_INFO: {"access_token": "synthetic"}}
    )
    original_options = dict(entry.options)
    manager = MagicMock()

    async def cloud_response(*_args: Any, **_kwargs: Any) -> Any:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, "local_key": "new-synthetic-key", "extra": True}
        )
        return SimpleNamespace(
            id=entry.data[CONF_DEVICE_ID],
            status={CONF_BLE_UNLOCK_CHECK: MEMBER3_CHECK},
        )

    manager.get_cloud_device_by_uuid = AsyncMock(side_effect=cloud_response)

    def isolated_manager(_hass: Any, data: dict[str, Any]) -> Any:
        data[CONF_TOKEN_INFO]["access_token"] = "rotated-synthetic"
        return manager

    with (
        patch(CLOUD, side_effect=isolated_manager),
        patch.object(hass.config_entries, "async_schedule_reload"),
    ):
        await flow.async_step_refresh_unlock_check({})
    assert entry.data["local_key"] == "new-synthetic-key"
    assert entry.data["extra"] is True
    assert entry.data[CONF_BLE_UNLOCK_CHECK] == MEMBER3_CHECK
    assert dict(entry.options) == original_options


@pytest.mark.parametrize(
    ("device", "error"),
    [
        (None, "unlock_check_device_not_found"),
        (SimpleNamespace(id="different"), "unlock_check_device_not_found"),
        (SimpleNamespace(id="device123"), "unlock_check_missing"),
        (SimpleNamespace(id="device123", status=None), "unlock_check_missing"),
        (SimpleNamespace(id="device123", status=[]), "unlock_check_missing"),
        (SimpleNamespace(id="device123", status={}), "unlock_check_missing"),
        (
            SimpleNamespace(id="device123", status={CONF_BLE_UNLOCK_CHECK: ""}),
            "unlock_check_missing",
        ),
        (
            SimpleNamespace(id="device123", status={CONF_BLE_UNLOCK_CHECK: None}),
            "unlock_check_missing",
        ),
        (
            SimpleNamespace(
                id="device123", status={CONF_BLE_UNLOCK_CHECK: "bad-secret-check"}
            ),
            "unlock_check_invalid",
        ),
        (
            SimpleNamespace(id="device123", status={CONF_BLE_UNLOCK_CHECK: 123}),
            "unlock_check_invalid",
        ),
    ],
)
async def test_missing_or_malformed_refresh_retains_check(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture, device: Any, error: str
) -> None:
    """No writes, reloads or credential exposure on rejected cloud responses."""
    flow, entry = options_flow(hass)
    hass.config_entries.async_update_entry(
        entry,
        data={**entry.data, CONF_BLE_UNLOCK_CHECK: MEMBER1_CHECK},
        options={CONF_TOKEN_INFO: {"access_token": "synthetic"}},
    )
    before = dict(entry.data)
    manager = MagicMock()
    manager.get_cloud_device_by_uuid = AsyncMock(return_value=device)
    with (
        caplog.at_level(logging.DEBUG),
        patch(CLOUD, return_value=manager),
        patch.object(hass.config_entries, "async_update_entry") as update,
        patch.object(hass.config_entries, "async_schedule_reload") as reload,
    ):
        result = await flow.async_step_refresh_unlock_check({})
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": error}
    update.assert_not_called()
    reload.assert_not_called()
    assert dict(entry.data) == before
    assert "bad-secret-check" not in repr(result) + caplog.text
    assert MEMBER1_CHECK not in repr(result) + caplog.text


async def test_sdk_refresh_failure_is_retryable_and_redacted(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Exercise the real cloud wrapper: fresh SDK cache calls run in the executor."""
    flow, entry = options_flow(hass)
    hass.config_entries.async_update_entry(
        entry,
        data={**entry.data, CONF_BLE_UNLOCK_CHECK: MEMBER1_CHECK},
        options={CONF_TOKEN_INFO: {"access_token": "synthetic"}},
    )
    sdk = MagicMock()
    device = SimpleNamespace(
        id=entry.data[CONF_DEVICE_ID],
        uuid=entry.data[CONF_UUID],
        status={CONF_BLE_UNLOCK_CHECK: MEMBER3_CHECK},
    )
    sdk.device_map = {device.id: device}
    sdk.update_device_cache.side_effect = [RuntimeError(MEMBER3_CHECK), None, None]
    with (
        caplog.at_level(logging.DEBUG),
        patch("custom_components.tuya_ble.cloud.Manager", return_value=sdk),
        patch.object(hass.config_entries, "async_schedule_reload") as reload,
    ):
        failed = await flow.async_step_refresh_unlock_check({})
        assert failed["errors"] == {"base": "unlock_check_refresh_failed"}
        assert entry.data[CONF_BLE_UNLOCK_CHECK] == MEMBER1_CHECK
        reload.assert_not_called()
        success = await flow.async_step_refresh_unlock_check({})
    assert success["type"] == FlowResultType.CREATE_ENTRY
    assert sdk.update_device_cache.call_count == 3
    assert entry.data[CONF_BLE_UNLOCK_CHECK] == MEMBER3_CHECK
    reload.assert_called_once_with(entry.entry_id)
    assert MEMBER3_CHECK not in caplog.text + repr(failed)


async def test_reload_lifecycle_restores_new_check_without_bluetooth(
    hass: HomeAssistant,
) -> None:
    """Public setup/unload recreates offline credentials from refreshed entry data."""
    flow, entry = options_flow(hass)
    hass.config_entries.async_update_entry(
        entry,
        data={**entry.data, CONF_BLE_UNLOCK_CHECK: MEMBER1_CHECK},
        options={CONF_TOKEN_INFO: {"access_token": "synthetic"}},
    )
    deps = _patch_deps(hass, product_info=MagicMock())
    manager = MagicMock()
    manager.get_cloud_device_by_uuid = AsyncMock(
        return_value=SimpleNamespace(
            id=entry.data[CONF_DEVICE_ID],
            status={CONF_BLE_UNLOCK_CHECK: MEMBER3_CHECK},
        )
    )
    try:
        with ExitStack() as stack:
            for patcher in deps["patches"]:
                stack.enter_context(patcher)
            stack.enter_context(
                patch.object(
                    hass.config_entries,
                    "async_unload_platforms",
                    new=AsyncMock(return_value=True),
                )
            )
            stack.enter_context(patch(CLOUD, return_value=manager))
            stack.enter_context(
                patch.object(hass.config_entries, "async_schedule_reload")
            )
            assert await async_setup_entry(hass, entry)
            assert (
                await hass.data[DOMAIN][entry.entry_id].manager.get_device_credentials(
                    ""
                )
            ).ble_unlock_check == MEMBER1_CHECK
            await flow.async_step_refresh_unlock_check({})
            assert await async_unload_entry(hass, entry)
            deps["device"].stop.assert_awaited_once()
            assert await async_setup_entry(hass, entry)
            assert (
                await hass.data[DOMAIN][entry.entry_id].manager.get_device_credentials(
                    ""
                )
            ).ble_unlock_check == MEMBER3_CHECK
            assert await async_unload_entry(hass, entry)
    finally:
        for task in deps["background_tasks"]:
            task["target"].close()


async def test_refresh_cancellation_does_not_write(hass: HomeAssistant) -> None:
    """Cancellation propagates instead of persisting a partial cloud result."""
    flow, entry = options_flow(hass)
    hass.config_entries.async_update_entry(
        entry,
        data={**entry.data, CONF_BLE_UNLOCK_CHECK: MEMBER1_CHECK},
        options={CONF_TOKEN_INFO: {"access_token": "synthetic"}},
    )
    manager = MagicMock()
    manager.get_cloud_device_by_uuid = AsyncMock(side_effect=asyncio.CancelledError)
    with (
        patch(CLOUD, return_value=manager),
        patch.object(hass.config_entries, "async_update_entry") as update,
        patch.object(hass.config_entries, "async_schedule_reload") as reload,
        pytest.raises(asyncio.CancelledError),
    ):
        await flow.async_step_refresh_unlock_check({})
    update.assert_not_called()
    reload.assert_not_called()
    assert entry.data[CONF_BLE_UNLOCK_CHECK] == MEMBER1_CHECK


@pytest.mark.usefixtures("registry")
async def test_reloaded_lock_and_button_use_refreshed_command(
    hass: HomeAssistant, freezer: Any
) -> None:
    """Public entity services use the new stored check after runtime rebuild."""
    freezer.move_to(NOW)
    flow, entry = options_flow(hass)
    hass.config_entries.async_update_entry(
        entry,
        data={**entry.data, CONF_BLE_UNLOCK_CHECK: MEMBER1_CHECK},
        options={CONF_TOKEN_INFO: {"access_token": "synthetic"}},
    )
    old_lock, old_device = _lock(hass, "testauto", MEMBER1_CHECK)
    await old_lock.async_open()
    old_command = _value(old_device, 71)
    manager = MagicMock()
    manager.get_cloud_device_by_uuid = AsyncMock(
        return_value=SimpleNamespace(
            id=entry.data[CONF_DEVICE_ID],
            status={CONF_BLE_UNLOCK_CHECK: MEMBER3_CHECK},
        )
    )
    with (
        patch(CLOUD, return_value=manager),
        patch.object(hass.config_entries, "async_schedule_reload") as reload,
    ):
        await flow.async_step_refresh_unlock_check({})
    reload.assert_called_once_with(entry.entry_id)
    check = _build_credentials_from_entry(entry).ble_unlock_check
    new_lock, new_device = _lock(hass, "testauto", check)
    new_button, button_device = _button(hass, "testbutton", check)
    await new_lock.async_open()
    await new_button.async_press()
    await hass.async_block_till_done()
    new_command = _value(new_device, 71)
    assert isinstance(new_command, bytes)
    assert new_command != old_command
    assert new_command[2:12] == b"\x00\x03" + b"87654321"
    assert _value(button_device, 71) == new_command
    assert new_lock.unique_id == old_lock.unique_id
