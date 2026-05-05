# Eufy X8 Robot Vacuum — Home Assistant Integration

A Home Assistant custom integration for Eufy X8 robot vacuums using the **Tuya v3.3 local protocol** (LAN, no cloud dependency after setup). Tested on the T2262 and T2262EV (X8 Pro).

> **Why this exists**: The official Eufy integration and most forks use the cloud MQTT protocol (AIOT/protobuf over AWS IoT). Eufy X8 devices are on the older Tuya platform and their MQTT subscriptions are rejected by the Anker broker — they simply don't receive status updates over that path. This integration talks directly to the robot over your local network using Tuya v3.3 on port 6668.

## Supported devices

| Model | Code | Notes |
|-------|------|-------|
| Eufy X8 | T2262 | Confirmed working |
| Eufy X8 Pro | T2262EV | Confirmed working ("EV" is a hardware revision) |

Both are vacuum-only with a basic charging dock (no auto-empty, no mop).

## Features

- **Full vacuum control**: start, stop, pause, return to dock, locate
- **Fan speed control**: Quiet / Standard / Turbo / Max
- **Work mode select**: Auto / Edge / Spot / No Sweep
- **Live status**: battery, cleaning time, cleaning area, activity, detailed status, errors
- **Consumable tracking**: side brush, rolling brush, filter, sensor pad, side sensor, total runtime
- **Goto service** (`eufy_x8.goto`): send the robot to any coordinates in its SLAM map (e.g. next to the bin after cleaning)
- **Locate brief service** (`eufy_x8.locate_brief`): make the robot beep for a short configurable duration — useful in automations to signal "please empty me"
- **Cleaning map camera**: accumulates path data across sessions, rendered as a PNG image
- **Automatic local key refresh**: the Tuya local key rotates when the robot reconnects to cloud; the integration detects this and fetches a new key automatically
- **Switches**: BoostIQ, Auto Return

## Installation

### HACS (recommended)

1. Open HACS → Integrations → three-dot menu → Custom repositories
2. Add `https://github.com/8none1/eufy-x8` as category **Integration**
3. Search for "Eufy X8" and install
4. Restart Home Assistant

### Manual

Copy `custom_components/eufy_x8/` into your HA config's `custom_components/` directory and restart.

## Configuration

Go to **Settings → Devices & Services → Add Integration → Eufy X8 Robot Vacuum**.

You will be asked for:
- **Email** — your Eufy / eufylife.com account email
- **Password** — your Eufy account password

The integration authenticates with the Eufy cloud to retrieve device IDs and local keys, then communicates locally. After setup, cloud access is only used to refresh the local key when it rotates.

The integration will attempt to auto-discover your robot's IP address via UDP broadcast. If the robot is asleep during setup, leave the IP field blank — it will be found automatically when the robot next wakes. You can also set the IP manually via **Configure** on the integration card.

## Entities

For a device named "Downstairs Robot":

| Entity | Type | Notes |
|--------|------|-------|
| Downstairs Robot | Vacuum | start/stop/pause/return/locate/fan speed |
| Downstairs Robot Battery | Sensor | % |
| Downstairs Robot Cleaning Time | Sensor | seconds |
| Downstairs Robot Cleaning Area | Sensor | m² |
| Downstairs Robot Activity | Sensor | Sleeping / Charging / Cleaning / Returning / etc. |
| Downstairs Robot Status | Sensor | More granular: Starting / Cleaning / Going to location / Standby / etc. — use this in automations |
| Downstairs Robot Error | Sensor | Human-readable error description |
| Downstairs Robot Side Brush | Sensor | Hours of use |
| Downstairs Robot Rolling Brush | Sensor | Hours of use |
| Downstairs Robot Filter | Sensor | Hours of use |
| Downstairs Robot Sensor Pad | Sensor | Hours of use |
| Downstairs Robot Side Sensor | Sensor | Hours of use |
| Downstairs Robot Total Runtime | Sensor | Hours |
| Downstairs Robot BoostIQ | Switch | Boost suction on carpets |
| Downstairs Robot Auto Return | Switch | Auto-return to dock when battery low |
| Downstairs Robot Work Mode | Select | Auto / Edge / Spot / No Sweep |
| Downstairs Robot Cleaning Map | Camera | Accumulated path PNG |

