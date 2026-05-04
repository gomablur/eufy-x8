"""Eufy cloud auth + Tuya Mobile API session.

Ported to async from https://github.com/8none1/robovac (fix_utf8 branch)
Original work: Andre Borie, Brendan McCluskey — Apache License 2.0
"""
from __future__ import annotations

import json
import logging
import math
import random
import string
import time
import uuid
from hashlib import md5, sha256
from typing import Any
import hmac

import aiohttp
from cryptography.hazmat.backends.openssl import backend as openssl_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from ..const import EUFY_LOGIN_URL, EUFY_USER_AGENT

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (from robovac tuyawebapi.py)
# ---------------------------------------------------------------------------

EUFY_HMAC_KEY = (
    "A_cepev5pfnhua4dkqkdpmnrdxx378mpjr_s8x78u7xwymasd9kqa7a73pjhxqsedaj".encode()
)

TUYA_INITIAL_BASE_URL = "https://a1.tuyaeu.com"

# AES-CBC cipher for Tuya password derivation
_TUYA_PASSWORD_CIPHER = Cipher(
    algorithms.AES(bytearray([
        36, 78, 109, 138, 86, 172, 135, 145,
        36, 67, 45, 139, 108, 188, 162, 196,
    ])),
    modes.CBC(bytearray([
        119, 36, 86, 242, 167, 102, 76, 243,
        57, 44, 53, 151, 233, 62, 87, 71,
    ])),
    backend=openssl_backend,
)

_SIGNATURE_RELEVANT_PARAMETERS = {
    "a", "v", "lat", "lon", "lang", "deviceId", "appVersion", "ttid",
    "isH5", "h5Token", "os", "clientId", "postData", "time", "requestId",
    "et", "n4h5", "sid", "sp",
}

_DEFAULT_QUERY_PARAMS = {
    "appVersion": "2.4.0",
    "platform": "sdk_gphone64_arm64",
    "clientId": "yx5v9uc3ef9wg3v9atje",
    "lang": "en",
    "osSystem": "12",
    "os": "Android",
    "timeZoneId": "Europe/London",
    "ttid": "android",
    "et": "0.0.1",
    "sdkVersion": "3.0.8cAnker",
}

_EUFY_HEADERS = {
    "User-Agent": EUFY_USER_AGENT,
    "timezone": "Europe/London",
    "category": "Home",
    "token": "",
    "uid": "",
    "openudid": "sdk_gphone64_arm64",
    "clientType": "2",
    "language": "en",
    "country": "US",
    "Accept-Encoding": "gzip",
}


# ---------------------------------------------------------------------------
# Crypto helpers
# ---------------------------------------------------------------------------

def _shuffled_md5(value: str) -> str:
    h = md5(value.encode("utf-8")).hexdigest()
    return h[8:16] + h[0:8] + h[24:32] + h[16:24]


def _get_signature(query_params: dict, encoded_post_data: str) -> str:
    params = dict(query_params)
    if encoded_post_data:
        params["postData"] = encoded_post_data
    sorted_pairs = sorted(params.items())
    filtered = [(k, v) for k, v in sorted_pairs if k in _SIGNATURE_RELEVANT_PARAMETERS]
    mapped = [
        f"{k}={_shuffled_md5(v) if k == 'postData' else v}"
        for k, v in filtered
    ]
    message = "||".join(mapped)
    return hmac.new(EUFY_HMAC_KEY, message.encode("utf-8"), sha256).hexdigest()


def _unpadded_rsa(key_exponent: int, key_n: int, plaintext: bytes) -> bytes:
    keylength = math.ceil(key_n.bit_length() / 8)
    input_nr = int.from_bytes(plaintext, byteorder="big")
    crypted_nr = pow(input_nr, key_exponent, key_n)
    return crypted_nr.to_bytes(keylength, byteorder="big")


def _derive_tuya_password(username: str) -> str:
    padded_size = 16 * math.ceil(len(username) / 16)
    password_uid = username.zfill(padded_size)
    encryptor = _TUYA_PASSWORD_CIPHER.encryptor()
    encrypted = encryptor.update(password_uid.encode("utf8"))
    encrypted += encryptor.finalize()
    return md5(encrypted.hex().upper().encode("utf-8")).hexdigest()


def _generate_device_id() -> str:
    base = "8534c8ec0ed0"
    chars = string.ascii_letters + string.digits
    return base + "".join(random.choice(chars) for _ in range(44 - len(base)))


# ---------------------------------------------------------------------------
# EufyAuth
# ---------------------------------------------------------------------------

