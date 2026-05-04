#!/usr/bin/env python3
"""
Capture goto coordinates from the Eufy app via ARP intercept.

When you use the Eufy app to send the robot to a location ("Go to Location"),
the app sends a goto command containing the exact SLAM map coordinates (x, y).
This script intercepts that command so you can record the coordinates for use
in automations (e.g. sending the robot to the bin after cleaning).

How it works:
  1. ARP-poisons your phone so its traffic to the robot passes through this machine
  2. IP forwarding keeps the connection transparent — the robot still responds normally
  3. Every Tuya v3.3 packet is decrypted and parsed
  4. When a goto command is found, the coordinates are printed and the script exits

Requirements:
  - Must run as root:  sudo python intercept_goto.py ...
  - pip install scapy pycryptodome
  - A Linux machine on the same network as the phone and robot
  - Know your network interface name (e.g. eth0, wlan0, enp3s0)

One-time setup:
  1. Run get_local_keys.py to get your device ID, IP, and local key
  2. Find your network interface: ip link show
  3. Find your machine's IP on that interface: ip addr show <iface>

Usage:
    sudo python intercept_goto.py \\
        --robot-ip 192.168.1.x \\
        --local-key <key from get_local_keys.py> \\
        --iface eth0 \\
        --my-ip 192.168.1.y

    Then open the Eufy app and tap "Go to Location" → tap anywhere on the map.
    The coordinates will be printed and the script will exit.

Optional arguments:
    --phone-ip <ip>   Skip auto-detection (faster — use if you know your phone's IP)
    --phone-mac <mac> Skip ARP lookup for phone
    --robot-mac <mac> Skip ARP lookup for robot
    --duration <sec>  How long to wait for a goto command (default: 180s)
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import signal
import sys
import threading
import time

TUYA_PORT = 6668


# ---------------------------------------------------------------------------
# Tuya v3.3 packet decryption
# ---------------------------------------------------------------------------

def _tuya_decrypt(payload: bytes, key: bytes) -> bytes | None:
    from Crypto.Cipher import AES
    if not payload:
        return None
    if payload[:3] == b"3.3":
        payload = payload[15:]
    if len(payload) % 16 != 0:
        return None
    try:
        cipher = AES.new(key[:16], AES.MODE_ECB)
        plain = cipher.decrypt(payload)
        pad = plain[-1]
        if 1 <= pad <= 16:
            plain = plain[:-pad]
        return plain
    except Exception:
        return None


def parse_tuya_packet(data: bytes, key: bytes) -> dict | None:
    if len(data) < 16 or data[:4] != b"\x00\x00U\xaa":
        return None
    cmd    = int.from_bytes(data[8:12],  "big")
    length = int.from_bytes(data[12:16], "big")
    if 16 + length > len(data):
        return None
    enc_end = 16 + length - 8
    plain = None
    retcode = 0
    for enc_start in (16, 20):
        if enc_end <= enc_start:
            continue
        plain = _tuya_decrypt(data[enc_start:enc_end], key)
        if plain:
            if enc_start == 20:
                retcode = int.from_bytes(data[16:20], "big")
            break
    if not plain:
        return None
    try:
        msg = json.loads(plain)
    except Exception:
        return None
    return {"cmd": cmd, "retcode": retcode, "msg": msg}


def _decode_dps124(value: str) -> dict | None:
    try:
        return json.loads(base64.b64decode(value).decode())
    except Exception:
        return None


def _find_goto(obj) -> dict | None:
    """Recursively search for a goto method in any decoded structure."""
    if isinstance(obj, str):
        d = _decode_dps124(obj)
        if isinstance(d, dict) and d.get("method") == "goto":
            return d
    if isinstance(obj, dict):
        for v in obj.values():
            r = _find_goto(v)
            if r:
                return r
    if isinstance(obj, list):
        for v in obj:
            r = _find_goto(v)
            if r:
                return r
    return None


# ---------------------------------------------------------------------------
# ARP spoofing
# ---------------------------------------------------------------------------

_spoof_running = False
_spoof_thread: threading.Thread | None = None


def _get_mac(ip: str) -> str | None:
    import subprocess
    result = subprocess.run(["arp", "-n", ip], capture_output=True, text=True)
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] == ip and parts[2] not in ("incomplete", "(incomplete)"):
            return parts[2]
    return None


def _start_arp_spoof(phone_ip: str, robot_ip: str, iface: str,
                     phone_mac: str | None, robot_mac: str | None) -> None:
    global _spoof_running, _spoof_thread
    from scapy.all import ARP, Ether, sendp, get_if_hwaddr

    our_mac   = get_if_hwaddr(iface)
    phone_mac = phone_mac or _get_mac(phone_ip)
    robot_mac = robot_mac or _get_mac(robot_ip)

    if not phone_mac or not robot_mac:
        print(f"Cannot resolve MACs: phone={phone_mac} robot={robot_mac}")
        print("Try passing --phone-mac and --robot-mac explicitly.")
        sys.exit(1)

    print(f"  Our MAC:   {our_mac}")
    print(f"  Phone MAC: {phone_mac}  ({phone_ip})")
    print(f"  Robot MAC: {robot_mac}  ({robot_ip})")

    poison_phone = Ether(dst=phone_mac) / ARP(op=2, pdst=phone_ip, hwdst=phone_mac,
                                               psrc=robot_ip, hwsrc=our_mac)
    poison_robot = Ether(dst=robot_mac) / ARP(op=2, pdst=robot_ip, hwdst=robot_mac,
                                               psrc=phone_ip, hwsrc=our_mac)
    _spoof_running = True

    def _loop():
        while _spoof_running:
            sendp([poison_phone, poison_robot], iface=iface, verbose=False)
            time.sleep(1.5)

    _spoof_thread = threading.Thread(target=_loop, daemon=True)
    _spoof_thread.start()


def _stop_arp_spoof(phone_ip: str, robot_ip: str, iface: str) -> None:
    global _spoof_running
    _spoof_running = False
    if _spoof_thread:
        _spoof_thread.join(timeout=3)
    try:
        from scapy.all import ARP, Ether, sendp
        phone_mac = _get_mac(phone_ip)
        robot_mac = _get_mac(robot_ip)
        if phone_mac and robot_mac:
            r1 = Ether(dst=phone_mac) / ARP(op=2, pdst=phone_ip, hwdst=phone_mac,
                                             psrc=robot_ip, hwsrc=robot_mac)
            r2 = Ether(dst=robot_mac) / ARP(op=2, pdst=robot_ip, hwdst=robot_mac,
                                             psrc=phone_ip, hwsrc=phone_mac)
            sendp([r1, r2] * 5, iface=iface, verbose=False)
            print("ARP entries restored.")
    except Exception as e:
        print(f"ARP restore error: {e}")


def _enable_ip_forward() -> bool:
    try:
        with open("/proc/sys/net/ipv4/ip_forward") as f:
            was = f.read().strip()
        with open("/proc/sys/net/ipv4/ip_forward", "w") as f:
            f.write("1\n")
        return was == "1"
    except Exception as e:
        print(f"Warning: could not enable IP forwarding: {e}")
        return False


def _disable_ip_forward() -> None:
    try:
        with open("/proc/sys/net/ipv4/ip_forward", "w") as f:
            f.write("0\n")
    except Exception:
        pass


def _add_iptables_forward(phone_ip: str, robot_ip: str) -> None:
    import subprocess
    for src, dst in [(phone_ip, robot_ip), (robot_ip, phone_ip)]:
        subprocess.run(["iptables", "-I", "FORWARD", "-s", src, "-d", dst, "-j", "ACCEPT"],
                       check=False, capture_output=True)


def _remove_iptables_forward(phone_ip: str, robot_ip: str) -> None:
    import subprocess
    for src, dst in [(phone_ip, robot_ip), (robot_ip, phone_ip)]:
        subprocess.run(["iptables", "-D", "FORWARD", "-s", src, "-d", dst, "-j", "ACCEPT"],
                       check=False, capture_output=True)


# ---------------------------------------------------------------------------
# Phone discovery
# ---------------------------------------------------------------------------

def discover_phone_ip(robot_ip: str, my_ip: str, iface: str, timeout: int = 60) -> str | None:
    from scapy.all import sniff, TCP, IP

    print(f"Waiting up to {timeout}s for phone to connect to {robot_ip}:6668 ...")
    print("(Open the Eufy app on your phone now)")

    found: list[str] = []

    def _pkt(pkt):
        if (IP in pkt and TCP in pkt
                and pkt[IP].dst == robot_ip
                and pkt[TCP].dport == TUYA_PORT
                and pkt[TCP].flags & 0x02
                and pkt[IP].src != my_ip):
            found.append(pkt[IP].src)

    sniff(iface=iface,
          filter=f"tcp and dst host {robot_ip} and dst port {TUYA_PORT}",
          prn=_pkt, stop_filter=lambda _: bool(found),
          timeout=timeout, store=False)
    return found[0] if found else None


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------

def capture_goto(robot_ip: str, phone_ip: str, my_ip: str, key: bytes,
                 iface: str, duration: int) -> tuple[int, int] | None:
    import socket as _socket

    print(f"\nCapturing traffic between {phone_ip} ↔ {robot_ip} for {duration}s ...")
    print("Use the Eufy app → Go to Location → tap a location on the map.\n")

    sock = _socket.socket(_socket.AF_PACKET, _socket.SOCK_RAW, _socket.htons(0x0003))
    sock.bind((iface, 0))
    sock.settimeout(1.0)

    buf: dict[tuple, bytes] = {}
    deadline = time.time() + duration
    found: list[tuple[int, int]] = []

    try:
        while time.time() < deadline and not found:
            try:
                raw_frame = sock.recv(65535)
            except OSError:
                continue

            # Parse raw Ethernet frame
            if len(raw_frame) < 14:
                continue
            eth_type = int.from_bytes(raw_frame[12:14], "big")
            if eth_type != 0x0800:
                continue
            ip = raw_frame[14:]
            if len(ip) < 20 or ip[9] != 6:
                continue
            ihl = (ip[0] & 0xF) * 4

            import socket as _s
            src_ip = _s.inet_ntoa(ip[12:16])
            dst_ip = _s.inet_ntoa(ip[16:20])
            if {src_ip, dst_ip} != {phone_ip, robot_ip}:
                continue

            tcp = ip[ihl:]
            if len(tcp) < 20:
                continue
            src_port = int.from_bytes(tcp[0:2], "big")
            dst_port = int.from_bytes(tcp[2:4], "big")
            if TUYA_PORT not in (src_port, dst_port):
                continue

            tcp_hdr_len = ((tcp[12] >> 4) & 0xF) * 4
            payload = tcp[tcp_hdr_len:]
            if not payload:
                continue

            stream_key = (src_ip, src_port)
            buf[stream_key] = buf.get(stream_key, b"") + payload
            data = buf[stream_key]

            offset = 0
            while offset < len(data):
                idx = data.find(b"\x00\x00U\xaa", offset)
                if idx == -1:
                    buf[stream_key] = b""
                    break
                if len(data) - idx < 20:
                    buf[stream_key] = data[idx:]
                    break
                length  = int.from_bytes(data[idx+12:idx+16], "big")
                pkt_end = idx + 16 + length
                if pkt_end > len(data):
                    buf[stream_key] = data[idx:]
                    break

                tuya = parse_tuya_packet(data[idx:pkt_end], key)
                offset = pkt_end
                if not tuya:
                    continue

                direction = "phone→robot" if src_ip == phone_ip else "robot→phone"
                goto_dec  = _find_goto(tuya["msg"])
                if goto_dec:
                    d = goto_dec.get("data", {})
                    if "x" in d and "y" in d:
                        x, y = d["x"], d["y"]
                        print(f"\n{'='*50}")
                        print(f"  GOTO COORDINATES CAPTURED")
                        print(f"  x={x}  y={y}")
                        print(f"{'='*50}")
                        print(f"\nUse these in your automation:")
                        print(f"  command: goto")
                        print(f"  params:")
                        print(f"    x: {x}")
                        print(f"    y: {y}")
                        found.append((x, y))
    finally:
        sock.close()

    return found[0] if found else None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if os.geteuid() != 0:
        print("This script requires root.  Run with: sudo python intercept_goto.py ...")
        sys.exit(1)

    parser = argparse.ArgumentParser(
        description="Capture Eufy goto coordinates by intercepting the Eufy app",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--robot-ip",  required=True, help="Robot IP address")
    parser.add_argument("--local-key", required=True, help="Tuya local key (from get_local_keys.py)")
    parser.add_argument("--iface",     required=True, help="Network interface (e.g. eth0, wlan0)")
    parser.add_argument("--my-ip",     required=True, help="This machine's IP on --iface")
    parser.add_argument("--phone-ip",  default=None,
                        help="Phone IP (auto-detected if omitted — open Eufy app first)")
    parser.add_argument("--phone-mac", default=None, help="Phone MAC (skips ARP lookup)")
    parser.add_argument("--robot-mac", default=None, help="Robot MAC (skips ARP lookup)")
    parser.add_argument("--duration",  type=int, default=180,
                        help="Seconds to wait for goto command (default: 180)")
    args = parser.parse_args()

    key      = args.local_key.encode()
    robot_ip = args.robot_ip
    iface    = args.iface
    my_ip    = args.my_ip

    print(f"Robot:     {robot_ip}")
    print(f"Interface: {iface}  (my IP: {my_ip})")
    print()

    # Discover phone IP if not supplied
    phone_ip = args.phone_ip
    if not phone_ip:
        phone_ip = discover_phone_ip(robot_ip, my_ip, iface, timeout=60)
        if not phone_ip:
            print("Could not detect phone.  Pass --phone-ip <ip> manually.")
            sys.exit(1)
        print(f"Phone detected: {phone_ip}")

    was_forwarding = _enable_ip_forward()
    _add_iptables_forward(phone_ip, robot_ip)
    print(f"IP forwarding enabled.  Starting ARP intercept...")
    _start_arp_spoof(phone_ip, robot_ip, iface, args.phone_mac, args.robot_mac)
    time.sleep(2)

    def _cleanup(sig=None, frame=None):
        print("\nCleaning up...")
        _stop_arp_spoof(phone_ip, robot_ip, iface)
        _remove_iptables_forward(phone_ip, robot_ip)
        if not was_forwarding:
            _disable_ip_forward()
        sys.exit(0)

    signal.signal(signal.SIGINT,  _cleanup)
    signal.signal(signal.SIGTERM, _cleanup)

    try:
        result = capture_goto(robot_ip, phone_ip, my_ip, key, iface, args.duration)
        if not result:
            print("\nNo goto command captured within the time window.")
            print("Tips:")
            print("  - Make sure the Eufy app is connected and showing the map")
            print("  - Tap 'Go to Location' and then tap a point on the map")
            print("  - Try passing --phone-ip if auto-detection failed")
    finally:
        _cleanup()


if __name__ == "__main__":
    main()