## Custom services

### `eufy_x8.goto` — send the robot to a location

Sends the robot to a set of coordinates in its persistent SLAM map. The primary use case is positioning it next to the bin after cleaning so it's easy to empty.

```yaml
service: eufy_x8.goto
target:
  entity_id: vacuum.downstairs_robot
data:
  x: 2283   # your coordinates from intercept_goto.py
  y: -363
```

The integration automatically sends the required `clear` command before `goto` and waits the necessary ~35 seconds between them. (Skipping this causes the robot to do a spot clean at the target instead of navigating there.)

### `eufy_x8.locate_brief` — short beep

Makes the robot beep for a configurable duration then stops, instead of beeping for the full ~60 second default. Useful in automations to signal that the robot wants attention (e.g. "I'm at the bin, please empty me").

```yaml
service: eufy_x8.locate_brief
target:
  entity_id: vacuum.downstairs_robot
data:
  duration: 5   # seconds, optional — defaults to 5, max 60
```

### Finding goto coordinates

Goto coordinates are in the robot's internal SLAM map — they are stable across sessions and reboots. The only reliable way to discover them is to capture them from the Eufy app using the `intercept_goto.py` tool in `tools/`.

See [tools/README.md](tools/README.md) — or run `intercept_goto.py` while using the Eufy app's "Go to Location" feature.

```bash
cd tools/
pip install scapy pycryptodome

# Step 1: get your device ID and local key
python get_local_keys.py --email you@example.com --password yourpassword

# Step 2: intercept the goto command from the Eufy app (requires root)
sudo python intercept_goto.py \
    --robot-ip 192.168.1.x \
    --local-key <key from step 1> \
    --iface eth0 \
    --my-ip 192.168.1.y
# Then in the Eufy app: Go to Location → tap the bin
# Coordinates are printed when captured
```

## "Go to bin after cleaning" automation

A common pattern: when the robot finishes a substantial clean, send it to wait next to the bin so it's ready to empty; when it arrives, give a short beep to let you know.

This uses the `Status` sensor (not the raw vacuum state) because it provides the granular values needed — "Cleaning", "Returning to dock", "Going to location", "Standby" — whereas the vacuum entity maps several of these to the same state.

**Automation 1 — go to bin when cleaning ends**

Triggers when the status changes from "Cleaning" to "Returning to dock" and the robot has cleaned at least 25 m². It then waits for the robot to actually dock before sending the goto, so the robot isn't mid-journey when the command arrives.

```yaml
alias: Downstairs vacuum - go to bin after clean
mode: single
trigger:
  - platform: state
    entity_id: sensor.downstairs_status
condition:
  - condition: template
    value_template: >
      {{ trigger.from_state.state | lower == 'cleaning'
         and trigger.to_state.state | lower == 'returning to dock' }}
  - condition: numeric_state
    entity_id: sensor.downstairs_cleaning_area
    above: 25
action:
  - wait_for_trigger:
      - platform: state
        entity_id: vacuum.downstairs
        to: docked
    timeout: "00:15:00"
    continue_on_timeout: false
  - service: eufy_x8.goto
    target:
      entity_id: vacuum.downstairs
    data:
      x: 2283   # replace with your bin coordinates
      y: -363
```

The 25 m² threshold means the automation only fires after a meaningful clean — not if the robot was moved off the dock briefly or ran a quick spot clean.

**Automation 2 — beep when the robot arrives at the bin**

Triggers when the status changes from "Going to location" to "Standby", meaning the robot has arrived at its goto destination.

```yaml
alias: Downstairs vacuum - beep when at bin
mode: single
trigger:
  - platform: state
    entity_id: sensor.downstairs_status
condition:
  - condition: template
    value_template: >
      {{ trigger.from_state.state | lower == 'going to location'
         and trigger.to_state.state | lower == 'standby' }}
action:
  - service: eufy_x8.locate_brief
    target:
      entity_id: vacuum.downstairs
    data:
      duration: 5
```

Both automations use case-insensitive string comparison so firmware capitalisation changes don't break them.

