# eufy-x8 — Improvement Handoff

This document is a brief for a Claude instance tasked with improving the
eufy-x8 Home Assistant integration. It summarises what needs doing, why,
and where the relevant code lives. Read the files referenced before making
changes.

## Context

eufy-x8 is a Home Assistant custom integration for Eufy X8 robot vacuums
using the Tuya v3.3 local protocol. It is well-featured (goto, cleaning map,
consumables, key rotation) but lags behind HA quality standards in a few
areas identified by comparison with the robovac integration.

Key files:
- `custom_components/eufy_x8/config_flow.py`
- `custom_components/eufy_x8/coordinator.py`
- `custom_components/eufy_x8/sensor.py`
- `custom_components/eufy_x8/vacuum.py`
- `custom_components/eufy_x8/button.py`
- `custom_components/eufy_x8/switch.py`
- `custom_components/eufy_x8/select.py`
- `custom_components/eufy_x8/camera.py`
- `custom_components/eufy_x8/const.py`
- `tests/` — existing test suite

---

## Task 1 — Add `DeviceInfo` to all entities  *(highest priority)*

**Problem**: No entity sets `_attr_device_info`, so all entities appear as
orphaned items in HA rather than grouped under a single device card. This is
a hard HA quality requirement.

**What to do**: Add a shared `DeviceInfo` property to `_Base` in `sensor.py`
and equivalent base classes in `vacuum.py`, `button.py`, `switch.py`,
`select.py`, and `camera.py`.

The device_id is available via `entry.data["device_id"]`. The device name is
`entry.data[CONF_DEVICE_NAME]`. Use:

```python
from homeassistant.helpers.entity import DeviceInfo
from .const import DOMAIN

@property
def device_info(self) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, self._entry.data["device_id"])},
        name=self._entry.data[CONF_DEVICE_NAME],
        manufacturer="Eufy",
        model="X8 / X8 Pro",
    )
```

All entities across all platform files must return the same
`identifiers={(DOMAIN, device_id)}` so HA links them to one device.

---

## Task 2 — Switch to `has_entity_name = True`  *(follows Task 1)*

**Problem**: `_Base.__init__` sets `_attr_name = f"{device_name} {suffix}"`.
The HA-recommended pattern is `_attr_has_entity_name = True` with the suffix
as the entity name — the device name prefix is then injected automatically
from `DeviceInfo`.

**What to do**: After Task 1 is done:
1. Set `_attr_has_entity_name = True` on `_Base` (sensor.py) and all other
   entity base classes.
2. Change `_attr_name` to just the suffix (e.g. `"Battery"`, `"Activity"`).
3. Remove the `device_name` parameter from `_Base.__init__` where it is only
   used for name construction — it is still needed for `DeviceInfo`.

Check all six platform files: `sensor.py`, `vacuum.py`, `button.py`,
`switch.py`, `select.py`, `camera.py`.

---

## Task 3 — Mark diagnostic entities with `EntityCategory.DIAGNOSTIC`

**Problem**: Sensors like consumables, position, detailed status, and error
code clutter the main device card. They belong in the diagnostics section.

**What to do**: Add to the relevant sensor classes in `sensor.py`:

```python
from homeassistant.helpers.entity import EntityCategory

class ConsumableSensor(_Base):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

class PositionSensor(_Base):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

class DetailedStatusSensor(_Base):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

class ErrorSensor(_Base):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
```

Battery, cleaning time, cleaning area, and activity should remain on the main
card (no category set).

---

## Task 4 — Add re-auth flow to `config_flow.py`

**Problem**: If credentials expire (password change, session invalidation),
the integration fails silently with no path to recovery other than deleting
and re-adding it. HA will surface a re-auth notification if
`async_step_reauth` is implemented.

**What to do**: Add to `EufyX8ConfigFlow`:

```python
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
        except Exception:
            errors["base"] = "cannot_connect"
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
```

Also add `"reauth_successful"` and `"reauth_confirm"` keys to
`strings.json` and `translations/en.json`.

---

## Task 5 — Improve error discrimination in `config_flow.async_step_user`

**Problem**: The current except clause catches all non-auth exceptions and
reports them as `cannot_connect`, making it impossible to distinguish a
network failure from a malformed response or a bug.

Current code in `config_flow.py` lines 69–73:
```python
except AuthError:
    errors["base"] = "invalid_auth"
except Exception:
    _LOGGER.exception("Unexpected error during auth")
    errors["base"] = "cannot_connect"
```

**What to do**: Import and catch specific exceptions from the API layer where
possible. At minimum, separate network/timeout errors from unexpected errors:

```python
import asyncio
except AuthError:
    errors["base"] = "invalid_auth"
except (TimeoutError, asyncio.TimeoutError, OSError):
    errors["base"] = "cannot_connect"
except Exception:
    _LOGGER.exception("Unexpected error during auth")
    errors["base"] = "unknown"
```

Add `"unknown"` to `strings.json` error section if not already present.

---

## Task 6 — Add CI via GitHub Actions

**Problem**: There is no CI. Regressions in protocol handling or HA API
usage go undetected.

**What to do**: Create `.github/workflows/ci.yml` with:

1. **hassfest** — validates the integration against HA standards
2. **HACS validation** — validates HACS metadata
3. **pytest** — runs the existing test suite in `tests/`

Minimal starting point:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: home-assistant/actions/hassfest@master

  hacs:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: hacs/action@main
        with:
          category: integration

  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install pytest pytest-asyncio pytest-homeassistant-custom-component
      - run: pytest tests/ -v
```

---

## Task 7 — Test the key-rotation path in coordinator

**Problem**: The `InvalidKey` branch in `coordinator._async_update_data`
(lines 115–121) is not covered by tests. It is the most critical error
recovery path in the integration.

**What to do**: Add to `tests/test_coordinator.py`:

- A test that makes `device.async_get()` raise `InvalidKey` on first call,
  succeed on second call after `_refresh_key()` is invoked — verify the key
  is updated in the config entry.
- A test that raises `InvalidKey` and then `TuyaException` on retry — verify
  `UpdateFailed` is raised.
- A test that `_refresh_key` logs a warning and returns the existing key when
  the cloud call fails.

Use `unittest.mock.AsyncMock` and `patch` to mock `get_local_key` and
`device.async_get`.

---

## Task ordering

Do these in order — Tasks 1 and 2 change the base class used by everything
else, so get them right first:

1. Task 1 — DeviceInfo
2. Task 2 — has_entity_name
3. Task 3 — EntityCategory.DIAGNOSTIC
4. Task 4 — re-auth flow
5. Task 5 — error discrimination
6. Task 6 — CI
7. Task 7 — coordinator tests

Run `pytest tests/` after each task to catch regressions.
