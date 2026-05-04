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


