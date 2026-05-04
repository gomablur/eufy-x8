#!/usr/bin/env python3
"""
Retrieve Tuya local keys for all Eufy devices on an account.

The local key is required by tuya_local_control.py and intercept_goto.py.
It rotates whenever the robot reconnects to the Eufy cloud (several times
per day), so re-run this if other tools start returning errors.

Usage:
    python get_local_keys.py --email you@example.com --password yourpassword

    # Or set environment variables:
    export EUFY_EMAIL=you@example.com
    export EUFY_PASSWORD=yourpassword
    python get_local_keys.py

Dependencies:
    pip install requests pycryptodome
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import math
import os
import sys
import time
import uuid

import requests

# ---------------------------------------------------------------------------
# Tuya Mobile API constants (app-level, same for all Eufy users)
# ---------------------------------------------------------------------------
EUFY_LOGIN_URL = "https://home-api.eufylife.com/v1/user/email/login"
EUFY_UA = "EufyHome-Android-3.1.3-753"

TUYA_CLIENT_ID = "yx5v9uc3ef9wg3v9atje"
TUYA_APP_SECRET = "s8x78u7xwymasd9kqa7a73pjhxqsedaj"
TUYA_BMP_SECRET = "cepev5pfnhua4dkqkdpmnrdxx378mpjr"
TUYA_HMAC_KEY = f"A_{TUYA_BMP_SECRET}_{TUYA_APP_SECRET}".encode()
TUYA_BASE_URL = "https://a1.tuyaeu.com/api.json"

TUYA_PASSWORD_KEY = bytes([
    36, 78, 109, 138, 86, 172, 135, 145,
    36, 67, 45, 139, 108, 188, 162, 196,
])
TUYA_PASSWORD_IV = bytes([
    119, 36, 86, 242, 167, 102, 76, 243,
    57, 44, 53, 151, 233, 62, 87, 71,
])

TUYA_SIGN_KEYS = {
    "a", "v", "lat", "lon", "lang", "deviceId", "appVersion", "ttid",
    "isH5", "h5Token", "os", "clientId", "postData", "time", "requestId",
    "et", "n4h5", "sid", "sp",
}


# ---------------------------------------------------------------------------
# Crypto helpers
# ---------------------------------------------------------------------------

def _shuffled_md5(value: str) -> str:
    from hashlib import md5
    h = md5(value.encode()).hexdigest()
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
    from hashlib import md5
    padded = uid.zfill(16 * math.ceil(max(len(uid), 1) / 16))
    cipher = AES.new(TUYA_PASSWORD_KEY, AES.MODE_CBC, TUYA_PASSWORD_IV)
    return md5(cipher.encrypt(padded.encode()).hex().upper().encode()).hexdigest()


def _rsa_encrypt(exponent: str, modulus: str, message: str) -> str:
    n, e = int(modulus), int(exponent)
    c = pow(int(message.encode().hex(), 16), e, n)
    return hex(c)[2:].zfill((n.bit_length() + 7) // 8 * 2)


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------

def get_local_keys(email: str, password: str) -> list[dict]:
    """
    Authenticate and return all devices with their local keys.
    Returns list of dicts: {id, name, localKey, ip, online}.
    """
    # Step 1: Eufy login → user_id
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

    # Step 2: Tuya token + encrypted login
    print("Getting Tuya session...")
    resp = _post("tuya.m.user.uid.token.create", data={"uid": uid, "countryCode": "44"})
    if not resp.get("success"):
        raise SystemExit(f"Token create failed: {resp}")
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
    api_url = domain.get("mobileApiUrl") or TUYA_BASE_URL
    if not api_url.endswith("/api.json"):
        api_url = api_url.rstrip("/") + "/api.json"

    # Step 3: List homes → devices
    print("Fetching device list...")
    homes_resp = _post("tuya.m.location.list", version="2.1", sid=sid, base_url=api_url)
    devices: dict[str, dict] = {}
    for home in homes_resp.get("result") or []:
        gid = str(home.get("groupId") or home.get("id", ""))
        dev_resp = _post("tuya.m.my.group.device.list", version="1.0",
                         sid=sid, gid=gid, base_url=api_url)
        for dev in dev_resp.get("result") or []:
            dev_id = dev.get("devId") or dev.get("id", "")
            if dev_id and dev_id not in devices:
                devices[dev_id] = {
                    "id": dev_id,
                    "name": dev.get("name", dev_id),
                    "localKey": dev.get("localKey", ""),
                    "ip": dev.get("ip", ""),
                    "online": dev.get("online", False),
                }
    return list(devices.values())


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch Tuya local keys for all Eufy devices")
    parser.add_argument("--email", default=os.environ.get("EUFY_EMAIL", ""),
                        help="Eufy account email (or set EUFY_EMAIL env var)")
    parser.add_argument("--password", default=os.environ.get("EUFY_PASSWORD", ""),
                        help="Eufy account password (or set EUFY_PASSWORD env var)")
    args = parser.parse_args()

    if not args.email or not args.password:
        parser.error(
            "Email and password required. Pass --email / --password "
            "or set EUFY_EMAIL / EUFY_PASSWORD environment variables."
        )

    devices = get_local_keys(args.email, args.password)

    if not devices:
        print("No devices found on this account.")
        sys.exit(1)

    print(f"\nFound {len(devices)} device(s):\n")
    print(f"  {'Name':<30}  {'Device ID':<26}  {'Local Key':<20}  {'IP':<16}  Online")
    print(f"  {'-'*30}  {'-'*26}  {'-'*20}  {'-'*16}  ------")
    for d in devices:
        print(f"  {d['name']:<30}  {d['id']:<26}  {d['localKey']:<20}  "
              f"{d['ip']:<16}  {d['online']}")

    print()
    print("Use the Device ID and Local Key with tuya_local_control.py and intercept_goto.py.")
    print("Note: local keys rotate when the robot reconnects to cloud — re-run if needed.")


if __name__ == "__main__":
    main()
