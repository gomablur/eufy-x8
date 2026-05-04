"""Device discovery and local key refresh."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .auth import EufyAuth

_LOGGER = logging.getLogger(__name__)


@dataclass
class DeviceInfo:
    device_id: str
    name: str
    ip: str
    local_key: str
    product_id: str
    online: bool


async def discover_devices(auth: EufyAuth) -> list[DeviceInfo]:
    """Return all devices on the account with local keys."""
    if not auth.user_id:
        await auth.authenticate()

    raw_devices = await auth.get_tuya_devices()
    devices = []
    for dev in raw_devices:
        dev_id = dev.get("devId") or dev.get("id", "")
        if not dev_id:
            continue
        devices.append(DeviceInfo(
            device_id=dev_id,
            name=dev.get("name", dev_id),
            ip=dev.get("ip", ""),
            local_key=dev.get("localKey", ""),
            product_id=dev.get("productId", ""),
            online=dev.get("online", False),
        ))
    return devices


async def get_local_key(auth: EufyAuth, device_id: str) -> str:
    """Refresh and return the current local key for a device."""
    devices = await discover_devices(auth)
    for d in devices:
        if d.device_id == device_id:
            return d.local_key
    return ""


async def get_path_data(auth: EufyAuth, device_id: str) -> list[dict[str, int]]:
    """
    Fetch latest cleaning path coordinates (v3.0 position coordinate system).
    Note: these differ from DPS 124 goto coordinates.
    """
    result = await auth.tuya_request(
        "tuya.m.device.media.latest", "3.0",
        {"devId": device_id, "start": "", "size": 500},
    )
    points = []
    for hex_str in (result or {}).get("dataList", []):
        pt = _decode_position(hex_str)
        if pt:
            points.append(pt)
    return points


def _decode_position(hex_str: str) -> dict[str, int] | None:
    try:
        data = bytes.fromhex(hex_str)
        fields: dict[int, int] = {}
        pos = 1  # skip length prefix byte
        while pos < len(data):
            tag = data[pos]; pos += 1
            field_num = tag >> 3
            wire_type = tag & 7
            if wire_type == 0:
                val = 0; shift = 0
                while pos < len(data):
                    b = data[pos]; pos += 1
                    val |= (b & 0x7F) << shift
                    shift += 7
                    if not (b & 0x80):
                        break
                fields[field_num] = val
            else:
                break
        if 1 in fields and 3 in fields:
            return {"x": fields[1], "y": fields[3]}
    except Exception:
        pass
    return None
