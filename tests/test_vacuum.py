"""Tests for the EufyX8Vacuum entity."""
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.vacuum import VacuumEntityFeature

from custom_components.eufy_x8.vacuum import EufyX8Vacuum, FEATURES
from custom_components.eufy_x8.const import (
    DPS_ACTIVATE,
    DPS_BATTERY,
    DPS_CLEAN_SPEED,
    DPS_LOCATE,
    DPS_RETURN_HOME,
    DPS_WORK_STATUS,
    WORK_STATUS_CHARGING,
    WORK_STATUS_RUNNING,
    WORK_STATUS_SLEEPING,
    WORK_STATUS_RECHARGE,
    WORK_STATUS_GOTO,
)


@pytest.fixture
def mock_coordinator():
    c = MagicMock()
    c.data = {}
    c.device = MagicMock()
    c.device.async_set = AsyncMock()
    c.device.async_goto = AsyncMock(return_value=True)
    c.device.async_clear = AsyncMock()
    return c


@pytest.fixture
def config_entry():
    entry = MagicMock()
    entry.data = {
        "device_id": "test_device_id_abc123",
        "device_name": "Test Robot",
    }
    return entry


@pytest.fixture
def vacuum(mock_coordinator, config_entry):
    return EufyX8Vacuum(mock_coordinator, config_entry)


# ---------------------------------------------------------------------------
# Features and identity
# ---------------------------------------------------------------------------

def test_vacuum_features(vacuum):
    assert vacuum.supported_features & VacuumEntityFeature.START
    assert vacuum.supported_features & VacuumEntityFeature.STOP
    assert vacuum.supported_features & VacuumEntityFeature.RETURN_HOME
    assert vacuum.supported_features & VacuumEntityFeature.BATTERY
    assert vacuum.supported_features & VacuumEntityFeature.FAN_SPEED
    assert vacuum.supported_features & VacuumEntityFeature.SEND_COMMAND


def test_vacuum_unique_id(vacuum):
    assert vacuum.unique_id == "test_device_id_abc123"


def test_vacuum_name(vacuum):
    # With has_entity_name=True and name=None, the vacuum IS the device;
    # HA uses the device name directly and the entity name attribute is None.
    assert vacuum.name is None


# ---------------------------------------------------------------------------
# State and properties
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("dps15,expected_state", [
    (WORK_STATUS_SLEEPING, "docked"),
    (WORK_STATUS_CHARGING, "docked"),
    (WORK_STATUS_RUNNING, "cleaning"),
    (WORK_STATUS_GOTO, "cleaning"),
    (WORK_STATUS_RECHARGE, "returning"),
    ("unknown_xyz", "idle"),  # falls back to idle
])
def test_vacuum_state(vacuum, mock_coordinator, dps15, expected_state):
    mock_coordinator.data = {DPS_WORK_STATUS: dps15}
    assert vacuum.state == expected_state


def test_vacuum_battery(vacuum, mock_coordinator):
    mock_coordinator.data = {DPS_BATTERY: 85}
    assert vacuum.battery_level == 85


def test_vacuum_battery_none(vacuum, mock_coordinator):
    mock_coordinator.data = {}
    assert vacuum.battery_level is None


@pytest.mark.parametrize("raw,expected_label", [
    ("Pure", "Low"),
    ("Standard", "Medium"),
    ("Turbo", "High"),
    ("Max", "Max"),
])
def test_vacuum_fan_speed(vacuum, mock_coordinator, raw, expected_label):
    mock_coordinator.data = {DPS_CLEAN_SPEED: raw}
    assert vacuum.fan_speed == expected_label


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_async_start(vacuum, mock_coordinator):
    await vacuum.async_start()
    mock_coordinator.device.async_set.assert_awaited_once_with({DPS_ACTIVATE: True})


@pytest.mark.asyncio
async def test_async_stop(vacuum, mock_coordinator):
    await vacuum.async_stop()
    mock_coordinator.device.async_set.assert_awaited_once_with({DPS_ACTIVATE: False})


@pytest.mark.asyncio
async def test_async_pause(vacuum, mock_coordinator):
    await vacuum.async_pause()
    mock_coordinator.device.async_set.assert_awaited_once_with({DPS_ACTIVATE: False})


@pytest.mark.asyncio
async def test_async_return_to_base(vacuum, mock_coordinator):
    await vacuum.async_return_to_base()
    mock_coordinator.device.async_set.assert_awaited_once_with({DPS_RETURN_HOME: True})


@pytest.mark.asyncio
async def test_async_locate(vacuum, mock_coordinator):
    await vacuum.async_locate()
    mock_coordinator.device.async_set.assert_awaited_once_with({DPS_LOCATE: True})


@pytest.mark.asyncio
@pytest.mark.parametrize("label,expected_raw", [
    ("Low", "Pure"),
    ("Medium", "Standard"),
    ("High", "Turbo"),
    ("Max", "Max"),
])
async def test_async_set_fan_speed(vacuum, mock_coordinator, label, expected_raw):
    await vacuum.async_set_fan_speed(label)
    mock_coordinator.device.async_set.assert_awaited_once_with({DPS_CLEAN_SPEED: expected_raw})


@pytest.mark.asyncio
async def test_send_command_goto(vacuum, mock_coordinator):
    await vacuum.async_send_command("goto", params={"x": 2283, "y": -363})
    mock_coordinator.device.async_goto.assert_awaited_once_with(2283, -363)


@pytest.mark.asyncio
async def test_send_command_goto_string_params(vacuum, mock_coordinator):
    # Params from YAML service calls arrive as strings sometimes
    await vacuum.async_send_command("goto", params={"x": "2283", "y": "-363"})
    mock_coordinator.device.async_goto.assert_awaited_once_with(2283, -363)


@pytest.mark.asyncio
async def test_send_command_clear(vacuum, mock_coordinator):
    await vacuum.async_send_command("clear")
    mock_coordinator.device.async_clear.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_command_unknown_does_not_raise(vacuum):
    # Unknown commands should log a warning but not raise
    await vacuum.async_send_command("unknowncommand")
