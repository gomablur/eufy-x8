# eufy-x8 tools/ — Claude Context

## What this directory is

Standalone diagnostic and setup scripts for the `eufy_x8` Home Assistant integration.
These tools help users discover goto coordinates for their home, test the local protocol,
and debug the integration — without needing HA running.

They are generic (no hardcoded credentials or device details) and intended to be published
as part of the integration repository.

## The four tools

| Script | Purpose | Requires root? |
|--------|---------|---------------|
| `get_local_keys.py` | Fetch Tuya local keys for all Eufy devices on an account | No |
| `tuya_local_control.py` | Send commands and monitor DPS over local LAN | No |
| `intercept_goto.py` | Capture goto (x,y) coords by intercepting the Eufy app | Yes (ARP) |
| `dump_path_data.py` | Fetch & decode cleaning path data from Tuya cloud API; probe APIs; render PNG | No |

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

## Cloud API (Tuya Mobile API — `a1.tuyaeu.com/api.json`)

Path data is only available via the Tuya cloud REST API, not the local protocol.
Auth: Eufy login → Tuya UID token → RSA-encrypted password → SID.

### Working endpoints (exhaustively probed, May 2026)

| Action | Version | Notes |
|--------|---------|-------|
| `tuya.m.device.media.latest` | `3.0` | **Only real path data source.** Returns `{dataList: [...], hasNext: bool}` |
| `tuya.m.device.media.latest` | `1.0`, `2.0` | Succeed but always return empty `dataList` |
| `tuya.m.device.dp.get` | `1.0`, `2.0` | Succeeds with pagination payload but returns 0 records — needs `dpIds` list payload |
| `tuya.m.device.info.get` | `1.0` | Succeeds but returns 0 records — needs correct payload |

Everything else (`tuya.m.robot.*`, `tuya.m.device.media.history/list/record.*`, etc.) returns
`API_OR_API_VERSION_WRONG` — these action names don't exist for this account/region.

### Definitively ruled out (do not re-probe)

All probing was done with 6 API versions (1.0, 2.0, 2.1, 3.0, 3.1, 4.0) and multiple payload
variants. `API_OR_API_VERSION_WRONG` means the action name does not exist for this account/region —
it is not a version mismatch. These are dead:

**All `tuya.m.robot.*` actions** — none exist for this device/account:
- `tuya.m.robot.history.list`, `tuya.m.robot.history.latest`
- `tuya.m.robot.map.get`, `tuya.m.robot.map.latest`
- `tuya.m.robot.path.get`, `tuya.m.robot.media.latest`

**All `tuya.m.device.media.*` variants except `latest`**:
- `tuya.m.device.media.history` — not found
- `tuya.m.device.media.list` — not found
- `tuya.m.device.media.record.list`, `tuya.m.device.media.record.latest` — not found
- `tuya.m.device.media.getLatestMessage` — not found
- `tuya.m.device.media.map`, `tuya.m.device.media.path` — not found

**`tuya.m.device.status.get`** — not found on any version.

**API versions 2.1, 3.1, 4.0** — always `API_OR_API_VERSION_WRONG` for every action tested.

**`tuya.m.device.media.detail`** — the action exists (returns `REMOTE_API_PARAM_ALL_INPUT_LOSS`,
not `API_OR_API_VERSION_WRONG`), but every parameter combination tried failed. Versions 1.0, 2.0,
3.0 all tried. Payloads attempted:
- Time ranges: `startTime`/`endTime` (both seconds and milliseconds epoch)
- Type fields: `type: "path"`, `type: "map"`, `type: 0`, `type: 1`, `dataType: 0`, `dataType: 1`
- ID fields: `mediaId: "0"`, `mediaId: 0`, `msgId: "0"`, `recordId: "0"`
- Combos: `start`+`size`, `start`+`size`+`type`, `uid`+`start`+`size`,
  `startTime`+`endTime`+`type`, `startTime`+`endTime`+`size`
- Still needs an ID token from the `media.latest` full response (not just `dataList`). Check
  `--raw-response` output after a clean to see if a `msgId` or similar field appears.

