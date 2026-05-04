"""Buttons: locate robot, capture current position."""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_DEVICE_NAME, DOMAIN, DPS_LOCATE
from .coordinator import EufyX8Coordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EufyX8Coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([LocateButton(coordinator, entry)])


class LocateButton(CoordinatorEntity[EufyX8Coordinator], ButtonEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: EufyX8Coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_name = "Locate"
        self._attr_unique_id = f"{entry.data['device_id']}_locate"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data["device_id"])},
            name=entry.data[CONF_DEVICE_NAME],
            manufacturer="Eufy",
            model="X8 / X8 Pro",
        )

    async def async_press(self) -> None:
        await self.coordinator.device.async_set({DPS_LOCATE: False})
        await self.coordinator.device.async_set({DPS_LOCATE: True})


