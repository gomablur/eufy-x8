#!/usr/bin/env python3
"""
Capture goto coordinates from DPS 124 without ARP spoofing.

When the Eufy app sends the robot to a location, the robot echoes the goto
command back on DPS 124.  This script fetches a fresh local key, connects
directly to the robot, and watches DPS 124 for that echo.

No root, no ARP, no packet capture required.

Workflow:
  1. Run this script (it will fetch a fresh key automatically)
  2. Open the Eufy app, tap Go to Location, tap the spot you want (e.g. the bin)
  3. Close/disconnect the Eufy app so it releases the connection
  4. This script will print the coordinates when it sees the goto echo

Note: Tuya devices only allow one TCP connection at a time.  Close the Eufy
app before running so this script can connect cleanly.

Usage:
    python watch_goto.py [--device-ip 192.168.42.17]

    # Credentials from env vars (recommended):
    export EUFY_EMAIL=you@example.com
    export EUFY_PASSWORD=yourpassword
    python watch_goto.py

    # Or pass them directly:
    python watch_goto.py --email you@example.com --password yourpassword
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time

import tinytuya

# Import key-fetch logic from sibling script
sys.path.insert(0, os.path.dirname(__file__))
from get_local_keys import get_local_keys

DPS_COMMAND_TRANS = "124"


def _decode_dps124(value: str) -> dict | None:
    try:
        return json.loads(base64.b64decode(value).decode())
    except Exception:
        return None


def _check_for_goto(dps: dict) -> tuple[int, int] | None:
    val = dps.get(DPS_COMMAND_TRANS)
    if not val or not isinstance(val, str):
        return None
    decoded = _decode_dps124(val)
    if not decoded or decoded.get("method") != "goto":
        return None
    data = decoded.get("data", {})
    x, y = data.get("x"), data.get("y")
    if x is not None and y is not None:
        return int(x), int(y)
    return None


def _print_coords(x: int, y: int, source: str) -> None:
    print()
    print("=" * 50)
    print(f"  GOTO COORDINATES CAPTURED ({source})")
    print(f"  x={x}  y={y}")
    print("=" * 50)
    print()
    print("Use in HA service call (eufy_x8.goto):")
    print(f"  x: {x}")
    print(f"  y: {y}")
    print()


def _pick_device(devices: list[dict], device_ip: str | None) -> dict:
    if not devices:
        raise SystemExit("No devices found on this account.")
    if device_ip:
        match = next((d for d in devices if d["ip"] == device_ip), None)
        if not match:
            print(f"No device with IP {device_ip}. Available:")
            for d in devices:
                print(f"  {d['name']:<30}  {d['ip']}")
            raise SystemExit(1)
        return match
    if len(devices) == 1:
        return devices[0]
    # Multiple devices — ask
    print("Multiple devices found:")
    for i, d in enumerate(devices):
        print(f"  {i+1}. {d['name']:<30}  {d['ip']}")
    while True:
        try:
            choice = int(input("Select device number: ")) - 1
            if 0 <= choice < len(devices):
                return devices[choice]
        except (ValueError, EOFError):
            pass
        print("Invalid choice.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Watch DPS 124 for goto coordinates — fetches key automatically",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--email", default=os.environ.get("EUFY_EMAIL", ""),
                        help="Eufy email (or set EUFY_EMAIL)")
    parser.add_argument("--password", default=os.environ.get("EUFY_PASSWORD", ""),
                        help="Eufy password (or set EUFY_PASSWORD)")
    parser.add_argument("--device-ip", default=None,
                        help="Robot IP to select (optional if only one device)")
    parser.add_argument("--duration", type=int, default=120,
                        help="Seconds to monitor for updates (default: 120)")
    args = parser.parse_args()

    if not args.email or not args.password:
        parser.error(
            "Credentials required. Pass --email/--password or set "
            "EUFY_EMAIL / EUFY_PASSWORD environment variables."
        )

    # Fetch fresh local key
    devices = get_local_keys(args.email, args.password)
    device = _pick_device(devices, args.device_ip)

    print(f"\nUsing device: {device['name']}  ({device['ip']})")
    print(f"Local key:    {device['localKey']}\n")

    d = tinytuya.Device(
        dev_id=device["id"],
        address=device["ip"],
        local_key=device["localKey"],
        version=3.3,
    )
    d.set_socketTimeout(8)

    # --- Check current cached state ---
    print(f"Connecting to {device['ip']} ...")
    raw = d.status()
    if raw and "dps" in raw:
        coords = _check_for_goto(raw["dps"])
        if coords:
            _print_coords(*coords, source="cached state")
        else:
            val = raw["dps"].get(DPS_COMMAND_TRANS)
            if val:
                decoded = _decode_dps124(val)
                print(f"DPS 124 present but not a goto: {decoded}")
            else:
                print("DPS 124 not in current state (no recent goto).")
    else:
        print(f"No response: {raw}")

    # --- Monitor for real-time updates ---
    print(f"\nMonitoring for {args.duration}s ...")
    print("Use the Eufy app to send the robot somewhere, then close the app.\n")

    d.set_socketPersistent(True)
    d.set_socketTimeout(2)
    d.set_socketRetryLimit(0)

    start = time.time()
    last_heartbeat = time.time()

    try:
        while time.time() - start < args.duration:
            elapsed = time.time() - start
            try:
                msg = d.receive()
            except Exception:
                msg = None
                time.sleep(0.1)

            if msg and "dps" in msg:
                coords = _check_for_goto(msg["dps"])
                if coords:
                    _print_coords(*coords, source=f"live update at {elapsed:.0f}s")
                    return
                else:
                    print(f"[{elapsed:5.1f}s] update (no goto): {list(msg['dps'].keys())}")

            if time.time() - last_heartbeat >= 10:
                d.heartbeat()
                last_heartbeat = time.time()

    except KeyboardInterrupt:
        print(f"\nStopped after {time.time()-start:.0f}s.")
        return

    print("Monitoring complete — no goto captured.")
    print("Tips:")
    print("  - Close the Eufy app before running this, then use it while this monitors")
    print("  - If the app is connected, this script can't connect simultaneously")


if __name__ == "__main__":
    main()
