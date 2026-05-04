"""Tests for EufyX8Coordinator session detection logic."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.eufy_x8.api.local import InvalidKey, TuyaException
from custom_components.eufy_x8.coordinator import (
    EufyX8Coordinator,
    _CLEANING_STATES,
    _DONE_STATES,
)
from custom_components.eufy_x8.const import (
    CONF_LOCAL_KEY,
    DPS_WORK_STATUS,
    WORK_STATUS_CHARGING,
    WORK_STATUS_COMPLETED,
    WORK_STATUS_GOTO,
    WORK_STATUS_RUNNING,
    WORK_STATUS_STANDBY,
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
    with patch("custom_components.eufy_x8.coordinator.Store"):
        with patch("custom_components.eufy_x8.coordinator.EufyAuth"):
            c = EufyX8Coordinator(mock_hass, mock_entry)
    return c


# ---------------------------------------------------------------------------
# _CLEANING_STATES / _DONE_STATES sanity
# ---------------------------------------------------------------------------

def test_cleaning_states_include_running_and_goto():
    assert WORK_STATUS_RUNNING in _CLEANING_STATES
    assert WORK_STATUS_GOTO in _CLEANING_STATES


def test_done_states_include_charging_and_completed():
    assert WORK_STATUS_CHARGING in _DONE_STATES
    assert WORK_STATUS_COMPLETED in _DONE_STATES


def test_cleaning_and_done_states_are_disjoint():
    assert _CLEANING_STATES.isdisjoint(_DONE_STATES), \
        "A state cannot be both cleaning and done"


# ---------------------------------------------------------------------------
# Session completion detection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_session_completion_triggers_path_fetch(coordinator, mock_hass):
    """Running → Charging should trigger _fetch_and_store_path."""
    coordinator._was_cleaning = True
    coordinator._prev_status = WORK_STATUS_RUNNING

    with patch.object(coordinator, "_fetch_and_store_path", new_callable=AsyncMock) as mock_fetch:
        await coordinator._check_clean_completed({DPS_WORK_STATUS: WORK_STATUS_CHARGING})
        mock_hass.async_create_task.assert_called_once()


@pytest.mark.asyncio
async def test_no_path_fetch_if_not_cleaning(coordinator, mock_hass):
    """Idle → Charging (never was cleaning) should not trigger path fetch."""
    coordinator._was_cleaning = False
    coordinator._prev_status = "standby"

    await coordinator._check_clean_completed({DPS_WORK_STATUS: WORK_STATUS_CHARGING})
    mock_hass.async_create_task.assert_not_called()


@pytest.mark.asyncio
async def test_running_sets_was_cleaning(coordinator):
    coordinator._was_cleaning = False
    coordinator._prev_status = ""

    await coordinator._check_clean_completed({DPS_WORK_STATUS: WORK_STATUS_RUNNING})
    assert coordinator._was_cleaning is True


@pytest.mark.asyncio
async def test_goto_to_standby_triggers_position_capture(coordinator, mock_hass):
    """Goto → standby should trigger async_capture_position."""
    coordinator._prev_status = WORK_STATUS_GOTO

    with patch.object(coordinator, "async_capture_position", new_callable=AsyncMock):
        await coordinator._check_clean_completed({DPS_WORK_STATUS: "standby"})
        mock_hass.async_create_task.assert_called_once()


@pytest.mark.asyncio
async def test_map_data_sessions_capped_at_20(coordinator):
    """_fetch_and_store_path must cap sessions at 20."""
    # Pre-fill with 20 sessions
    coordinator.map_data = {
        "sessions": [{"timestamp": str(i), "points": [{"x": i, "y": i}]} for i in range(20)]
    }

    with patch("custom_components.eufy_x8.coordinator.get_path_data",
               new_callable=AsyncMock, return_value=[{"x": 99, "y": 99}]):
        with patch.object(coordinator._map_store, "async_save", new_callable=AsyncMock):
            with patch.object(coordinator, "async_request_refresh", new_callable=AsyncMock):
                await coordinator._fetch_and_store_path()

    assert len(coordinator.map_data["sessions"]) == 20
    # The newest session should be the one we just added
    assert coordinator.map_data["sessions"][-1]["points"] == [{"x": 99, "y": 99}]


# ---------------------------------------------------------------------------
# Key rotation (Task 7) — the most critical recovery path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invalid_key_triggers_refresh_and_retries(coordinator, mock_entry):
    """InvalidKey on first poll → key refreshed → second poll succeeds."""
    new_key = "newkey12345abcde"

    # First call raises InvalidKey; second call succeeds
    coordinator._device = MagicMock()
    coordinator._device.state = {"15": "Charging", "104": 80}
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

    # Key should have been written back to config entry
    coordinator.hass.config_entries.async_update_entry.assert_called_once()
    call_kwargs = coordinator.hass.config_entries.async_update_entry.call_args
    assert call_kwargs[1]["data"][CONF_LOCAL_KEY] == new_key

    # Second async_get succeeded — result is the device state
    assert result == {"15": "Charging", "104": 80}


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
        return_value=existing_key,  # same key returned
    ):
        result = await coordinator._refresh_key()

    assert result == existing_key
    coordinator.hass.config_entries.async_update_entry.assert_not_called()
