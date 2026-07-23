# Eufy X8 Robot Vacuum — Home Assistant Integration

> ### 🍴 This is a fork
>
> This is a fork of **[8none1/eufy-x8](https://github.com/8none1/eufy-x8)** by [@8none1](https://github.com/8none1), whose excellent work — the protocol research, the integration, the `goto` service, and the tooling — is the entire foundation this builds on. **All credit for the hard parts goes to them.**
>
> This fork adds one thing: **support for newer Eufy models that speak Tuya protocol v3.4/v3.5**, while keeping the original v3.3 devices working exactly as before. It was made to get the **X8 Pro Hybrid SES (T2276)** — which speaks Tuya **v3.5** (AES-GCM with a session-key handshake) and so timed out against the original v3.3-only client — talking to Home Assistant.
>
> **What changed:** the transport layer (`api/local.py`) now delegates the Tuya wire protocol to the well-maintained [`tinytuya`](https://github.com/jasonacox/tinytuya) library and **auto-detects the protocol version** (v3.1 / v3.3 / v3.4 / v3.5). The public interface, entities, services, and behaviour are otherwise unchanged. See [CHANGELOG.md](CHANGELOG.md) for details.
>
> If you have an original X8 / X8 Pro (T2262 / T2262EV), the upstream project is the canonical source — please star and support it there.

A Home Assistant custom integration for Eufy X8 robot vacuums using the **Tuya local protocol** (v3.3–v3.5, auto-detected — LAN, no cloud dependency after setup). Tested on the T2262, T2262EV (X8 Pro), and T2276 (X8 Pro Hybrid SES).

> **Why this exists**: The X8 (T2262 / T2262EV) is on Eufy's older Tuya platform, not the AIOT/protobuf path the modern fleet uses. The Anker AIOT MQTT broker denies subscription for these device IDs (`SUBACK 0x80`), but the X8 is still reachable via Eufy's older V1 cloud API — and that's the path my own [`eufy-clean`](https://github.com/8none1/eufy-clean) fork uses to drive it. It works for the basics. What it lacks is a `goto` service (the killer feature for "park next to the bin" automations) and it depends on the Anker cloud being up.
>
> The original Tuya-based community integration ([`mitchellrj/eufy_robovac`](https://github.com/mitchellrj/eufy_robovac)) is dormant — last commit June 2020 — and stopped working on the X8 at some point in the past, possibly due to firmware changes.
>
> This integration talks directly to the robot over Tuya v3.3 on port 6668. No cloud after the initial credential exchange, and a `goto` service so the robot can park itself somewhere convenient.

## Supported devices

| Model | Code | Protocol | Notes |
|-------|------|----------|-------|
| Eufy X8 | T2262 | Tuya v3.3 | Confirmed working (upstream) |
| Eufy X8 Pro | T2262EV | Tuya v3.3 | Confirmed working ("EV" is a hardware revision, upstream) |
| Eufy X8 Pro Hybrid SES | T2276 | Tuya v3.5 | Confirmed working (this fork) |

The T2262 / T2262EV are vacuum-only with a basic charging dock (no auto-empty, no mop). The T2276 (X8 Pro Hybrid SES) has an auto-empty station and mopping; those extra capabilities are exposed as DPS but only the common vacuum controls are surfaced as entities so far.

The protocol version is detected automatically at connect time, so no configuration is needed regardless of model.

## Features

- **Full vacuum control**: start, stop, pause, return to dock, locate
- **Fan speed control**: Low / Medium / High / Max
- **Work mode select**: Auto / Edge / Spot / No Sweep
- **Live status**: battery, cleaning time, cleaning area, activity, detailed status, errors
- **Goto service** (`eufy_x8.goto`): send the robot to any coordinates in its SLAM map (e.g. next to the bin after cleaning)
- **Locate brief service** (`eufy_x8.locate_brief`): make the robot beep for a short configurable duration — useful in automations to signal "please empty me"
- **Automatic local key refresh**: the Tuya local key rotates when the robot reconnects to cloud; the integration detects this and fetches a new key automatically
- **Switches**: BoostIQ, Auto Return

## Installation

### HACS (recommended)

1. Open HACS → Integrations → three-dot menu → Custom repositories
2. Add `https://github.com/gomablur/eufy-x8` as category **Integration**
   (for original X8 / X8 Pro on v3.3, the upstream `https://github.com/8none1/eufy-x8` is the canonical source)
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
| Downstairs Robot Activity | Sensor | HA vacuum state: docked / cleaning / returning / idle |
| Downstairs Robot Status | Sensor | More granular: Starting / Cleaning / Going to location / Returning to dock / Standby / etc. — use this in automations |
| Downstairs Robot Error | Sensor | Human-readable error description |
| Downstairs Robot BoostIQ | Switch | Boost suction on carpets |
| Downstairs Robot Auto Return | Switch | Auto-return to dock when battery low |
| Downstairs Robot Work Mode | Select | Auto / Edge / Spot / No Sweep |

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

### Why a flag helper is needed

The robot doesn't broadcast its current position or destination via any DPS value, so there is no way to distinguish "arrived at bin" from "arrived at any other goto destination" purely from the robot's state. The solution is an `input_boolean` helper (`input_boolean.vacuum_headed_to_bin`) that automation 1 sets just before dispatching the goto, and automation 2 checks before beeping. This means if you ever add other goto commands the beep won't fire spuriously.

Create the helper in HA under **Settings → Devices & Services → Helpers → Create helper → Toggle**, name it `Vacuum headed to bin`.

### Automation 1 — go to bin when cleaning ends

Triggers when the status changes from "Cleaning" to "Returning to dock" and the robot has cleaned at least 25 m². It then waits for the robot to actually dock before sending the goto (so the robot isn't mid-journey when the command arrives), sets the flag, and dispatches the goto.

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
  - service: input_boolean.turn_on
    target:
      entity_id: input_boolean.vacuum_headed_to_bin
  - service: eufy_x8.goto
    target:
      entity_id: vacuum.downstairs
    data:
      x: 2283   # replace with your bin coordinates
      y: -363
```

The 25 m² threshold means the automation only fires after a meaningful clean — not if the robot was moved off the dock briefly or ran a quick spot clean.

### Automation 2 — beep when the robot arrives at the bin

Triggers when the status changes from "Going to location" to "Standby" (robot arrived at its destination) **and** the flag is set, so the beep only fires when this automation sent it there, not for any other goto you might add in future.

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
  - condition: state
    entity_id: input_boolean.vacuum_headed_to_bin
    state: "on"
action:
  - service: input_boolean.turn_off
    target:
      entity_id: input_boolean.vacuum_headed_to_bin
  - service: eufy_x8.locate_brief
    target:
      entity_id: vacuum.downstairs
    data:
      duration: 5
```

Both automations use case-insensitive string comparison so firmware capitalisation changes don't break them.

## Known limitations

- **No room cleaning**: Room-by-room cleaning requires the AIOT cloud MQTT path. Eufy X8s (T2262/T2262EV) are on the Tuya platform and room cleaning via local protocol returns "Failed" in all tested states.
- **No map / path visualisation**: The robot clearly has its own SLAM map (goto coordinates are stable across sessions) but does not expose it on either the local or cloud protocols. The Tuya `media.latest` endpoint was investigated as a path-data source and proved unusable — see [tools/CLAUDE.md](tools/CLAUDE.md) for the full write-up so nobody has to repeat the dead-end work.
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

**`Unexpected Payload` (904) or `InvalidKey` (914) errors in logs**
Usually the local key has rotated; the integration refreshes it automatically. On v3.4/v3.5 a long-lived session can also desync and return 904 — the transport uses a fresh (non-persistent) socket per poll and drops/reconnects on any error, so this self-heals within one poll. If you see it repeatedly, check that your Eufy credentials are still valid.

**Robot ignores goto command / does a spot clean instead**
The `clear` → wait → `goto` sequence may have been disrupted. Ensure the robot is not actively cleaning when you send the goto command.

**Status stuck at idle / battery at 0%**
The robot hasn't sent a status update yet. This resolves within 1–2 minutes after the robot wakes or finishes a clean.

## Dependencies

The transport layer delegates the Tuya wire protocol to [`tinytuya`](https://github.com/jasonacox/tinytuya), which implements v3.1 / v3.3 / v3.4 / v3.5. It is declared in `manifest.json` (`tinytuya==1.20.0`) and Home Assistant installs it automatically on first load. The cloud auth path (`api/auth.py`, for local-key retrieval/refresh) uses `aiohttp`, already bundled with Home Assistant.

> Note: upstream (v3.3-only) shipped a self-contained hand-rolled Tuya client with no third-party dependency. This fork trades that for `tinytuya` in order to gain the v3.4/v3.5 session-key + AES-GCM handshake without reimplementing it.

For the `tools/` directory:
```
pip install tinytuya requests pycryptodome
pip install scapy    # intercept_goto.py only
```

## Protocol reference

See [tools/CLAUDE.md](tools/CLAUDE.md) for full DPS reference, goto command format, coordinate system explanation, and HA automation examples.

## Acknowledgements

- **[@8none1](https://github.com/8none1)** — the original author of [eufy-x8](https://github.com/8none1/eufy-x8), which this project is a fork of. The integration architecture, entity design, the `goto` service, the protocol reverse-engineering, and the `tools/` are all their work. This fork only swaps the transport layer for broader protocol-version support; everything that makes the integration good came from upstream.
- **[jasonacox/tinytuya](https://github.com/jasonacox/tinytuya)** — the Tuya local protocol library this fork's transport now delegates to (v3.1–v3.5).
- Protocol research and initial tools based on work in the [eufy-clean](https://github.com/8none1/eufy-clean) project and the broader Tuya local protocol community.
