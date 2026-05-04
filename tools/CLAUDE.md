# eufy-x8 tools/ — Claude Context

## What this directory is

Standalone diagnostic and setup scripts for the `eufy_x8` Home Assistant integration.
These tools help users discover goto coordinates for their home, test the local protocol,
and debug the integration — without needing HA running.

They are generic (no hardcoded credentials or device details) and intended to be published
as part of the integration repository.

## The three tools

| Script | Purpose | Requires root? |
|--------|---------|---------------|
| `get_local_keys.py` | Fetch Tuya local keys for all Eufy devices on an account | No |
| `tuya_local_control.py` | Send commands and monitor DPS over local LAN | No |
| `intercept_goto.py` | Capture goto (x,y) coords by intercepting the Eufy app | Yes (ARP) |

### Typical workflow for a new user

1. `get_local_keys.py` — get device ID, IP, and local key
2. `tuya_local_control.py ... status` — verify connection
3. `intercept_goto.py` — for each location they care about (bin, rooms), use the Eufy
   app to send the robot there while this intercepts the coordinates
4. Put those coordinates in HA automations using `vacuum.send_command`

## Protocol reference (Eufy X8, Tuya v3.3 local)

### Key DPS numbers

| DPS | Name | Type | Notes |
|-----|------|------|-------|
| 15 | work_status | str | Robot state — see states below |
| 101 | return_home | bool | True = go to dock |
| 102 | clean_speed | str | "Quiet" / "Standard" / "Turbo" / "Max" |
| 103 | locate | bool | Toggle beeper |
| 104 | battery | int | 0–100% |
| 109 | cleaning_time | int | Seconds |
| 110 | cleaning_area | int | m² |
| 122 | work_status_2 | str | Granular sub-state |
| 124 | command_trans | str | Base64 JSON command transport |
| 125 | map_info | str | Base64 JSON: `{"defaultID": N, "version": N}` |
| 142 | last_clean | str | Base64 JSON last clean result — contents not fully decoded yet |

### DPS 15 work_status values

| Value | Meaning |
|-------|---------|
| `Sleeping` | Idle on dock, deep sleep |
| `Charging` | On dock, charging |
| `standby` | Off dock, not cleaning |
| `Running` | Cleaning |
| `Goto` | Navigating to goto target |
| `Recharge` | Returning to dock |
| `Completed` | Just finished cleaning |
| `Locating` | Finding position |

### DPS 122 work_status_2 values (during Running)

| Value | Meaning |
|-------|---------|
| `Nosweep` | Just started, leaving dock area |
| `Continue` | Actively sweeping |

### Goto command (DPS 124)

The goto command is sent as base64-encoded JSON to DPS 124:

```python
import base64, json, time
payload = {
    "method": "goto",
    "data": {"target": "go", "x": X, "y": Y},
    "timestamp": round(time.time() * 1000),
}
cmd = base64.b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
device.set_value(124, cmd)
```

**Critical**: a `clear` command must be sent ~35 seconds before `goto`. Without it the
robot performs a spot clean at the target instead of navigating there. This is the
sequence the Eufy app always uses. `tuya_local_control.py goto` handles this automatically.

```python
# clear command (same format, no data field)
payload = {"method": "clear", "timestamp": round(time.time() * 1000)}
```

The robot echoes back: `{"method": "goto", "result": "O", "timestamp": ...}` (O = OK, F = Failed).

### Coordinate system

Goto coordinates are in the robot's persistent SLAM map — stable across sessions and
reboots. They are NOT the same as the `media.latest v3.0` path data coordinates, which
are session-local.

The only reliable way to discover goto coordinates for a location is to use
`intercept_goto.py` while the Eufy app sends the robot there.

### Local key rotation

The Tuya local key (`localKey`) rotates whenever the robot reconnects to the Eufy cloud —
this can happen multiple times per day (after sleep, after returning from a clean, etc.).

Always run `get_local_keys.py` at the start of a session. The HA integration handles
rotation automatically (detects `InvalidKey` errors, fetches fresh key, retries).

## Integration automation (HA)

After a clean session, send the robot to a target location using:

```yaml
service: vacuum.send_command
target:
  entity_id: vacuum.your_robot
data:
  command: goto
  params:
    x: 2283   # replace with your coordinates from intercept_goto.py
    y: -363
```

Trigger: state change `returning` → `docked` (robot just finished a clean and docked).

The `async_send_command` in `vacuum.py` handles the `goto` command, which calls
`coordinator.device.async_goto(x, y)` in `api/local.py`.

## What still needs doing

- [ ] Upstairs robot (T2262EV, 192.168.42.144) — goto coordinates not yet captured
- [ ] HA automation YAML — create the "go to bin after clean" automation in HA
- [ ] DPS 142 (`last_clean`) — contents not fully decoded; may contain useful data
- [ ] `accumulate_map.py` (in eufy-clean/standalone) — experimental path accumulator,
      not yet proven reliable enough to move here

## Dependencies

```
pip install tinytuya requests pycryptodome
pip install scapy    # intercept_goto.py only
```

## Related files in the integration

| File | Purpose |
|------|---------|
| `custom_components/eufy_x8/api/local.py` | Tuya v3.3 device, `async_goto()`, `async_clear()` |
| `custom_components/eufy_x8/coordinator.py` | Key rotation, session detection, path accumulation |
| `custom_components/eufy_x8/vacuum.py` | `async_send_command` — `goto` / `clear` entry points |
| `custom_components/eufy_x8/sensor.py` | Status, activity, position sensors |
