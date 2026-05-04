#!/usr/bin/env python3
"""
Capture goto coordinates from DPS 124 without ARP spoofing.

When the Eufy app sends the robot to a location, the robot echoes the goto
command back on DPS 124.  This script connects directly to the robot and
watches DPS 124 for that echo — no root, no ARP, no packet capture.

Workflow:
  1. Run this script
  2. Open the Eufy app, tap Go to Location, tap the spot you want (e.g. the bin)
  3. Close/disconnect the Eufy app so it releases the connection
  4. This script will see the goto echo on next status poll or gratuitous update
     and print the coordinates

Note: Tuya devices only allow one TCP connection at a time.  If the Eufy app
is connected, this script may get connection refused or a stale state read.
Close the app first if you want the most reliable capture.

Usage:
    python watch_goto.py \\
        --device-ip 192.168.42.17 \\
        --device-id <id> \\
        --local-key <key> \\
        [--duration 120]

Get device-id and local-key from:
    python get_local_keys.py --email you@example.com
"""
from __future__ import annotations

import argparse
import base64
import json
import time

import tinytuya

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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Watch DPS 124 for goto coordinates — no root required",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--device-ip",  required=True)
    parser.add_argument("--device-id",  required=True)
    parser.add_argument("--local-key",  required=True)
    parser.add_argument("--duration", type=int, default=120,
                        help="Seconds to monitor for updates (default: 120)")
    args = parser.parse_args()

    d = tinytuya.Device(
        dev_id=args.device_id,
        address=args.device_ip,
        local_key=args.local_key,
        version=3.3,
    )
    d.set_socketTimeout(8)

    # --- Check current state first ---
    print(f"Connecting to {args.device_ip} ...")
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
    print(f"\nMonitoring for {args.duration}s ... (use the Eufy app, then close it)")
    print("Ctrl+C to stop early.\n")

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

    print("Monitoring complete — no goto captured.")
    print("Tips:")
    print("  - Close the Eufy app before running this, then use it and run again")
    print("  - Or try running this first and use the app while it monitors")


if __name__ == "__main__":
    main()