## Coordinate systems — important note

There are two separate coordinate spaces for the X8:

| System | Used for | Stable? | How to get |
|--------|----------|---------|------------|
| **Goto coordinates** | `vacuum.send_command` goto | Yes — fixed to SLAM map | `intercept_goto.py` or Capture Position button |
| **Path data coordinates** | `Last Position` sensor, Cleaning Map camera | No — session-local, resets each clean | Automatic (from Tuya cloud API) |

Do **not** use Last Position sensor values in goto commands — they are in a different, session-local coordinate system and will point to the wrong location.

## Cleaning map camera

The `Cleaning Map` camera entity renders an accumulated view of the robot's cleaning paths across sessions, built from Tuya path data fetched after each clean. Older sessions are shown in grey; the most recent session is shown in light blue; the dock position is marked in red.

The map accumulates up to 20 sessions. Path coordinates are stored in HA persistent storage and survive restarts.

## Known limitations

- **No room cleaning**: Room-by-room cleaning requires the AIOT cloud MQTT path. Eufy X8s (T2262/T2262EV) are on the Tuya platform and room cleaning via local protocol returns "Failed" in all tested states.
- **Initial state delay**: Battery level and status default to 0 / idle until the robot sends its first status push, typically within 1–2 minutes of waking or finishing a clean.
- **Local key rotation**: The Tuya local key rotates when the robot reconnects to the Eufy cloud (several times per day). The integration handles this automatically by catching the `InvalidKey` error and fetching a fresh key.
- **IP changes**: If your router assigns a new IP, update it via **Configure** on the integration card.

## Tools

The `tools/` directory contains standalone scripts for discovering goto coordinates and testing the local protocol. See [tools/CLAUDE.md](tools/CLAUDE.md) for full protocol documentation.

| Script | Purpose |
|--------|---------|
| `get_local_keys.py` | Fetch Tuya local keys for all Eufy devices on an account |
| `tuya_local_control.py` | Send commands and monitor DPS values over local LAN |
| `intercept_goto.py` | Capture goto (x, y) coordinates by intercepting the Eufy app (requires root) |

```bash
cd tools/
pip install tinytuya requests pycryptodome   # all tools
pip install scapy                            # intercept_goto.py only

python get_local_keys.py --email you@example.com --password yourpassword
python tuya_local_control.py --device-ip 192.168.1.x --device-id <id> --local-key <key> status
python tuya_local_control.py ... goto 2283 -363
python tuya_local_control.py ... monitor 60
sudo python intercept_goto.py --robot-ip 192.168.1.x --local-key <key> --iface eth0 --my-ip 192.168.1.y
```

## Troubleshooting

**Robot not found during setup**
The UDP discovery listens for 4 seconds. If the robot is asleep it won't respond. Leave the IP field blank and the integration will find it automatically when the robot next wakes. Alternatively, enter the IP manually.

**`Unexpected Payload` or `InvalidKey` errors in logs**
The local key has rotated. The integration handles this automatically — if you see it repeatedly, check that your Eufy credentials are still valid.

**Robot ignores goto command / does a spot clean instead**
The `clear` → wait → `goto` sequence may have been disrupted. Ensure the robot is not actively cleaning when you send the goto command.

**Cleaning map is blank**
Path data is fetched after a cleaning session completes. Run a full clean cycle to populate it.

**Status stuck at idle / battery at 0%**
The robot hasn't sent a status update yet. This resolves within 1–2 minutes after the robot wakes or finishes a clean.

## Dependencies

- `tinytuya` — Tuya v3.3 local protocol (installed automatically via `requirements` in manifest)
- `Pillow` — cleaning map camera image rendering (installed automatically)

For the `tools/` directory:
```
pip install tinytuya requests pycryptodome
pip install scapy    # intercept_goto.py only
```

## Protocol reference

See [tools/CLAUDE.md](tools/CLAUDE.md) for full DPS reference, goto command format, coordinate system explanation, and HA automation examples.

## Acknowledgements

Protocol research and initial tools based on work in the [eufy-clean](https://github.com/8none1/eufy-clean) project and the broader Tuya local protocol community.
