"""Eufy X8 Robot Vacuum integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

from .api.discovery import TuyaLocalDiscovery
from .const import CONF_DEVICE_ID, CONF_DEVICE_IP, CONF_LOCAL_KEY, DOMAIN
from .coordinator import EufyX8Coordinator

CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["vacuum", "sensor", "switch", "button", "select"]
_DISCOVERY_KEY = f"{DOMAIN}_discovery"


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Start UDP discovery service (shared across all config entries)."""
    hass.data.setdefault(DOMAIN, {})

    async def _on_device_found(info: dict) -> None:
        """Update IP in config entry if the robot's IP has changed."""
        device_id = info.get("gwId") or info.get("devId", "")
        new_ip = info.get("ip", "")
        if not device_id or not new_ip:
            return
        for entry in hass.config_entries.async_entries(DOMAIN):
            if entry.data.get(CONF_DEVICE_ID) == device_id:
                if entry.data.get(CONF_DEVICE_IP) != new_ip:
                    _LOGGER.info("Updating IP for %s: %s → %s",
                                 entry.title, entry.data[CONF_DEVICE_IP], new_ip)
                    hass.config_entries.async_update_entry(
                        entry, data={**entry.data, CONF_DEVICE_IP: new_ip}
                    )

    if _DISCOVERY_KEY not in hass.data:
        discovery = TuyaLocalDiscovery(_on_device_found)
        try:
            await discovery.start()
            hass.data[_DISCOVERY_KEY] = discovery
            hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, discovery.close)
            _LOGGER.debug("Tuya UDP discovery started")
        except Exception:
            _LOGGER.warning("UDP discovery unavailable — IP auto-update disabled")

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})

    # Register this device with the discovery service
    discovery: TuyaLocalDiscovery | None = hass.data.get(_DISCOVERY_KEY)
    if discovery:
        discovery.add_device(entry.data[CONF_DEVICE_ID], entry.data.get(CONF_DEVICE_IP))

    coordinator = EufyX8Coordinator(hass, entry)
    entry.async_on_unload(entry.add_update_listener(coordinator.async_entry_updated))
    await coordinator.async_config_entry_first_refresh()
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    discovery: TuyaLocalDiscovery | None = hass.data.get(_DISCOVERY_KEY)
    if discovery:
        discovery.remove_device(entry.data[CONF_DEVICE_ID], entry.data.get(CONF_DEVICE_IP))

    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        coordinator: EufyX8Coordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()
    return ok
