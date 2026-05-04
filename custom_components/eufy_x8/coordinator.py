"""DataUpdateCoordinator for Eufy X8."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api.auth import EufyAuth
from .api.cloud import get_local_key, get_path_data
from .api.local import InvalidKey, TuyaDevice, TuyaException
from .const import (
    CONF_DEVICE_ID,
    CONF_DEVICE_IP,
    CONF_EMAIL,
    CONF_LOCAL_KEY,
    CONF_PASSWORD,
    DOMAIN,
    DPS_WORK_STATUS,
    UPDATE_INTERVAL,
    WORK_STATUS_CHARGING,
    WORK_STATUS_COMPLETED,
    WORK_STATUS_GOTO,
    WORK_STATUS_RUNNING,
)

_LOGGER = logging.getLogger(__name__)

PING_INTERVAL = 10
TIMEOUT = 5
MAP_STORE_VERSION = 1
# States that indicate a cleaning session was in progress
_CLEANING_STATES = {WORK_STATUS_RUNNING, WORK_STATUS_GOTO}
# States that indicate the session has ended
_DONE_STATES = {WORK_STATUS_COMPLETED, WORK_STATUS_CHARGING, "standby", "Sleeping"}


class EufyX8Coordinator(DataUpdateCoordinator[dict[str, Any]]):

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._entry = entry
        self._auth = EufyAuth(
            entry.data[CONF_EMAIL],
            entry.data[CONF_PASSWORD],
        )
        self._device: TuyaDevice | None = None
        self._prev_status: str = ""
        self._was_cleaning: bool = False

        # Persistent map store: accumulates path points across sessions
        self._map_store = Store(
            hass,
            version=MAP_STORE_VERSION,
            key=f"{DOMAIN}_map_{entry.data[CONF_DEVICE_ID]}",
        )
        self.map_data: dict[str, Any] = {"sessions": []}
        self.last_position: dict[str, Any] | None = None

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL),
        )

    async def async_config_entry_first_refresh(self) -> None:
        stored = await self._map_store.async_load()
        if stored:
            self.map_data = stored
        await super().async_config_entry_first_refresh()

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

        state = self.device.state
        await self._check_clean_completed(state)
        return state

    async def _check_clean_completed(self, state: dict) -> None:
        """Detect cleaning session end and fetch path data from cloud."""
        status = state.get(DPS_WORK_STATUS, "")
        if not status:
            return

        if status in _CLEANING_STATES:
            self._was_cleaning = True

        if self._was_cleaning and status in _DONE_STATES and self._prev_status not in _DONE_STATES:
            self._was_cleaning = False
            _LOGGER.debug("Clean session completed, fetching path data")
            self.hass.async_create_task(self._fetch_and_store_path())

        # Auto-capture position when goto finishes (robot arrives at target)
        if self._prev_status == WORK_STATUS_GOTO and status == "standby":
            _LOGGER.debug("Goto completed, auto-capturing position")
            self.hass.async_create_task(self.async_capture_position())

        self._prev_status = status

    async def async_capture_position(self) -> dict[str, Any] | None:
        """
        Fetch the most recent path data point from cloud and store it.

        Note: media.latest v3.0 returns session-local coordinates, not absolute
        map (goto) coordinates. The stored value is useful for comparing positions
        within the same cleaning session, but cannot be reliably converted to goto
        coordinates. Use the ARP intercept tool to capture goto coordinates directly.
        """
        try:
            points = await get_path_data(self._auth, self._entry.data[CONF_DEVICE_ID])
            if not points:
                _LOGGER.warning("No position data returned from cloud")
                return None
            pt = points[-1]
            self.last_position = {
                "x": pt["x"],
                "y": pt["y"],
                "captured_at": datetime.now().isoformat(),
            }
            _LOGGER.info("Captured session-local position: (%d, %d)", pt["x"], pt["y"])
            self.async_update_listeners()
            return self.last_position
        except Exception as exc:
            _LOGGER.warning("Failed to capture position: %s", exc)
            return None

    async def _fetch_and_store_path(self) -> None:
        try:
            points = await get_path_data(self._auth, self._entry.data[CONF_DEVICE_ID])
            if not points:
                return
            session = {
                "timestamp": datetime.now().isoformat(),
                "points": points,
            }
            self.map_data.setdefault("sessions", []).append(session)
            # Keep last 20 sessions
            self.map_data["sessions"] = self.map_data["sessions"][-20:]
            await self._map_store.async_save(self.map_data)
            _LOGGER.info("Stored %d path points from clean session", len(points))
            await self.async_request_refresh()
        except Exception as exc:
            _LOGGER.warning("Failed to fetch path data: %s", exc)

    async def async_shutdown(self) -> None:
        if self._device is not None:
            await self._device.async_disable()
        await self._auth.close()
        await super().async_shutdown()
