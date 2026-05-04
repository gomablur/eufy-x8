"""Config flow: email + password → all devices added automatically."""
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


def _make_entry_data(
    email: str, password: str, device: "DeviceInfo", local_ips: dict[str, str]
) -> dict:
    return {
        CONF_EMAIL: email,
        CONF_PASSWORD: password,
        CONF_DEVICE_ID: device.device_id,
        CONF_DEVICE_NAME: device.name,
        CONF_DEVICE_IP: local_ips.get(device.device_id, ""),
        CONF_LOCAL_KEY: device.local_key,
    }


class EufyX8ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

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
                    local_ips = await discover_local_ips(
                        [d.device_id for d in devices],
                        timeout=DISCOVERY_TIMEOUT,
                    )

                    configured_ids = {
                        e.data[CONF_DEVICE_ID]
                        for e in self.hass.config_entries.async_entries(DOMAIN)
                        if CONF_DEVICE_ID in e.data
                    }
                    new_devices = [d for d in devices if d.device_id not in configured_ids]

                    if not new_devices:
                        await auth.close()
                        return self.async_abort(reason="already_configured")

                    # Schedule a flow for every device after the first — each will create
                    # its own entry via async_step_import without user interaction.
                    for device in new_devices[1:]:
                        self.hass.async_create_task(
                            self.hass.config_entries.flow.async_init(
                                DOMAIN,
                                context={"source": config_entries.SOURCE_IMPORT},
                                data=_make_entry_data(email, password, device, local_ips),
                            )
                        )

                    first = new_devices[0]
                    await auth.close()
                    await self.async_set_unique_id(first.device_id)
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=first.name,
                        data=_make_entry_data(email, password, first, local_ips),
                    )
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

    async def async_step_import(
        self, import_data: dict[str, Any]
    ) -> config_entries.FlowResult:
        """Create an entry from programmatic discovery (additional devices from async_step_user)."""
        await self.async_set_unique_id(import_data[CONF_DEVICE_ID])
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=import_data[CONF_DEVICE_NAME],
            data=import_data,
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