class EufyAuth:
    """Handles Eufy cloud login and Tuya Mobile API session (async)."""

    def __init__(self, email: str, password: str) -> None:
        self.email = email
        self.password = password
        self._session: aiohttp.ClientSession | None = None

        self._access_token: str = ""
        self._user_id: str = ""
        self._timezone: str = "Europe/London"
        self._region: str = "EU"
        self._phone_code: str = "44"
        self._base_url: str = TUYA_INITIAL_BASE_URL

        self._tuya_sid: str = ""
        self._device_id: str = _generate_device_id()

        self._query_params = {
            **_DEFAULT_QUERY_PARAMS,
            "deviceId": self._device_id,
        }

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    # ------------------------------------------------------------------
    # Step 1: Eufy login
    # ------------------------------------------------------------------
    async def eufy_login(self) -> None:
        session = await self._get_session()
        async with session.post(
            EUFY_LOGIN_URL,
            headers=_EUFY_HEADERS,
            json={
                "client_Secret": "GQCpr9dSp3uQpsOMgJ4xQ",
                "client_id": "eufyhome-app",
                "email": self.email,
                "password": self.password,
            },
        ) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)

        if str(data.get("code", "")) != "1" and not data.get("access_token"):
            raise AuthError(f"Eufy login failed: {data.get('msg', data)}")

        self._access_token = data["access_token"]
        self._user_id = str(data.get("user_id", ""))
        self._timezone = data.get("timezone", "Europe/London")
        self._region = data.get("region", "EU")
        self._phone_code = str(data.get("phone_code", "44"))
        self._query_params["timeZoneId"] = self._timezone

    # ------------------------------------------------------------------
    # Step 2: Tuya session via uid=eh-{user_id}
    # ------------------------------------------------------------------
    async def acquire_tuya_session(self) -> None:
        uid = f"eh-{self._user_id}"
        password = _derive_tuya_password(uid)

        # Get ephemeral RSA token
        token_result = await self._tuya_request(
            "tuya.m.user.uid.token.create",
            data={"uid": uid, "countryCode": self._phone_code},
            requires_session=False,
        )
        encrypted_password = _unpadded_rsa(
            key_exponent=int(token_result["exponent"]),
            key_n=int(token_result["publicKey"]),
            plaintext=password.encode("utf-8"),
        )
        login_data = {
            "uid": uid,
            "createGroup": True,
            "ifencrypt": 1,
            "passwd": encrypted_password.hex(),
            "countryCode": self._phone_code,
            "options": '{"group": 1}',
            "token": token_result["token"],
        }
        try:
            session_result = await self._tuya_request(
                "tuya.m.user.uid.password.login.reg",
                data=login_data,
                requires_session=False,
            )
        except Exception:
            session_result = await self._tuya_request(
                "tuya.m.user.uid.password.login",
                data=login_data,
                requires_session=False,
            )

        self._tuya_sid = session_result["sid"]
        self._query_params["sid"] = self._tuya_sid
        domain = session_result.get("domain", {})
        self._base_url = domain.get("mobileApiUrl", TUYA_INITIAL_BASE_URL)
        if self._phone_code == "44" and session_result.get("phoneCode"):
            self._phone_code = str(session_result["phoneCode"])

    # ------------------------------------------------------------------
    # Full auth flow
    # ------------------------------------------------------------------
    async def authenticate(self) -> None:
        await self.eufy_login()
        await self.acquire_tuya_session()

    # ------------------------------------------------------------------
    # Tuya Mobile API request helper
    # ------------------------------------------------------------------
    async def _tuya_request(
        self, action: str, version: str = "1.0",
        data: dict | None = None, requires_session: bool = True,
    ) -> Any:
        if requires_session and not self._tuya_sid:
            await self.authenticate()

        params = {
            **self._query_params,
            "time": str(int(time.time())),
            "requestId": str(uuid.uuid4()),
            "a": action,
            "v": version,
        }
        encoded_post_data = json.dumps(data, separators=(",", ":")) if data else ""
        params["sign"] = _get_signature(params, encoded_post_data)

        session = await self._get_session()
        async with session.post(
            self._base_url + "/api.json",
            params=params,
            data={"postData": encoded_post_data} if encoded_post_data else None,
        ) as resp:
            resp.raise_for_status()
            response = await resp.json(content_type=None)

        if "result" not in response:
            raise APIError(f"No 'result' in Tuya API response for {action}: {response}")
        return response["result"]

    async def tuya_request(self, action: str, version: str, post_data: dict) -> Any:
        return await self._tuya_request(action, version, post_data)

    # ------------------------------------------------------------------
    # Eufy device list
    # ------------------------------------------------------------------
    async def get_device_list(self, base_url: str | None = None) -> list[dict]:
        """Fetch device list from Eufy cloud API."""
        url = (base_url or "https://api.eufylife.com") + "/v1/device/list/devices-and-groups"
        headers = {
            **_EUFY_HEADERS,
            "token": self._access_token,
            "id": self._user_id,
        }
        session = await self._get_session()
        async with session.get(url, headers=headers) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)
        devices = []
        for item in data.get("items", []):
            for device in item.get("device_list", [item]) if "device_list" in item else [item]:
                devices.append(device)
        return devices

    # ------------------------------------------------------------------
    # Tuya homes → device list (with localKey)
    # ------------------------------------------------------------------
    async def get_tuya_devices(self) -> list[dict]:
        """Walk homes and return all devices with localKey."""
        if not self._tuya_sid:
            await self.authenticate()
        homes = await self._tuya_request("tuya.m.location.list", "2.1")
        devices: dict[str, dict] = {}
        for home in homes or []:
            gid = str(home.get("groupId") or home.get("id", ""))
            params = {**self._query_params, "time": str(int(time.time())),
                      "requestId": str(uuid.uuid4()), "a": "tuya.m.my.group.device.list",
                      "v": "1.0", "gid": gid}
            encoded = json.dumps({}, separators=(",", ":"))
            params["sign"] = _get_signature(params, encoded)
            session = await self._get_session()
            async with session.post(
                self._base_url + "/api.json", params=params,
                data={"postData": encoded}
            ) as resp:
                resp.raise_for_status()
                result = (await resp.json(content_type=None)).get("result", [])
            for dev in result or []:
                dev_id = dev.get("devId") or dev.get("id", "")
                if dev_id and dev_id not in devices:
                    devices[dev_id] = dev
        return list(devices.values())

    @property
    def user_id(self) -> str:
        return self._user_id

    @property
    def access_token(self) -> str:
        return self._access_token


class AuthError(Exception):
    pass


class APIError(Exception):
    pass
