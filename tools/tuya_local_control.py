#!/usr/bin/env python3
"""
Local Tuya protocol control for Eufy X8 robot vacuums.

Communicates directly with the robot over your LAN using Tuya v3.3 protocol
(port 6668).  No cloud connection required once you have the local key.

Get the device ID, IP, and local key by running:
    python get_local_keys.py --email you@example.com --password yourpassword

Usage:
    python tuya_local_control.py --device-ip 192.168.1.x --device-id <id> --local-key <key> <command>

Commands:
    status                  Print all current DPS values
    home                    Send robot back to dock
    goto <x> <y>            Send robot to map coordinates (sends clear first, then waits)
    last_clean              Decode DPS 142 (last clean result) — useful for debugging
    monitor [seconds]       Watch all DPS updates in real time (default: 120s)

Goto coordinates:
    Coordinates are in the robot's internal SLAM map coordinate system.
    Use intercept_goto.py to discover the coordinates for locations in your home
    (e.g. the position next to your bin for easy emptying after a clean).

    The goto command requires:
      1. Robot must not be in an active clean (standby, charging, or completed state)
      2. A 'clear' command is sent first, then after ~35s the goto is sent
         (this is the sequence the Eufy app uses — skipping it causes spot clean)

Dependencies:
    pip install tinytuya
"""
from __future__ import annotations

import argparse
import base64
import json
import struct
import sys
import time

import tinytuya

# DPS numbers for Eufy X8 (Tuya v3.3 local protocol)
DPS_POWER         = "1"    # bool
DPS_ACTIVATE      = "2"    # bool: start/stop clean
DPS_WORK_MODE     = "5"    # str: "auto", "Nosweep", "Edge", "Spot"
DPS_WORK_STATUS   = "15"   # str: "Sleeping", "Running", "Charging", "Goto", etc.
DPS_RETURN_HOME   = "101"  # bool: True = return to dock
DPS_CLEAN_SPEED   = "102"  # str: "Quiet", "Standard", "Turbo", "Max"
DPS_LOCATE        = "103"  # bool: toggle beeper
DPS_BATTERY       = "104"  # int: 0–100
DPS_CLEANING_TIME = "109"  # int: seconds
DPS_CLEANING_AREA = "110"  # int: m²
DPS_COMMAND_TRANS = "124"  # str: base64 JSON command transport (goto etc.)
DPS_MAP_INFO      = "125"  # str: base64 JSON map metadata
DPS_LAST_CLEAN    = "142"  # str: base64 JSON last clean result

CLEAR_GOTO_WAIT = 35       # seconds to wait between 'clear' and 'goto'


def _make_device(device_ip: str, device_id: str, local_key: str) -> tinytuya.Device:
    d = tinytuya.Device(dev_id=device_id, address=device_ip,
                        local_key=local_key, version=3.3)
    d.set_socketTimeout(8)
    return d


def _encode_cmd(method: str, data: dict | None = None) -> str:
    payload: dict = {"method": method, "timestamp": round(time.time() * 1000)}
    if data:
        payload["data"] = data
    return base64.b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()


def _decode_dps(dps: dict) -> dict:
    result = {}
    for k, v in dps.items():
        if isinstance(v, str) and len(v) > 8:
            try:
                result[k] = json.loads(base64.b64decode(v).decode("utf-8"))
                continue
            except Exception:
                pass
        result[k] = v
    return result


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_status(d: tinytuya.Device) -> None:
    raw = d.status()
    if not raw or "dps" not in raw:
        print("No response from device.")
        return
    decoded = _decode_dps(raw["dps"])
    print("Current status:")
    for k, v in sorted(decoded.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 999):
        print(f"  DPS {k:>4}: {v}")


def cmd_home(d: tinytuya.Device) -> None:
    result = d.set_value(int(DPS_RETURN_HOME), True)
    print(f"Return home: {result}")


def cmd_goto(d: tinytuya.Device, x: int, y: int) -> None:
    """
    Navigate to map coordinates.

    Sends 'clear' first (stops any current task), waits CLEAR_GOTO_WAIT seconds,
    then sends 'goto'.  This matches the sequence used by the Eufy app — skipping
    the clear causes the robot to perform a spot clean at the target instead of
    navigating there.
    """
    print(f"Sending 'clear'...")
    clear_cmd = _encode_cmd("clear")
    d.set_value(int(DPS_COMMAND_TRANS), clear_cmd)

    print(f"Waiting {CLEAR_GOTO_WAIT}s before sending goto...")
    for i in range(CLEAR_GOTO_WAIT, 0, -5):
        print(f"  {i}s remaining...")
        time.sleep(5)

    print(f"Sending goto({x}, {y})...")
    goto_cmd = _encode_cmd("goto", {"target": "go", "x": x, "y": y})
    result = d.set_value(int(DPS_COMMAND_TRANS), goto_cmd)

    # Filter response: look for goto echo, not stale status push
    if result and "dps" in result:
        decoded = _decode_dps(result["dps"])
        dps124 = decoded.get("124")
        if isinstance(dps124, dict) and dps124.get("method") == "goto":
            accepted = dps124.get("result") == "O"
            print(f"  {'Accepted' if accepted else 'Rejected'}: {dps124}")
        else:
            print(f"  Response (may be stale): {result}")
    else:
        print(f"  Raw result: {result}")


