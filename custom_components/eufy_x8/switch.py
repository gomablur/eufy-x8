"""Switches: BoostIQ, Do Not Disturb, Auto Return."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_DEVICE_NAME,
    DOMAIN,
    DPS_AUTO_RETURN,
    DPS_BOOST_IQ,
    DPS_DO_NOT_DISTURB,
)
from .coordinator import EufyX8Coordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EufyX8Coordinator = hass.data[DOMAIN][entry.entry_id]
    name = entry.data[CONF_DEVICE_NAME]
    async_add_entities([
        BoostIQSwitch(coordinator, entry, name),
        DoNotDisturbSwitch(coordinator, entry, name),
        AutoReturnSwitch(coordinator, entry, name),
    ])


class _DPSSwitch(CoordinatorEntity[EufyX8Coordinator], SwitchEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, device_name, suffix, unique_suffix, dps):
        super().__init__(coordinator)
        self._attr_name = suffix
        self._attr_unique_id = f"{entry.data['device_id']}_{unique_suffix}"
        self._dps = dps
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data["device_id"])},
            name=device_name,
            manufacturer="Eufy",
            model="X8 / X8 Pro",
        )

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data.get(self._dps))

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.device.async_set({self._dps: True})

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.device.async_set({self._dps: False})


class BoostIQSwitch(_DPSSwitch):
    _attr_icon = "mdi:rocket-launch"

    def __init__(self, coordinator, entry, name):
        super().__init__(coordinator, entry, name, "BoostIQ", "boost_iq", DPS_BOOST_IQ)


class DoNotDisturbSwitch(_DPSSwitch):
    _attr_icon = "mdi:sleep"

    def __init__(self, coordinator, entry, name):
        super().__init__(coordinator, entry, name, "Do Not Disturb", "do_not_disturb", DPS_DO_NOT_DISTURB)


class AutoReturnSwitch(_DPSSwitch):
    _attr_icon = "mdi:home-import-outline"

    def __init__(self, coordinator, entry, name):
        super().__init__(coordinator, entry, name, "Auto Return", "auto_return", DPS_AUTO_RETURN)
