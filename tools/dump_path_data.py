#!/usr/bin/env python3
"""
Fetch and decode cleaning path data from the Eufy/Tuya cloud API.

Calls tuya.m.device.media.latest (v3.0) and dumps:
  - Raw API response
  - All decoded protobuf fields per record (not just x/y)
  - Point count and coordinate range
  - Optionally renders a PNG map (requires Pillow)

Usage:
    python dump_path_data.py --email you@example.com --password yourpass --device-id <id>

    # Save a PNG:
    python dump_path_data.py ... --png /tmp/map.png

Dependencies:
    pip install requests pycryptodome
    pip install Pillow   # optional, for --png
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import math
import os
import struct
import sys
import time
import uuid

import requests

# ---------------------------------------------------------------------------
# Tuya Mobile API auth (same as get_local_keys.py)
# ---------------------------------------------------------------------------
EUFY_LOGIN_URL = "https://home-api.eufylife.com/v1/user/email/login"
EUFY_UA        = "EufyHome-Android-3.1.3-753"
TUYA_CLIENT_ID = "yx5v9uc3ef9wg3v9atje"
TUYA_APP_SECRET = "s8x78u7xwymasd9kqa7a73pjhxqsedaj"
TUYA_BMP_SECRET = "cepev5pfnhua4dkqkdpmnrdxx378mpjr"
TUYA_HMAC_KEY   = f"A_{TUYA_BMP_SECRET}_{TUYA_APP_SECRET}".encode()
TUYA_BASE_URL   = "https://a1.tuyaeu.com/api.json"

TUYA_PASSWORD_KEY = bytes([36,78,109,138,86,172,135,145,36,67,45,139,108,188,162,196])
TUYA_PASSWORD_IV  = bytes([119,36,86,242,167,102,76,243,57,44,53,151,233,62,87,71])

TUYA_SIGN_KEYS = {
    "a","v","lat","lon","lang","deviceId","appVersion","ttid",
    "isH5","h5Token","os","clientId","postData","time","requestId",
    "et","n4h5","sid","sp",
}


def _shuffled_md5(value: str) -> str:
    h = hashlib.md5(value.encode()).hexdigest()
    return h[8:16] + h[0:8] + h[24:32] + h[16:24]


def _sign(params: dict) -> str:
    parts = []
    for k in sorted(params.keys()):
        if k not in TUYA_SIGN_KEYS:
            continue
        v = params[k]
        if v is None or v == "":
            continue
        parts.append(f"postData={_shuffled_md5(str(v))}" if k == "postData" else f"{k}={v}")
    return hmac.new(TUYA_HMAC_KEY, "||".join(parts).encode(), hashlib.sha256).hexdigest()


def _post(action: str, data: dict | None = None, version: str = "1.0",
          sid: str | None = None, gid: str | None = None,
          base_url: str = TUYA_BASE_URL) -> dict:
    p: dict = {
        "appVersion": "2.4.0",
        "deviceId": "abcdef1234567890abcdef1234567890abcdef12345",
        "platform": "sdk_gphone64_arm64",
        "clientId": TUYA_CLIENT_ID,
        "lang": "en", "osSystem": "12", "os": "Android",
        "timeZoneId": "Europe/London", "ttid": "android",
        "et": "0.0.1", "sdkVersion": "3.0.8cAnker",
        "time": str(int(time.time())),
        "requestId": str(uuid.uuid4()).replace("-", ""),
        "a": action, "v": version,
    }
    if sid:
        p["sid"] = sid
    if gid:
        p["gid"] = gid
    if data:
        p["postData"] = json.dumps(data, separators=(",", ":"))
    p["sign"] = _sign(p)
    r = requests.post(base_url, data=p, timeout=15)
    r.raise_for_status()
    return r.json()


def _derive_password(uid: str) -> str:
    from Crypto.Cipher import AES
    padded = uid.zfill(16 * math.ceil(max(len(uid), 1) / 16))
    cipher = AES.new(TUYA_PASSWORD_KEY, AES.MODE_CBC, TUYA_PASSWORD_IV)
    return hashlib.md5(cipher.encrypt(padded.encode()).hex().upper().encode()).hexdigest()


def _rsa_encrypt(exponent: str, modulus: str, message: str) -> str:
    n, e = int(modulus), int(exponent)
    c = pow(int(message.encode().hex(), 16), e, n)
    return hex(c)[2:].zfill((n.bit_length() + 7) // 8 * 2)


def authenticate(email: str, password: str) -> tuple[str, str, dict]:
    """Returns (sid, api_url, domain_info)."""
    print("Logging in to Eufy...")
    r = requests.post(
        EUFY_LOGIN_URL,
        headers={
            "User-Agent": EUFY_UA, "category": "Home", "Accept": "*/*",
            "openudid": "abcdef1234567890",
            "Content-Type": "application/json", "clientType": "1",
        },
        json={
            "email": email, "password": password,
            "client_id": "eufyhome-app",
            "client_secret": "GQCpr9dSp3uQpsOMgJ4xQ",
        },
        timeout=15,
    )
    data = r.json()
    user_id = str(data.get("user_id", ""))
    if not user_id:
        raise SystemExit(f"Eufy login failed: {data.get('msg', data)}")

    uid = f"eh-{user_id}"
    print("Getting Tuya session...")
    resp = _post("tuya.m.user.uid.token.create", data={"uid": uid, "countryCode": "44"})
    result = resp["result"]
    encrypted = _rsa_encrypt(
        result.get("exponent", "65537"), result["publicKey"],
        _derive_password(uid)
    )
    login_data = {
        "uid": uid, "passwd": encrypted, "countryCode": "44",
        "createGroup": True, "ifencrypt": 1,
        "options": {"group": 1}, "token": result["token"],
    }
    resp2 = _post("tuya.m.user.uid.password.login.reg", data=login_data)
    if not resp2.get("success"):
        resp2 = _post("tuya.m.user.uid.password.login", data=login_data)
    if not resp2.get("success"):
        raise SystemExit(f"Tuya login failed: {resp2}")

    sid = resp2["result"]["sid"]
    domain = resp2["result"].get("domain", {})
    print(f"\nDomain info from login: {json.dumps(domain, indent=2)}")

    api_url = domain.get("mobileApiUrl") or TUYA_BASE_URL
    if not api_url.endswith("/api.json"):
        api_url = api_url.rstrip("/") + "/api.json"
    access_token = data.get("access_token", "")
    return sid, api_url, domain, access_token


# ---------------------------------------------------------------------------
# Protobuf decoder — returns ALL fields, all wire types
# ---------------------------------------------------------------------------

def _decode_varint(data: bytes, pos: int) -> tuple[int, int]:
    val = 0
    shift = 0
    while pos < len(data):
        b = data[pos]; pos += 1
        val |= (b & 0x7F) << shift
        shift += 7
        if not (b & 0x80):
            break
    return val, pos


def decode_all_fields(hex_str: str) -> dict[int, list]:
    """Decode all protobuf fields from a hex record. Returns {field_num: [values]}."""
    try:
        data = bytes.fromhex(hex_str)
    except ValueError:
        return {}

    fields: dict[int, list] = {}
    pos = 1  # skip length prefix byte

    while pos < len(data):
        if pos >= len(data):
            break
        tag = data[pos]; pos += 1
        field_num = tag >> 3
        wire_type = tag & 7

        if field_num == 0:
            break

        if wire_type == 0:  # varint
            val, pos = _decode_varint(data, pos)
            fields.setdefault(field_num, []).append(("varint", val))

        elif wire_type == 1:  # 64-bit
            if pos + 8 > len(data):
                break
            val = struct.unpack_from("<Q", data, pos)[0]
            pos += 8
            fields.setdefault(field_num, []).append(("64bit", val))

        elif wire_type == 2:  # length-delimited
            length, pos = _decode_varint(data, pos)
            if pos + length > len(data):
                break
            val = data[pos:pos + length]
            pos += length
            fields.setdefault(field_num, []).append(("bytes", val.hex()))

        elif wire_type == 5:  # 32-bit
            if pos + 4 > len(data):
                break
            val = struct.unpack_from("<I", data, pos)[0]
            pos += 4
            fields.setdefault(field_num, []).append(("32bit", val))

        else:
            # Unknown wire type — can't continue safely
            break

    return fields


def zigzag_decode(n: int) -> int:
    """Protobuf sint32/sint64 zigzag decoding."""
    return (n >> 1) ^ -(n & 1)


# ---------------------------------------------------------------------------
# Path data fetch
# ---------------------------------------------------------------------------

MEDIA_VERSIONS = ["3.0", "2.0", "1.0", "2.1"]
MEDIA_ACTIONS  = [
    "tuya.m.device.media.latest",
    "tuya.m.device.media.history",
    "tuya.m.device.media.detail",
]

# Broader probe list for --probe-all
PROBE_ALL_ACTIONS = [
    # Confirmed working
    "tuya.m.device.media.latest",
    # Media variants
    "tuya.m.device.media.history",
    "tuya.m.device.media.list",
    "tuya.m.device.media.record.list",
    "tuya.m.device.media.record.latest",
    "tuya.m.device.media.detail",
    "tuya.m.device.media.getLatestMessage",
    "tuya.m.device.media.map",
    "tuya.m.device.media.path",
    # Robot-specific
    "tuya.m.robot.history.list",
    "tuya.m.robot.history.latest",
    "tuya.m.robot.map.get",
    "tuya.m.robot.map.latest",
    "tuya.m.robot.path.get",
    "tuya.m.robot.media.latest",
    # Device info
    "tuya.m.device.dp.get",
    "tuya.m.device.status.get",
    "tuya.m.device.info.get",
]

PROBE_ALL_VERSIONS = ["3.0", "2.0", "1.0", "2.1", "3.1", "4.0"]

# Payloads to try in --probe-all (in addition to the standard one)
def _probe_payloads(device_id: str) -> list[dict]:
    return [
        {"devId": device_id, "start": "", "size": 10},
        {"devId": device_id, "size": 10},
        {"devId": device_id},
        {"devId": device_id, "start": "", "size": 10, "type": "path"},
        {"devId": device_id, "start": "", "size": 10, "type": "map"},
    ]


def fetch_path_data(sid: str, api_url: str, device_id: str, size: int = 500,
                    action: str = "tuya.m.device.media.latest",
                    version: str = "3.0", start: str = "") -> dict:
    """Fetch raw media response."""
    resp = _post(
        action,
        {"devId": device_id, "start": start, "size": size},
        version,
        sid=sid, base_url=api_url,
    )
    return resp


def fetch_all_pages(sid: str, api_url: str, device_id: str, size: int = 500,
                    action: str = "tuya.m.device.media.latest",
                    version: str = "3.0") -> list[str]:
    """Fetch all pages of path data, following hasNext automatically."""
    all_records: list[str] = []
    start = ""
    page = 1
    while True:
        resp = fetch_path_data(sid, api_url, device_id, size=size,
                               action=action, version=version, start=start)
        if not resp.get("success"):
            print(f"  Page {page} failed: {resp.get('errorCode', resp)}")
            break
        result    = resp.get("result", {})
        records   = result.get("dataList", [])
        has_more  = result.get("hasNext", False)
        all_records.extend(records)
        print(f"  Page {page}: {len(records)} records  (hasNext={has_more})")
        if not has_more or not records:
            break
        start = records[-1]
        page += 1
    return all_records


def probe_media_api(sid: str, api_url: str, device_id: str) -> tuple[str, str, dict] | None:
    """Try standard action/version combos and return the first that succeeds."""
    for action in MEDIA_ACTIONS:
        for version in MEDIA_VERSIONS:
            resp = fetch_path_data(sid, api_url, device_id, size=10,
                                   action=action, version=version)
            if resp.get("success"):
                print(f"  SUCCESS: {action} v{version}")
                return action, version, resp
            else:
                print(f"  {resp.get('errorCode', 'error')}: {action} v{version}")
    return None


def probe_all_apis(sid: str, api_url: str, device_id: str) -> None:
    """Try every known action/version/payload combo and report what succeeds."""
    print(f"\n--- Broad API probe ({len(PROBE_ALL_ACTIONS)} actions × "
          f"{len(PROBE_ALL_VERSIONS)} versions) ---")
    successes = []
    payloads = _probe_payloads(device_id)
    for action in PROBE_ALL_ACTIONS:
        for version in PROBE_ALL_VERSIONS:
            # Try standard payload first; only try others if standard fails
            for payload in payloads:
                p = {**payload}
                p_str = json.dumps(p, separators=(",", ":"))
                resp = _post(action, p, version, sid=sid, base_url=api_url)
                if resp.get("success"):
                    result = resp.get("result", {})
                    record_count = len(result.get("dataList", [])) if isinstance(result, dict) else "?"
                    print(f"  ✓ {action} v{version}  payload={p_str}  records={record_count}")
                    successes.append((action, version, p_str, record_count))
                    break  # don't try other payloads if this one worked
                # Only print failures for the first payload to avoid noise
                elif payload == payloads[0]:
                    print(f"  ✗ {action} v{version}  {resp.get('errorCode', 'error')}")

    print(f"\n--- Probe summary: {len(successes)} working endpoint(s) ---")
    for s in successes:
        print(f"  {s[0]} v{s[1]}  {s[2]}  ({s[3]} records)")


def probe_media_detail(sid: str, api_url: str, device_id: str) -> None:
    """Probe tuya.m.device.media.detail with many payload variants to find required params."""
    print("\n--- Probing tuya.m.device.media.detail (finding required params) ---")

    now = int(time.time())
    payloads = [
        # Time range variants
        {"devId": device_id, "startTime": now - 86400, "endTime": now},
        {"devId": device_id, "startTime": now - 7 * 86400, "endTime": now},
        {"devId": device_id, "startTime": (now - 86400) * 1000, "endTime": now * 1000},
        # Type variants
        {"devId": device_id, "type": "path"},
        {"devId": device_id, "type": "map"},
        {"devId": device_id, "type": 0},
        {"devId": device_id, "type": 1},
        {"devId": device_id, "dataType": 0},
        {"devId": device_id, "dataType": 1},
        # ID variants
        {"devId": device_id, "mediaId": "0"},
        {"devId": device_id, "mediaId": 0},
        {"devId": device_id, "msgId": "0"},
        {"devId": device_id, "recordId": "0"},
        # Combo variants
        {"devId": device_id, "start": "0", "size": 10},
        {"devId": device_id, "start": "0", "size": 10, "type": "path"},
        {"devId": device_id, "uid": "", "start": "", "size": 10},
        {"devId": device_id, "startTime": now - 86400, "endTime": now, "type": "path"},
        {"devId": device_id, "startTime": now - 86400, "endTime": now, "size": 10},
    ]
    for version in ["3.0", "2.0", "1.0"]:
        for payload in payloads:
            resp = _post("tuya.m.device.media.detail", payload, version,
                         sid=sid, base_url=api_url)
            code = resp.get("errorCode", "")
            if resp.get("success"):
                print(f"  ✓ v{version}  {json.dumps(payload, separators=(',',':'))}  → {resp.get('result')}")
            elif code != "REMOTE_API_PARAM_ALL_INPUT_LOSS":
                # Different error = something changed — worth noting
                print(f"  ? v{version}  {json.dumps(payload, separators=(',',':'))}  → {code}: {resp.get('errorMsg','')}")
    print("  (rows with REMOTE_API_PARAM_ALL_INPUT_LOSS suppressed — all params still missing)")


def probe_urls(device_id: str, map_id: str | None = None,
               sid: str = "", access_token: str = "") -> None:
    """Probe images.tuyaeu.com with and without auth headers."""
    import urllib.request
    import urllib.error

    base_urls = [
        f"https://images.tuyaeu.com/{device_id}/map.png",
        f"https://images.tuyaeu.com/{device_id}/latest.png",
        f"https://images.tuyaeu.com/device/{device_id}/map.png",
        f"https://images.tuyaeu.com/map/{device_id}.png",
        f"https://images.tuyaeu.com/{device_id}/path.png",
    ]
    if map_id:
        base_urls += [
            f"https://images.tuyaeu.com/{device_id}/{map_id}.png",
            f"https://images.tuyaeu.com/{device_id}/{map_id}/map.png",
            f"https://images.tuyaeu.com/map/{device_id}/{map_id}.png",
        ]

    # Auth header sets to try
    auth_variants: list[tuple[str, dict]] = [("(no auth)", {})]
    if sid:
        auth_variants += [
            ("sid header",      {"sid": sid}),
            ("sid cookie",      {"Cookie": f"sid={sid}"}),
            ("Bearer sid",      {"Authorization": f"Bearer {sid}"}),
        ]
    if access_token:
        auth_variants += [
            ("Bearer token",    {"Authorization": f"Bearer {access_token}"}),
            ("token header",    {"token": access_token}),
            ("access_token hdr",{"access_token": access_token}),
        ]

    def _try(url, label, extra_headers):
        headers = {"User-Agent": EUFY_UA, **extra_headers}
        # Also try with sid as query param
        urls_to_try = [url]
        if sid:
            urls_to_try.append(url + f"?sid={sid}")
        for u in urls_to_try:
            try:
                req = urllib.request.Request(u, headers=headers)
                with urllib.request.urlopen(req, timeout=5) as r:
                    ct  = r.headers.get("Content-Type", "")
                    clen = r.headers.get("Content-Length", "?")
                    print(f"  ✓ [{r.status}] {label}  {u}  {ct} {clen}b")
                    return True
            except urllib.error.HTTPError as e:
                if e.code != 403:
                    print(f"  ? [HTTP {e.code}] {label}  {u}")
            except Exception as ex:
                print(f"  ✗ {type(ex).__name__}  {u}")
        return False

    print("\n--- Probing images.tuyaeu.com ---")
    any_success = False
    for url in base_urls:
        for label, headers in auth_variants:
            if _try(url, label, headers):
                any_success = True
    if not any_success:
        print("  All 403 — server exists but all URL/auth combinations rejected")


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_png(points: list[dict], path: str) -> None:
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("Pillow not installed — skipping PNG render. pip install Pillow")
        return

    if not points:
        print("No points to render.")
        return

    xs = [p["x"] for p in points]
    ys = [p["y"] for p in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span_x = max_x - min_x or 1
    span_y = max_y - min_y or 1

    SIZE   = 800
    MARGIN = 30

    def to_px(x, y):
        px = int(MARGIN + (x - min_x) / span_x * (SIZE - 2 * MARGIN))
        py = int(MARGIN + (y - min_y) / span_y * (SIZE - 2 * MARGIN))
        return px, py

    img  = Image.new("RGB", (SIZE, SIZE), (20, 20, 20))
    draw = ImageDraw.Draw(img)

    for pt in points:
        cx, cy = to_px(pt["x"], pt["y"])
        draw.ellipse([cx-3, cy-3, cx+3, cy+3], fill=(100, 200, 255))

    # Mark first and last points
    if points:
        dx, dy = to_px(points[0]["x"], points[0]["y"])
        draw.ellipse([dx-6, dy-6, dx+6, dy+6], fill=(255, 80, 80), outline=(255,255,255), width=1)
        ex, ey = to_px(points[-1]["x"], points[-1]["y"])
        draw.ellipse([ex-6, ey-6, ex+6, ey+6], fill=(80, 255, 80), outline=(255,255,255), width=1)

    img.save(path, format="PNG")
    print(f"PNG saved to {path}  ({SIZE}x{SIZE}px, {len(points)} points)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _decode_records(data_list: list[str], print_fields: bool = False
                    ) -> tuple[list[dict], list[dict], dict[int, set], dict[int, list]]:
    """Decode records. Returns (points_xy, points_zz, field_summary, all_field_values)."""
    points_xy: list[dict] = []
    points_zz: list[dict] = []
    field_summary: dict[int, set] = {}
    all_field_values: dict[int, list] = {}  # field_num -> [raw_varint, ...]

    for hex_str in data_list:
        fields = decode_all_fields(hex_str)
        for fn, vals in fields.items():
            field_summary.setdefault(fn, set())
            all_field_values.setdefault(fn, [])
            for wt, v in vals:
                field_summary[fn].add(wt)
                if wt == "varint":
                    all_field_values[fn].append(v)

        if print_fields:
            print(f"  {hex_str[:40]}{'...' if len(hex_str) > 40 else ''}")
            for fn in sorted(fields):
                for wt, v in fields[fn]:
                    zz = zigzag_decode(v) if wt == "varint" else ""
                    zz_str = f"  (zigzag={zz})" if zz != "" else ""
                    print(f"    field {fn} [{wt}] = {v}{zz_str}")
            print()

        f1 = fields.get(1, [])
        f3 = fields.get(3, [])
        if f1 and f3:
            raw_x, raw_y = f1[0][1], f3[0][1]
            points_xy.append({"x": raw_x, "y": raw_y})
            points_zz.append({"x": zigzag_decode(raw_x), "y": zigzag_decode(raw_y)})

    return points_xy, points_zz, field_summary, all_field_values


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dump Eufy cleaning path data from cloud API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--email",       default=os.environ.get("EUFY_EMAIL", ""))
    parser.add_argument("--password",    default=os.environ.get("EUFY_PASSWORD", ""))
    parser.add_argument("--device-id",   required=True, help="Tuya device ID")
    parser.add_argument("--size",        type=int, default=500,
                        help="Records per page (default 500)")
    parser.add_argument("--raw",         action="store_true",
                        help="Print raw hex records")
    parser.add_argument("--raw-response", action="store_true",
                        help="Print full API response JSON (to find extra result fields)")
    parser.add_argument("--fields",      action="store_true",
                        help="Print all decoded protobuf fields per record")
    parser.add_argument("--png",         metavar="PATH",
                        help="Render path as PNG and save to PATH")
    parser.add_argument("--probe-all",    action="store_true",
                        help="Probe all known API actions/versions/payloads")
    parser.add_argument("--probe-urls",  action="store_true",
                        help="Probe images.tuyaeu.com with auth header variants")
    parser.add_argument("--probe-detail", action="store_true",
                        help="Probe tuya.m.device.media.detail with many payload variants")
    parser.add_argument("--map-id",      metavar="ID",
                        help="Map ID from DPS 125 (used with --probe-urls)")
    parser.add_argument("--no-domain",   action="store_true",
                        help="Suppress domain info output")
    parser.add_argument("--poll",        type=int, metavar="SECONDS",
                        help="Repeatedly fetch every N seconds; accumulate all points (Ctrl-C to stop and save PNG)")
    args = parser.parse_args()

    if not args.email or not args.password:
        parser.error("--email and --password required (or set EUFY_EMAIL / EUFY_PASSWORD)")

    sid, api_url, domain, access_token = authenticate(args.email, args.password)
    if args.no_domain:
        pass  # already printed in authenticate(); future: suppress there too

    # --- Broad API probe ---
    if args.probe_all:
        probe_all_apis(sid, api_url, args.device_id)

    # --- media.detail parameter probe ---
    if args.probe_detail:
        probe_media_detail(sid, api_url, args.device_id)

    # --- URL probes ---
    if args.probe_urls:
        probe_urls(args.device_id, map_id=args.map_id, sid=sid, access_token=access_token)

    # --- Poll mode ---
    if args.poll:
        import time as _time
        print(f"\nFinding working media endpoint...")
        found = probe_media_api(sid, api_url, args.device_id)
        if not found:
            print("No working API/version found.")
            sys.exit(1)
        action, version, _ = found

        all_points: list[dict] = []
        seen: set[tuple] = set()
        print(f"\nPolling every {args.poll}s — Ctrl-C to stop\n")
        print(f"  {'Poll':<6} {'Records':<9} {'New':<6} {'Field 1 (x)':<14} {'Field 3 (y)':<14} {'Field 4':<10} {'Field 5':<10} {'Field 6':<10} {'Field 8'}")
        print(f"  {'-'*90}")
        poll_num = 0
        try:
            while True:
                poll_num += 1
                data_list = fetch_all_pages(sid, api_url, args.device_id,
                                            size=args.size, action=action, version=version)
                new_this_poll = 0
                last_fields: dict = {}
                for hex_str in data_list:
                    fields = decode_all_fields(hex_str)
                    last_fields = fields
                    key = tuple(sorted((fn, vals[0][1]) for fn, vals in fields.items()))
                    if key not in seen:
                        seen.add(key)
                        new_this_poll += 1
                        f1 = fields.get(1, [[None, 0]])[0][1]
                        f3 = fields.get(3, [[None, 0]])[0][1]
                        all_points.append({"x": f1, "y": f3})

                f = lambda n: last_fields.get(n, [[None, "?"]])[0][1] if last_fields else "?"
                print(f"  {poll_num:<6} {len(data_list):<9} {new_this_poll:<6} "
                      f"{f(1)!s:<14} {f(3)!s:<14} {f(4)!s:<10} {f(5)!s:<10} {f(6)!s:<10} {f(8)}")
                _time.sleep(args.poll)
        except KeyboardInterrupt:
            print(f"\n\nStopped. {len(all_points)} unique points accumulated.")
            if args.png and all_points:
                render_png(all_points, args.png)
        return

    # --- Main path data fetch ---
    print(f"\nFinding working media endpoint...")
    found = probe_media_api(sid, api_url, args.device_id)
    if not found:
        print("\nNo working API/version combination found.")
        sys.exit(1)

    action, version, first_resp = found

    if args.raw_response:
        print("\n--- Full API response (first page) ---")
        print(json.dumps(first_resp, indent=2))
        print()

    print(f"\nFetching all pages (page size={args.size})...")
    data_list = fetch_all_pages(sid, api_url, args.device_id,
                                size=args.size, action=action, version=version)

    if args.raw:
        print("\n--- Raw records (hex) ---")
        for r in data_list:
            print(f"  {r}")
        print()

    print(f"\nTotal records fetched: {len(data_list)}")
    if data_list:
        print(f"First: {data_list[0]}")
        print(f"Last : {data_list[-1]}")

    points_xy, points_zz, field_summary, all_field_values = _decode_records(
        data_list, print_fields=args.fields
    )

    print(f"\n--- All fields — range across {len(data_list)} records ---")
    print(f"  {'Field':<8} {'Raw min':<10} {'Raw max':<10} {'Span':<10} {'ZZ min':<10} {'ZZ max':<10} {'ZZ span'}")
    print(f"  {'-'*75}")
    for fn in sorted(all_field_values):
        vals = all_field_values[fn]
        if not vals:
            continue
        lo, hi = min(vals), max(vals)
        span = hi - lo
        zz_vals = [zigzag_decode(v) for v in vals]
        zlo, zhi = min(zz_vals), max(zz_vals)
        zspan = zhi - zlo
        print(f"  {fn:<8} {lo:<10} {hi:<10} {span:<10} {zlo:<10} {zhi:<10} {zspan}")

    if points_xy:
        print(f"\n--- Position trace (fields 1=x, 3=y, raw varint) ---")
        print(f"  Points  : {len(points_xy)}")
        xs = [p["x"] for p in points_xy]
        ys = [p["y"] for p in points_xy]
        print(f"  X range : {min(xs)} – {max(xs)}  (span {max(xs)-min(xs)})")
        print(f"  Y range : {min(ys)} – {max(ys)}  (span {max(ys)-min(ys)})")
        zxs = [p["x"] for p in points_zz]
        zys = [p["y"] for p in points_zz]
        print(f"  ZZ X    : {min(zxs)} – {max(zxs)}  (span {max(zxs)-min(zxs)})")
        print(f"  ZZ Y    : {min(zys)} – {max(zys)}  (span {max(zys)-min(zys)})")

    print(f"\n--- Sample values per field (first 5 records) ---")
    for hex_str in data_list[:5]:
        fields = decode_all_fields(hex_str)
        row = {fn: f"{vals[0][1]} (zz={zigzag_decode(vals[0][1])})"
               for fn, vals in sorted(fields.items()) if vals[0][0] == "varint"}
        print(f"  {row}")

    if args.png and points_xy:
        render_png(points_xy, args.png)


if __name__ == "__main__":
    main()
