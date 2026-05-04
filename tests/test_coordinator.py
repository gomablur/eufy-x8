"""Tests for EufyX8Coordinator — key rotation and update logic."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.eufy_x8.api.local import InvalidKey, TuyaException
from custom_components.eufy_x8.coordinator import EufyX8Coordinator
from custom_components.eufy_x8.const import (
    CONF_LOCAL_KEY,
    DPS_WORK_STATUS,
    WORK_STATUS_CHARGING,
)


@pytest.fixture
def mock_hass():
    hass = MagicMock()
    hass.async_create_task = MagicMock()
    hass.config_entries = MagicMock()
    return hass


@pytest.fixture
def mock_entry():
    entry = MagicMock()
    entry.data = {
        "email": "test@example.com",
        "password": "password",
        "device_id": "testdevice123",
        "device_name": "Test Robot",
        "device_ip": "192.168.1.100",
        "local_key": "abcdef1234567890",
    }
    return entry


@pytest.fixture
def coordinator(mock_hass, mock_entry):
    with patch("custom_components.eufy_x8.coordinator.EufyAuth"):
        c = EufyX8Coordinator(mock_hass, mock_entry)
    return c


# ---------------------------------------------------------------------------
# Key rotation — the most critical recovery path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invalid_key_triggers_refresh_and_retries(coordinator, mock_entry):
    """InvalidKey on first poll → key refreshed → second poll succeeds."""
    new_key = "newkey12345abcde"

    coordinator._device = MagicMock()
    coordinator._device.state = {DPS_WORK_STATUS: WORK_STATUS_CHARGING, "104": 80}
    coordinator._device.async_get = AsyncMock(
        side_effect=[InvalidKey("stale key"), None]
    )
    coordinator._device.update_local_key = MagicMock()

    with patch(
        "custom_components.eufy_x8.coordinator.get_local_key",
        new_callable=AsyncMock,
        return_value=new_key,
    ):
        result = await coordinator._async_update_data()

    coordinator.hass.config_entries.async_update_entry.assert_called_once()
    call_kwargs = coordinator.hass.config_entries.async_update_entry.call_args
    assert call_kwargs[1]["data"][CONF_LOCAL_KEY] == new_key
    assert result == {DPS_WORK_STATUS: WORK_STATUS_CHARGING, "104": 80}


@pytest.mark.asyncio
async def test_invalid_key_then_tuya_exception_raises_update_failed(coordinator):
    """InvalidKey → key refresh → second poll also fails → UpdateFailed raised."""
    coordinator._device = MagicMock()
    coordinator._device.state = {}
    coordinator._device.async_get = AsyncMock(
        side_effect=[InvalidKey("stale"), TuyaException("still broken")]
    )
    coordinator._device.update_local_key = MagicMock()

    with patch(
        "custom_components.eufy_x8.coordinator.get_local_key",
        new_callable=AsyncMock,
        return_value="newkey12345abcde",
    ):
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_refresh_key_logs_warning_and_returns_existing_on_cloud_failure(
    coordinator, mock_entry
):
    """When the cloud key fetch fails, _refresh_key returns the existing key."""
    original_key = mock_entry.data[CONF_LOCAL_KEY]

    with patch(
        "custom_components.eufy_x8.coordinator.get_local_key",
        new_callable=AsyncMock,
        side_effect=Exception("cloud unreachable"),
    ):
        result = await coordinator._refresh_key()

    assert result == original_key


@pytest.mark.asyncio
async def test_refresh_key_no_op_if_key_unchanged(coordinator, mock_entry):
    """_refresh_key does not update the config entry if the key hasn't changed."""
    existing_key = mock_entry.data[CONF_LOCAL_KEY]

    with patch(
        "custom_components.eufy_x8.coordinator.get_local_key",
        new_callable=AsyncMock,
        return_value=existing_key,
    ):
        result = await coordinator._refresh_key()

    assert result == existing_key
    coordinator.hass.config_entries.async_update_entry.assert_not_called()