**`images.tuyaeu.com`** — server responds but 403 on every combination tried:
- URL patterns: `/{devId}/map.png`, `/{devId}/latest.png`, `/device/{devId}/map.png`,
  `/map/{devId}.png`, `/{devId}/path.png`, `/{devId}/{mapId}.png`, `/{devId}/{mapId}/map.png`,
  `/map/{devId}/{mapId}.png`
- Auth variants: no auth, `sid` header, `sid` cookie, `Bearer {sid}`, `Bearer {access_token}`,
  `token` header, `access_token` header, `?sid=` query param
- Conclusion: needs a device-signed URL or certificate-bound token we cannot generate from the
  mobile API credentials alone.

**`px.tuyaeu.com`** — DNS does not resolve. Domain is dead/retired.

### media.latest v3.0 — actual behaviour (confirmed 2026-05-04)

**Always returns exactly 1 record** regardless of robot state (docked, cleaning, mid-session,
post-session). Pagination never occurs (`hasNext` always `False`).

The data is **not confirmed position data**. A full clean was monitored with 15-second polling
(~112 polls, robot ran until battery died). The plotted values do not form a recognisable map
and field 3 jumps by thousands of units between consecutive polls.

**Record format varies by session phase:**

| Phase | Fields present | Notes |
|-------|---------------|-------|
| Session start / end | 1, 2, 3, 4, 5, 6, 8 | Consistent values ~(216, 918); likely a status/reference record |
| Localisation (leaving dock) | Different structure, no fields 1 & 3 | Robot finding its map position |
| Active cleaning | 1, 2, 3 only | Values change each poll; meaning unknown |

**Field ranges across a full clean:**

| Field | Range | Notes |
|-------|-------|-------|
| 1 | 216–4726 | ~3400–4700 during cleaning |
| 3 | 320–6283 | Large jumps between polls; unknown meaning |
| 4 | 4977 | Session start/end only; possibly map width |
| 5 | 3910–3992 | Session start/end only; possibly map height |
| 6 | 2556–2648 | Session start/end only; unknown |
| 8 | 262 | Session start only; absent at end |

**Conclusion**: `media.latest v3.0` is not a usable source of cleaning path or live position
data for these devices. What it actually encodes is unknown. Dead end — do not re-investigate.

### `dump_path_data.py` usage

```bash
# Fetch path data and render PNG
python dump_path_data.py --email you@example.com --password 'pass' \
    --device-id <id> --png /tmp/map.png

# Dump full API response to look for hidden result fields
python dump_path_data.py ... --raw-response

# Probe all API action/version combos
python dump_path_data.py ... --probe-all

# Probe media.detail parameter variants
python dump_path_data.py ... --probe-detail

# Probe images.tuyaeu.com URL patterns with auth variants
python dump_path_data.py ... --probe-urls [--map-id <id>]
```

Dependencies: `pip install requests pycryptodome Pillow`

## What still needs doing

- [ ] Downstairs bin goto coordinates — need to re-capture after a full clean (robot was
      confused after test session 2026-05-04; SLAM needs a clean run to re-localise).
      Previously confirmed as x=2283, y=-363 but stability across reboots unverified.
      Use `watch_goto.py` after the next full clean.
- [ ] Upstairs robot (T2262EV, 192.168.42.144) — goto coordinates not yet captured
- [ ] HA automation — "go to bin after clean" (trigger: state returning → docked,
      action: eufy_x8.goto x=2283 y=-363)
- [ ] DPS 142 (`last_clean`) — absent on all test cleans; may only appear after a fully
      completed clean+dock cycle

## Dead ends (do not re-investigate)

- `media.latest v3.0` — not usable path/position data (see section above)
- `media.detail`, `images.tuyaeu.com`, `px.tuyaeu.com` — all dead (see cloud API section)

## Dependencies

```
pip install tinytuya requests pycryptodome
pip install scapy    # intercept_goto.py only
pip install Pillow   # dump_path_data.py --png only
```

## Related files in the integration

| File | Purpose |
|------|---------|
| `custom_components/eufy_x8/api/local.py` | Tuya v3.3 device, `async_goto()`, `async_clear()` |
| `custom_components/eufy_x8/coordinator.py` | Key rotation, session detection, path accumulation |
| `custom_components/eufy_x8/vacuum.py` | `async_send_command` — `goto` / `clear` entry points |
| `custom_components/eufy_x8/sensor.py` | Status, activity, position sensors |
