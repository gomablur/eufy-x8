"""Select: work mode (auto / edge / spot)."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_DEVICE_NAME, DOMAIN, DPS_WORK_MODE
from .coordinator import EufyX8Coordinator

WORK_MODES = ["auto", "Edge", "Spot", "Nosweep"]
WORK_MODE_LABELS = {
    "auto": "Auto",
    "Edge": "Edge",
    "Spot": "Spot",
    "Nosweep": "No Sweep",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EufyX8Coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([WorkModeSelect(coordinator, entry)])


class WorkModeSelect(CoordinatorEntity[EufyX8Coordinator], SelectEntity):
    _attr_has_entity_name = True
    _attr_icon = "mdi:robot-vacuum"

    def __init__(self, coordinator: EufyX8Coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_name = "Work Mode"
        self._attr_unique_id = f"{entry.data['device_id']}_work_mode"
        self._attr_options = list(WORK_MODE_LABELS.values())
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data["device_id"])},
            name=entry.data[CONF_DEVICE_NAME],
            manufacturer="Eufy",
            model="X8 / X8 Pro",
        )

    @property
    def current_option(self) -> str | None:
        raw = self.coordinator.data.get(DPS_WORK_MODE, "auto")
        return WORK_MODE_LABELS.get(raw, raw)

    async def async_select_option(self, option: str) -> None:
        raw = next((k for k, v in WORK_MODE_LABELS.items() if v == option), option)
        await self.coordinator.device.async_set({DPS_WORK_MODE: raw})
