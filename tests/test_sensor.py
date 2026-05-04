"""Tests for sensor entities."""
from unittest.mock import MagicMock

import pytest

from custom_components.eufy_x8.sensor import (
    ActivitySensor,
    BatterySensor,
    CleaningAreaSensor,
    CleaningTimeSensor,
    DetailedStatusSensor,
    ErrorSensor,
)
from custom_components.eufy_x8.const import (
    DPS_BATTERY,
    DPS_CLEANING_AREA,
    DPS_CLEANING_TIME,
    DPS_ERROR_CODE,
    DPS_WORK_STATUS,
    DPS_WORK_STATUS_2,
    WORK_STATUS_CHARGING,
    WORK_STATUS_RUNNING,
    WORK_STATUS_SLEEPING,
)


@pytest.fixture
def mock_coordinator():
    c = MagicMock()
    c.data = {}
    return c


@pytest.fixture
def config_entry():
    entry = MagicMock()
    entry.data = {
        "device_id": "test_device_id_abc123",
        "device_name": "Test Robot",
    }
    return entry


# ---------------------------------------------------------------------------
# BatterySensor
# ---------------------------------------------------------------------------

def test_battery_sensor_value(mock_coordinator, config_entry):
    mock_coordinator.data = {DPS_BATTERY: 72}
    sensor = BatterySensor(mock_coordinator, config_entry, "Test Robot")
    assert sensor.native_value == 72


def test_battery_sensor_none(mock_coordinator, config_entry):
    mock_coordinator.data = {}
    sensor = BatterySensor(mock_coordinator, config_entry, "Test Robot")
    assert sensor.native_value is None


# ---------------------------------------------------------------------------
# CleaningTimeSensor / CleaningAreaSensor
# ---------------------------------------------------------------------------

def test_cleaning_time_sensor(mock_coordinator, config_entry):
    mock_coordinator.data = {DPS_CLEANING_TIME: 3600}
    sensor = CleaningTimeSensor(mock_coordinator, config_entry, "Test Robot")
    assert sensor.native_value == 3600


def test_cleaning_area_sensor(mock_coordinator, config_entry):
    mock_coordinator.data = {DPS_CLEANING_AREA: 42}
    sensor = CleaningAreaSensor(mock_coordinator, config_entry, "Test Robot")
    assert sensor.native_value == 42


# ---------------------------------------------------------------------------
# ActivitySensor
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("dps15,expected", [
    (WORK_STATUS_CHARGING, "docked"),
    (WORK_STATUS_SLEEPING, "docked"),
    (WORK_STATUS_RUNNING, "cleaning"),
    ("Recharge", "returning"),
    ("unknown_value", "unknown_value"),  # falls back to raw value
])
def test_activity_sensor(mock_coordinator, config_entry, dps15, expected):
    mock_coordinator.data = {DPS_WORK_STATUS: dps15}
    sensor = ActivitySensor(mock_coordinator, config_entry, "Test Robot")
    assert sensor.native_value == expected


# ---------------------------------------------------------------------------
# DetailedStatusSensor
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("dps15,dps122,expected", [
    ("Running", "Continue", "Cleaning"),
    ("Running", "Nosweep", "Starting"),
    ("Running", "", "Running"),
    ("Goto", "", "Going to location"),
    ("Recharge", "", "Returning to dock"),
    ("Charging", "", "Charging"),
    ("Sleeping", "", "Sleeping"),
    ("standby", "", "Standby"),
    ("Completed", "", "Completed"),
])
def test_detailed_status_sensor(mock_coordinator, config_entry, dps15, dps122, expected):
    mock_coordinator.data = {DPS_WORK_STATUS: dps15, DPS_WORK_STATUS_2: dps122}
    sensor = DetailedStatusSensor(mock_coordinator, config_entry, "Test Robot")
    assert sensor.native_value == expected


def test_detailed_status_sensor_attributes(mock_coordinator, config_entry):
    mock_coordinator.data = {DPS_WORK_STATUS: "Running", DPS_WORK_STATUS_2: "Continue"}
    sensor = DetailedStatusSensor(mock_coordinator, config_entry, "Test Robot")
    attrs = sensor.extra_state_attributes
    assert attrs["dps15"] == "Running"
    assert attrs["dps122"] == "Continue"


# ---------------------------------------------------------------------------
# ErrorSensor
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("code,expected", [
    (0, "None"),
    (1, "Front bumper stuck"),
    (4, "Rolling brush stuck"),
    (8, "Low battery"),
    ("Wheel_stuck", "Wheel stuck"),
    ("N_enough_pow", "Low battery"),
    (99, "99"),  # unknown code → str(code)
])
def test_error_sensor(mock_coordinator, config_entry, code, expected):
    mock_coordinator.data = {DPS_ERROR_CODE: code}
    sensor = ErrorSensor(mock_coordinator, config_entry, "Test Robot")
    assert sensor.native_value == expected
