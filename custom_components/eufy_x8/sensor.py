"""Sensors: battery, cleaning stats, error, consumables, activity."""
from __future__ import annotations

import base64
import json
import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfArea, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ACTIVITY_MAP,
    CONF_DEVICE_NAME,
    DOMAIN,
    DPS_BATTERY,
    DPS_CLEANING_AREA,
    DPS_CLEANING_TIME,
    DPS_CONSUMABLES,
    DPS_ERROR_CODE,
    DPS_WORK_STATUS,
    DPS_WORK_STATUS_2,
)
from .coordinator import EufyX8Coordinator

_LOGGER = logging.getLogger(__name__)

ERROR_MESSAGES = {
    0: "None", "no_error": "None",
    1: "Front bumper stuck", 2: "Wheel stuck", 3: "Side brush stuck",
    4: "Rolling brush stuck", 5: "Device trapped", 6: "Device trapped",
    7: "Wheel suspended", 8: "Low battery", 9: "Magnetic boundary",
    12: "Right wall sensor", 13: "Device tilted", 14: "Insert dust collector",
    17: "Restricted area", 18: "Laser cover stuck", 19: "Laser sensor stuck",
    20: "Laser sensor blocked", 21: "Base blocked",
    "Wheel_stuck": "Wheel stuck", "R_brush_stuck": "Rolling brush stuck",
    "Crash_bar_stuck": "Front bumper stuck", "sensor_dirty": "Sensor dirty",
    "N_enough_pow": "Low battery", "Stuck_5_min": "Device trapped",
    "Fan_stuck": "Fan stuck", "S_brush_stuck": "Side brush stuck",
}

# Consumable keys from DPS 116 JSON and their display names
CONSUMABLE_LABELS = {
    "SB": "Side Brush",
    "RB": "Rolling Brush",
    "FM": "Filter",
    "SP": "Sensor Pad",
    "SS": "Side Sensor",
    "TR": "Total Runtime",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EufyX8Coordinator = hass.data[DOMAIN][entry.entry_id]
    name = entry.data[CONF_DEVICE_NAME]
    entities = [
        BatterySensor(coordinator, entry, name),
        CleaningTimeSensor(coordinator, entry, name),
        CleaningAreaSensor(coordinator, entry, name),
        ActivitySensor(coordinator, entry, name),
        DetailedStatusSensor(coordinator, entry, name),
        ErrorSensor(coordinator, entry, name),
        PositionSensor(coordinator, entry, name),
    ]
    for key, label in CONSUMABLE_LABELS.items():
        entities.append(ConsumableSensor(coordinator, entry, name, key, label))
    async_add_entities(entities)


class _Base(CoordinatorEntity[EufyX8Coordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, device_name, suffix, unique_suffix):
        super().__init__(coordinator)
        self._attr_name = suffix
        self._attr_unique_id = f"{entry.data['device_id']}_{unique_suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data["device_id"])},
            name=device_name,
            manufacturer="Eufy",
            model="X8 / X8 Pro",
        )


class BatterySensor(_Base):
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, coordinator, entry, name):
        super().__init__(coordinator, entry, name, "Battery", "battery")

    @property
    def native_value(self):
        return self.coordinator.data.get(DPS_BATTERY)


class CleaningTimeSensor(_Base):
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_device_class = SensorDeviceClass.DURATION

    def __init__(self, coordinator, entry, name):
        super().__init__(coordinator, entry, name, "Cleaning Time", "cleaning_time")

    @property
    def native_value(self):
        return self.coordinator.data.get(DPS_CLEANING_TIME)


class CleaningAreaSensor(_Base):
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfArea.SQUARE_METERS

    def __init__(self, coordinator, entry, name):
        super().__init__(coordinator, entry, name, "Cleaning Area", "cleaning_area")

    @property
    def native_value(self):
        return self.coordinator.data.get(DPS_CLEANING_AREA)


class ActivitySensor(_Base):
    def __init__(self, coordinator, entry, name):
        super().__init__(coordinator, entry, name, "Activity", "activity")

    @property
    def native_value(self):
        dps15 = self.coordinator.data.get(DPS_WORK_STATUS, "unknown")
        return ACTIVITY_MAP.get(dps15, dps15)


class ErrorSensor(_Base):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry, name):
        super().__init__(coordinator, entry, name, "Error", "error")

    @property
    def native_value(self) -> str:
        code = self.coordinator.data.get(DPS_ERROR_CODE, 0)
        return ERROR_MESSAGES.get(code, str(code))

    @property
    def extra_state_attributes(self):
        return {"error_code": self.coordinator.data.get(DPS_ERROR_CODE, 0)}


class ConsumableSensor(_Base):
    """Reports hours of use for a single consumable component."""

    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_icon = "mdi:progress-wrench"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry, device_name, key: str, label: str):
        suffix = label
        unique_suffix = f"consumable_{key.lower()}"
        super().__init__(coordinator, entry, device_name, suffix, unique_suffix)
        self._key = key

    @property
    def native_value(self) -> float | None:
        raw = self.coordinator.data.get(DPS_CONSUMABLES)
        if not raw:
            return None
        try:
            decoded = json.loads(base64.b64decode(raw).decode())
            return decoded.get("consumable", {}).get("duration", {}).get(self._key)
        except Exception:
            return None


# DPS15 + DPS122 → human-readable status
_DETAILED_STATUS: dict[tuple[str, str], str] = {
    ("Running", "Nosweep"): "Starting",
    ("Running", "Continue"): "Cleaning",
    ("Running", ""): "Running",
    ("Goto", ""): "Going to location",
    ("Recharge", ""): "Returning to dock",
    ("Charging", ""): "Charging",
    ("Sleeping", ""): "Sleeping",
    ("standby", ""): "Standby",
    ("Completed", ""): "Completed",
    ("Locating", ""): "Locating",
}


class DetailedStatusSensor(_Base):
    """Combines DPS 15 and DPS 122 into a more granular human-readable status."""

    _attr_icon = "mdi:robot-vacuum"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry, name):
        super().__init__(coordinator, entry, name, "Status", "detailed_status")

    @property
    def native_value(self) -> str:
        dps15 = self.coordinator.data.get(DPS_WORK_STATUS, "")
        dps122 = self.coordinator.data.get(DPS_WORK_STATUS_2, "")
        status = _DETAILED_STATUS.get((dps15, dps122))
        if status is None:
            status = _DETAILED_STATUS.get((dps15, ""), dps15 or "Unknown")
        return status

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "dps15": self.coordinator.data.get(DPS_WORK_STATUS),
            "dps122": self.coordinator.data.get(DPS_WORK_STATUS_2),
        }


class PositionSensor(_Base):
    """
    Last position from media.latest v3.0 (session-local coordinates).
    These are NOT goto coordinates — they cannot be used directly in goto commands.
    """

    _attr_icon = "mdi:map-marker"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry, name):
        super().__init__(coordinator, entry, name, "Last Position", "last_position")

    @property
    def native_value(self) -> str | None:
        pos = self.coordinator.last_position
        if pos is None:
            return None
        return f"{pos['x']}, {pos['y']}"

    @property
    def extra_state_attributes(self) -> dict:
        pos = self.coordinator.last_position
        if pos is None:
            return {}
        return {
            "x": pos["x"],
            "y": pos["y"],
            "captured_at": pos["captured_at"],
            "note": "session-local coordinates, not goto map coordinates",
        }
