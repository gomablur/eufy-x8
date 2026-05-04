"""Tests for constants and mappings in const.py."""
import pytest

from custom_components.eufy_x8.const import (
    ACTIVITY_MAP,
    FAN_SPEED_FROM_LABEL,
    FAN_SPEED_LABELS,
    FAN_SPEED_TO_LABEL,
    WORK_STATUS_CHARGING,
    WORK_STATUS_COMPLETED,
    WORK_STATUS_GOTO,
    WORK_STATUS_LOCATING,
    WORK_STATUS_RECHARGE,
    WORK_STATUS_RUNNING,
    WORK_STATUS_SLEEPING,
    WORK_STATUS_STANDBY,
)

ALL_WORK_STATUSES = [
    WORK_STATUS_SLEEPING,
    WORK_STATUS_CHARGING,
    WORK_STATUS_RUNNING,
    WORK_STATUS_RECHARGE,
    WORK_STATUS_COMPLETED,
    WORK_STATUS_STANDBY,
    WORK_STATUS_GOTO,
    WORK_STATUS_LOCATING,
]


def test_activity_map_covers_all_statuses():
    """Every WORK_STATUS_* constant must have an entry in ACTIVITY_MAP."""
    for status in ALL_WORK_STATUSES:
        assert status in ACTIVITY_MAP, f"Missing ACTIVITY_MAP entry for {status!r}"


def test_activity_map_values_are_valid_ha_states():
    """ACTIVITY_MAP values must be HA vacuum activity strings."""
    valid = {"docked", "cleaning", "returning", "idle", "error", "paused"}
    for status, activity in ACTIVITY_MAP.items():
        assert activity in valid, f"{status!r} maps to unknown activity {activity!r}"


def test_fan_speed_label_roundtrip():
    """FAN_SPEED_TO_LABEL and FAN_SPEED_FROM_LABEL must be exact inverses."""
    for raw, label in FAN_SPEED_TO_LABEL.items():
        assert FAN_SPEED_FROM_LABEL[label] == raw, \
            f"Round-trip failed: {raw!r} → {label!r} → {FAN_SPEED_FROM_LABEL.get(label)!r}"


def test_fan_speed_labels_list_matches_to_label():
    """FAN_SPEED_LABELS must contain exactly the values from FAN_SPEED_TO_LABEL."""
    assert set(FAN_SPEED_LABELS) == set(FAN_SPEED_TO_LABEL.values())


def test_work_status_constants_are_non_empty_strings():
    for status in ALL_WORK_STATUSES:
        assert isinstance(status, str) and status, f"Work status must be non-empty string: {status!r}"


def test_docked_states():
    """Sleeping and Charging should both map to 'docked'."""
    assert ACTIVITY_MAP[WORK_STATUS_SLEEPING] == "docked"
    assert ACTIVITY_MAP[WORK_STATUS_CHARGING] == "docked"


def test_cleaning_state():
    assert ACTIVITY_MAP[WORK_STATUS_RUNNING] == "cleaning"
    assert ACTIVITY_MAP[WORK_STATUS_GOTO] == "cleaning"


def test_returning_state():
    assert ACTIVITY_MAP[WORK_STATUS_RECHARGE] == "returning"
