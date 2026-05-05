#!/usr/bin/env python3
"""
Test whether sending DPS 103 = False cancels an in-progress locate beep.

Usage:
    python test_locate_cancel.py --device-ip 192.168.42.17 \
        --device-id <id> --local-key <key> [--delay 5]

What it does:
    1. Sends DPS 103 = False  (reset, ensures edge)
    2. Sends DPS 103 = True   (start locate / beeping)
    3. Waits --delay seconds
    4. Sends DPS 103 = False  (cancel — this is what we're testing)

If the beeping stops after step 4 the cancel works and we can implement
auto-cancel in async_locate().  If it keeps going until the robot's
built-in timeout, we need a different approach.
"""
from __future__ import annotations

import argparse
import time

import tinytuya

DPS_LOCATE = "103"


def main() -> None:
    parser = argparse.ArgumentParser(description="Test locate cancel via DPS 103")
    parser.add_argument("--device-ip",  required=True)
    parser.add_argument("--device-id",  required=True)
    parser.add_argument("--local-key",  required=True)
    parser.add_argument("--delay", type=float, default=5.0,
                        help="Seconds to wait before cancelling (default: 5)")
    args = parser.parse_args()

    d = tinytuya.Device(
        dev_id=args.device_id,
        address=args.device_ip,
        local_key=args.local_key,
        version=3.3,
    )
    d.set_socketTimeout(8)

    print("Step 1: DPS 103 = False  (reset)")
    d.set_value(DPS_LOCATE, False)
    time.sleep(0.5)

    print("Step 2: DPS 103 = True   (start locate — robot should beep now)")
    d.set_value(DPS_LOCATE, True)

    print(f"Step 3: Waiting {args.delay}s ...")
    time.sleep(args.delay)

    print("Step 4: DPS 103 = False  (cancel — does the beeping stop?)")
    d.set_value(DPS_LOCATE, False)

    print("Done. Did the beeping stop? (y/n)")


if __name__ == "__main__":
    main()
