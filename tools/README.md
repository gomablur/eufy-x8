# Eufy X8 Tools

Standalone diagnostic and setup scripts for the `eufy_x8` Home Assistant integration. These help you discover goto coordinates for locations in your home, test the local protocol, and debug without needing HA running.

## Setup

Create a virtualenv and install dependencies:

```bash
cd tools
python3 -m venv .venv
source .venv/bin/activate
pip install tinytuya requests pycryptodome
pip install scapy    # intercept_goto.py only (requires root)
```

You also need:
- A Eufy/eufylife.com account
- The robot connected to your LAN
- For `intercept_goto.py`: a Linux machine on the same network as the phone and robot

> **Note**: The local key rotates frequently (several times a day). Re-run `get_local_keys.py` whenever other scripts return errors, and update the key in your command.

## The three tools

### 1. `get_local_keys.py` — fetch device credentials

Retrieves the Tuya Device ID, IP address, and local key for every Eufy device on your account.

```bash
.venv/bin/python get_local_keys.py --email you@example.com --password yourpassword

# Or use environment variables:
export EUFY_EMAIL=you@example.com
export EUFY_PASSWORD=yourpassword
.venv/bin/python get_local_keys.py
```

Output:
```
Found 2 device(s):

  Name                            Device ID                   Local Key             IP                Online
  ------------------------------  --------------------------  --------------------  ----------------  ------
  Downstairs Clean                bfc291ad10e8247fefwnk2      abc123xyz...          192.168.1.17      True
  Upstairs Clean                  bf3b83d14f132d51b0gzpk      def456uvw...          192.168.1.144     False
```

---

### 2. `tuya_local_control.py` — send commands and monitor the robot

Communicates with the robot directly over your LAN using Tuya v3.3 (port 6668).

```bash
.venv/bin/python tuya_local_control.py \
    --device-ip 192.168.1.17 \
    --device-id bfc291ad10e8247fefwnk2 \
    --local-key abc123xyz... \
    <command>
```

**Commands:**

| Command | Description |
|---------|-------------|
| `status` | Print all current DPS values |
| `home` | Send robot back to dock |
| `goto <x> <y>` | Navigate to map coordinates (sends `clear` first, waits 35s, then sends `goto`) |
| `last_clean` | Decode DPS 142 — last clean result |
| `monitor [seconds]` | Watch all DPS updates in real time (default: 120s) |

**Examples:**

```bash
# Check current status
.venv/bin/python tuya_local_control.py --device-ip 192.168.1.17 --device-id <id> --local-key <key> status

# Send robot to bin position
.venv/bin/python tuya_local_control.py ... goto 2283 -363

# Watch for updates while using the Eufy app (useful for observing DPS 124 echoes)
.venv/bin/python tuya_local_control.py ... monitor 300
```

**Goto note**: `goto` requires the robot to be in `standby` or `Charging` state (not actively cleaning). The command automatically sends `clear`, waits 35 seconds, then sends `goto`. Skipping `clear` causes the robot to do a spot clean at the target instead of navigating there.

---

### 3. `intercept_goto.py` — capture goto coordinates from the Eufy app

When you use the Eufy app's "Go to Location" feature, the app sends exact SLAM map coordinates to the robot. This script intercepts those coordinates so you can record them for use in automations.

**Requires root** (for ARP poisoning and raw socket capture).

```bash
# Find your network interface
ip link show

# Find your machine's IP on that interface
ip addr show <iface>

# Run the intercept
sudo .venv/bin/python intercept_goto.py \
    --robot-ip 192.168.1.17 \
    --local-key abc123xyz... \
    --iface eth0 \
    --my-ip 192.168.1.50

# Then in the Eufy app: Go to Location → tap the bin (or wherever you want)
# Coordinates are printed when captured:
#   ==================================================
#   GOTO COORDINATES CAPTURED
#   x=2283  y=-363
#   ==================================================
```

**Optional arguments:**

| Argument | Description |
|----------|-------------|
| `--phone-ip <ip>` | Skip phone auto-detection (use if you know your phone's IP) |
| `--phone-mac <mac>` | Skip ARP lookup for phone |
| `--robot-mac <mac>` | Skip ARP lookup for robot |
| `--duration <sec>` | How long to wait for a goto command (default: 180s) |

**How it works:**
1. ARP-poisons your phone so its traffic to the robot passes through this machine
2. IP forwarding keeps the connection transparent — the robot still responds normally
3. Every Tuya v3.3 packet is decrypted and parsed
4. When a goto command is found, the coordinates are printed and the script exits
5. ARP entries are restored on exit (SIGINT/SIGTERM handled cleanly)

---

## Typical workflow for a new user

1. **Set up venv**: `python3 -m venv .venv && source .venv/bin/activate && pip install tinytuya requests pycryptodome`
2. **Get credentials**: `.venv/bin/python get_local_keys.py` — note Device ID, IP, Local Key
3. **Verify connection**: `.venv/bin/python tuya_local_control.py ... status`
4. **Capture coordinates**: `sudo .venv/bin/python intercept_goto.py` — use the Eufy app to send the robot to each location you care about (bin, favourite spots)
5. **Use in automations**: put the coordinates in HA `vacuum.send_command` calls

## Protocol reference

See [CLAUDE.md](CLAUDE.md) for complete DPS reference, goto command format, coordinate system details, and HA automation YAML.