def cmd_last_clean(d: tinytuya.Device) -> None:
    """Decode DPS 142 (last clean result) and dump its full structure."""
    raw = d.status()
    if not raw or "dps" not in raw:
        print("No response from device.")
        return

    dps = raw["dps"]
    val = dps.get("142")
    if val is None:
        print("DPS 142 not present.")
        print(f"Available keys: {sorted(dps.keys(), key=lambda k: int(k) if k.isdigit() else 999)}")
        return

    print(f"DPS 142 raw ({type(val).__name__}, len={len(str(val))}):")
    print(f"  {str(val)[:120]}")
    print()

    if isinstance(val, str):
        # Try base64 → JSON
        try:
            decoded = json.loads(base64.b64decode(val).decode("utf-8"))
            print("Decoded as base64 → JSON:")
            print(json.dumps(decoded, indent=2))
            return
        except Exception:
            pass

        # Try raw JSON
        try:
            decoded = json.loads(val)
            print("Decoded as raw JSON:")
            print(json.dumps(decoded, indent=2))
            return
        except Exception:
            pass

        # Try base64 → binary hex dump
        try:
            raw_bytes = base64.b64decode(val)
            print(f"Decoded as base64 → binary ({len(raw_bytes)} bytes):")
            for i in range(0, min(len(raw_bytes), 256), 16):
                chunk = raw_bytes[i:i+16]
                hex_part = " ".join(f"{b:02x}" for b in chunk)
                asc_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
                print(f"  {i:04x}  {hex_part:<47}  {asc_part}")
            if len(raw_bytes) > 256:
                print(f"  ... {len(raw_bytes) - 256} more bytes")

            # Attempt 48-byte map header parse
            if len(raw_bytes) >= 48:
                print()
                print("Map header parse attempt:")
                (version, width, height, resolution,
                 pile_x, pile_y, origin_x, origin_y,
                 robot_x, robot_y) = struct.unpack_from("<IIIIiiiiii", raw_bytes, 0)
                angle = struct.unpack_from("<f", raw_bytes, 40)[0]
                print(f"  {width}×{height}px  res={resolution}mm/px")
                print(f"  dock:  x={pile_x}, y={pile_y}")
                print(f"  robot: x={robot_x}, y={robot_y}  angle={angle:.1f}°")
        except Exception as e:
            print(f"base64 decode failed: {e}")

    elif isinstance(val, dict):
        print("DPS 142 (already dict):")
        print(json.dumps(val, indent=2))

    # Also show DPS 125 (map info) for context
    val125 = dps.get("125")
    if val125:
        print()
        print(f"DPS 125 (map info): {val125}")
        try:
            print(" →", json.loads(base64.b64decode(val125).decode()))
        except Exception:
            pass


def cmd_monitor(d: tinytuya.Device, duration: int = 120) -> None:
    """Watch all DPS updates in real time."""
    d.set_socketPersistent(True)
    d.set_socketTimeout(2)
    d.set_socketRetryLimit(0)

    print(f"Monitoring for {duration}s ... (Ctrl+C to stop)")
    print()

    last_heartbeat = time.time()
    start = time.time()

    try:
        while time.time() - start < duration:
            elapsed = time.time() - start
            try:
                msg = d.receive()
            except Exception:
                msg = None
                time.sleep(0.1)

            if msg and "dps" in msg:
                decoded = _decode_dps(msg["dps"])
                print(f"[{elapsed:6.1f}s] {decoded}")

            if time.time() - last_heartbeat >= 10:
                d.heartbeat()
                last_heartbeat = time.time()
    except KeyboardInterrupt:
        print(f"\nStopped after {time.time()-start:.0f}s.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Control an Eufy X8 robot via local Tuya protocol",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python tuya_local_control.py --device-ip 192.168.1.10 --device-id abc123 --local-key xyz status
  python tuya_local_control.py ... goto 2283 -363
  python tuya_local_control.py ... monitor 60
        """,
    )
    parser.add_argument("--device-ip",  required=True, help="Robot IP address")
    parser.add_argument("--device-id",  required=True, help="Tuya device ID")
    parser.add_argument("--local-key",  required=True, help="Tuya local key")
    parser.add_argument("command", nargs="+",
                        help="Command: status | home | goto <x> <y> | last_clean | monitor [seconds]")
    args = parser.parse_args()

    d = _make_device(args.device_ip, args.device_id, args.local_key)
    cmd = args.command[0]
    rest = args.command[1:]

    if cmd == "status":
        cmd_status(d)
    elif cmd == "home":
        cmd_home(d)
    elif cmd == "goto":
        if len(rest) < 2:
            parser.error("goto requires x and y: goto <x> <y>")
        cmd_goto(d, int(rest[0]), int(rest[1]))
    elif cmd == "last_clean":
        cmd_last_clean(d)
    elif cmd == "monitor":
        duration = int(rest[0]) if rest else 120
        cmd_monitor(d, duration)
    else:
        parser.error(f"Unknown command '{cmd}'. "
                     f"Valid commands: status, home, goto, last_clean, monitor")


if __name__ == "__main__":
    main()
