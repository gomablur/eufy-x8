"""Config flow: email + password → device select (with async UDP discovery)."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback

from .api.auth import AuthError, EufyAuth
from .api.cloud import DeviceInfo, discover_devices
from .api.discovery import discover_local_ips
from .const import (
    CONF_DEVICE_ID,
    CONF_DEVICE_IP,
    CONF_DEVICE_NAME,
    CONF_EMAIL,
    CONF_LOCAL_KEY,
    CONF_PASSWORD,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# Short listen — enough to catch a robot that's already awake.
# Background discovery in __init__.py fills in IPs for sleeping robots.
DISCOVERY_TIMEOUT = 4.0


class EufyX8ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._auth: EufyAuth | None = None
        self._devices: list[DeviceInfo] = []
        self._local_ips: dict[str, str] = {}
        self._email: str = ""
        self._password: str = ""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input[CONF_EMAIL]
            password = user_input[CONF_PASSWORD]
            auth = EufyAuth(email, password)
            try:
                await auth.authenticate()
                devices = await discover_devices(auth)
                if not devices:
                    errors["base"] = "no_devices"
                else:
                    self._auth = auth
                    self._email = email
                    self._password = password
                    self._devices = devices
                    self._local_ips = await discover_local_ips(
                        [d.device_id for d in devices],
                        timeout=DISCOVERY_TIMEOUT,
                    )
                    return await self.async_step_device()
            except AuthError:
                errors["base"] = "invalid_auth"
            except (TimeoutError, asyncio.TimeoutError, OSError):
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during auth")
                errors["base"] = "unknown"
            finally:
                if errors:
                    await auth.close()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_EMAIL): str,
                vol.Required(CONF_PASSWORD): str,
            }),
            errors=errors,
        )

    async def async_step_device(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        errors: dict[str, str] = {}

        device_options = {}
        for d in self._devices:
            ip = self._local_ips.get(d.device_id, "")
            suffix = f" — {ip}" if ip else " — IP not yet discovered"
            device_options[d.device_id] = d.name + suffix

        if user_input is not None:
            device_id = user_input[CONF_DEVICE_ID]
            device = next((d for d in self._devices if d.device_id == device_id), None)
            manual_ip = (user_input.get(CONF_DEVICE_IP) or "").strip()

            if device is None:
                errors["base"] = "device_not_found"
            elif not device.local_key:
                errors["base"] = "no_local_key"
            else:
                ip = self._local_ips.get(device_id) or manual_ip
                await self._auth.close()
                await self.async_set_unique_id(device_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=device.name,
                    data={
                        CONF_EMAIL: self._email,
                        CONF_PASSWORD: self._password,
                        CONF_DEVICE_ID: device.device_id,
                        CONF_DEVICE_NAME: device.name,
                        CONF_DEVICE_IP: ip,
                        CONF_LOCAL_KEY: device.local_key,
                    },
                )

        any_missing = any(d.device_id not in self._local_ips for d in self._devices)
        schema: dict = {vol.Required(CONF_DEVICE_ID): vol.In(device_options)}
        if any_missing:
            schema[vol.Optional(CONF_DEVICE_IP, default="")] = str

        return self.async_show_form(
            step_id="device",
            data_schema=vol.Schema(schema),
            errors=errors,
            description_placeholders={
                "discovery_note": (
                    f"Auto-discovered {len(self._local_ips)}/{len(self._devices)} device(s). "
                    "Leave IP blank — the integration will find it automatically once the robot wakes."
                    if any_missing else
                    f"All {len(self._devices)} device(s) found on local network."
                )
            },
        )

    # ------------------------------------------------------------------
    # Re-auth flow (triggered when credentials expire or password changes)
    # ------------------------------------------------------------------

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> config_entries.FlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        errors: dict[str, str] = {}
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])

        if user_input is not None:
            auth = EufyAuth(user_input[CONF_EMAIL], user_input[CONF_PASSWORD])
            try:
                await auth.authenticate()
                self.hass.config_entries.async_update_entry(
                    entry,
                    data={
                        **entry.data,
                        CONF_EMAIL: user_input[CONF_EMAIL],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    },
                )
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")
            except AuthError:
                errors["base"] = "invalid_auth"
            except (TimeoutError, asyncio.TimeoutError, OSError):
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during re-auth")
                errors["base"] = "unknown"
            finally:
                await auth.close()

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({
                vol.Required(CONF_EMAIL, default=entry.data.get(CONF_EMAIL, "")): str,
                vol.Required(CONF_PASSWORD): str,
            }),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return EufyX8OptionsFlow(config_entry)


class EufyX8OptionsFlow(config_entries.OptionsFlow):
    """Allow the user to manually override the IP address."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        if user_input is not None:
            ip = (user_input.get(CONF_DEVICE_IP) or "").strip()
            if ip:
                self.hass.config_entries.async_update_entry(
                    self._config_entry,
                    data={**self._config_entry.data, CONF_DEVICE_IP: ip},
                )
            return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(
                    CONF_DEVICE_IP,
                    default=self._config_entry.data.get(CONF_DEVICE_IP, ""),
                ): str,
            }),
            description_placeholders={
                "current_ip": self._config_entry.data.get(CONF_DEVICE_IP) or "not set"
            },
        )
