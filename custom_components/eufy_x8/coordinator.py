"""DataUpdateCoordinator for Eufy X8."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api.auth import EufyAuth
from .api.cloud import get_local_key
from .api.local import InvalidKey, TuyaDevice, TuyaException
from .const import (
    CONF_DEVICE_ID,
    CONF_DEVICE_IP,
    CONF_EMAIL,
    CONF_LOCAL_KEY,
    CONF_PASSWORD,
    DOMAIN,
    UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

PING_INTERVAL = 10
TIMEOUT = 5


class EufyX8Coordinator(DataUpdateCoordinator[dict[str, Any]]):

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._entry = entry
        self._auth = EufyAuth(
            entry.data[CONF_EMAIL],
            entry.data[CONF_PASSWORD],
        )
        self._device: TuyaDevice | None = None

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL),
        )

    def _make_device(self) -> TuyaDevice:
        return TuyaDevice(
            device_id=self._entry.data[CONF_DEVICE_ID],
            host=self._entry.data[CONF_DEVICE_IP],
            timeout=TIMEOUT,
            ping_interval=PING_INTERVAL,
            update_entity_state=self.async_request_refresh,
            local_key=self._entry.data[CONF_LOCAL_KEY],
        )

    @property
    def device(self) -> TuyaDevice:
        if self._device is None:
            self._device = self._make_device()
        return self._device

    async def _refresh_key(self) -> str:
        try:
            new_key = await get_local_key(self._auth, self._entry.data[CONF_DEVICE_ID])
            if new_key and new_key != self._entry.data[CONF_LOCAL_KEY]:
                _LOGGER.info("Local key refreshed from cloud")
                self.hass.config_entries.async_update_entry(
                    self._entry,
                    data={**self._entry.data, CONF_LOCAL_KEY: new_key},
                )
                if self._device is not None:
                    try:
                        self._device.update_local_key(new_key)
                    except InvalidKey:
                        await self._device.async_disable()
                        self._device = None
                return new_key
        except Exception as exc:
            _LOGGER.warning("Key refresh failed: %s", exc)
        return self._entry.data[CONF_LOCAL_KEY]

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            await self.device.async_get()
        except InvalidKey:
            _LOGGER.warning("Invalid local key, attempting refresh")
            await self._refresh_key()
            try:
                await self.device.async_get()
            except TuyaException as exc:
                raise UpdateFailed(f"Device update failed after key refresh: {exc}") from exc
        except TuyaException as exc:
            raise UpdateFailed(f"Device update failed: {exc}") from exc

        return self.device.state

    async def async_shutdown(self) -> None:
        if self._device is not None:
            await self._device.async_disable()
        await self._auth.close()
        await super().async_shutdown()
