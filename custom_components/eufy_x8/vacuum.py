"""Vacuum entity for Eufy X8."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol
from homeassistant.components.vacuum import (
    StateVacuumEntity,
    VacuumEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback, async_get_current_platform
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ACTIVITY_MAP,
    CONF_DEVICE_NAME,
    DOMAIN,
    DPS_ACTIVATE,
    DPS_CLEAN_SPEED,
    DPS_LOCATE,
    DPS_RETURN_HOME,
    DPS_WORK_MODE,
    DPS_WORK_STATUS,
    FAN_SPEED_FROM_LABEL,
    FAN_SPEED_LABELS,
    FAN_SPEED_TO_LABEL,
)
from .coordinator import EufyX8Coordinator

_LOGGER = logging.getLogger(__name__)

FEATURES = (
    VacuumEntityFeature.START
    | VacuumEntityFeature.STOP
    | VacuumEntityFeature.PAUSE
    | VacuumEntityFeature.RETURN_HOME
    | VacuumEntityFeature.STATUS
    | VacuumEntityFeature.LOCATE
    | VacuumEntityFeature.FAN_SPEED
    | VacuumEntityFeature.STATE
    | VacuumEntityFeature.SEND_COMMAND
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EufyX8Coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([EufyX8Vacuum(coordinator, entry)])

    platform = async_get_current_platform()
    platform.async_register_entity_service(
        "goto",
        {
            vol.Required("x"): vol.Coerce(int),
            vol.Required("y"): vol.Coerce(int),
        },
        "async_goto_location",
    )


class EufyX8Vacuum(CoordinatorEntity[EufyX8Coordinator], StateVacuumEntity):

    _attr_has_entity_name = True
    _attr_name = None   # vacuum IS the device; HA uses the device name directly
    _attr_fan_speed_list = FAN_SPEED_LABELS
    _attr_supported_features = FEATURES

    def __init__(self, coordinator: EufyX8Coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = entry.data["device_id"]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data["device_id"])},
            name=entry.data[CONF_DEVICE_NAME],
            manufacturer="Eufy",
            model="X8 / X8 Pro",
        )

    @property
    def state(self) -> str | None:
        dps15 = self.coordinator.data.get(DPS_WORK_STATUS, "")
        return ACTIVITY_MAP.get(dps15, "idle")

    @property
    def fan_speed(self) -> str | None:
        raw = self.coordinator.data.get(DPS_CLEAN_SPEED)
        return FAN_SPEED_TO_LABEL.get(raw, raw)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        dps = self.coordinator.data
        return {
            "work_status": dps.get(DPS_WORK_STATUS),
            "cleaning_time_s": dps.get("109"),
            "cleaning_area_m2": dps.get("110"),
            "boost_iq": dps.get("118"),
        }

    async def async_start(self) -> None:
        await self.coordinator.device.async_set({DPS_WORK_MODE: "auto"})

    async def async_stop(self, **kwargs) -> None:
        await self.coordinator.device.async_set({DPS_ACTIVATE: False})

    async def async_pause(self) -> None:
        await self.coordinator.device.async_set({DPS_ACTIVATE: False})

    async def async_return_to_base(self, **kwargs) -> None:
        await self.coordinator.device.async_set({DPS_RETURN_HOME: True})

    async def async_locate(self, **kwargs) -> None:
        # Reset first to guarantee a False→True edge, since the robot is edge-triggered
        # and will ignore a True→True (no-change) command.
        await self.coordinator.device.async_set({DPS_LOCATE: False})
        await self.coordinator.device.async_set({DPS_LOCATE: True})
        asyncio.create_task(self._async_cancel_locate())

    async def _async_cancel_locate(self) -> None:
        await asyncio.sleep(5)
        await self.coordinator.device.async_set({DPS_LOCATE: False})

    async def async_set_fan_speed(self, fan_speed: str, **kwargs) -> None:
        raw = FAN_SPEED_FROM_LABEL.get(fan_speed, fan_speed)
        await self.coordinator.device.async_set({DPS_CLEAN_SPEED: raw})

    async def async_goto_location(self, x: int, y: int) -> None:
        accepted = await self.coordinator.device.async_goto(x, y)
        if not accepted:
            _LOGGER.warning("goto(%d, %d) rejected by robot", x, y)

    async def async_send_command(
        self, command: str, params: dict[str, Any] | None = None, **kwargs
    ) -> None:
        """
        goto via service call:
          command: "goto"
          params: {"x": 2283, "y": -363}
        """
        params = params or {}
        if command == "goto":
            x = int(params["x"])
            y = int(params["y"])
            accepted = await self.coordinator.device.async_goto(x, y)
            if not accepted:
                _LOGGER.warning("goto(%d, %d) rejected by robot", x, y)
        elif command == "clear":
            await self.coordinator.device.async_clear()
        else:
            _LOGGER.warning("Unknown command: %s", command)
