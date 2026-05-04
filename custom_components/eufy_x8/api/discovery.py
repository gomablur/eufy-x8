# Adapted from https://github.com/8none1/robovac (fix_utf8 branch)
# Original work: Brendan McCluskey — Apache License 2.0

import asyncio
import json
import logging
from hashlib import md5

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

_LOGGER = logging.getLogger(__name__)

UDP_KEY = md5(b"yGAdlopoPVldABfn").digest()


class TuyaLocalDiscovery(asyncio.DatagramProtocol):
    def __init__(self, callback):
        self.discovered_callback = callback
        self._known_device_ids: set[str] = set()
        self._known_ips: set[str] = set()
        self._listeners: list = []

    def add_device(self, device_id: str, ip: str | None = None) -> None:
        self._known_device_ids.add(device_id)
        if ip:
            self._known_ips.add(ip)

    def remove_device(self, device_id: str, ip: str | None = None) -> None:
        self._known_device_ids.discard(device_id)
        if ip:
            self._known_ips.discard(ip)

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            self._listeners = await asyncio.gather(
                loop.create_datagram_endpoint(lambda: self, local_addr=("0.0.0.0", 6666), reuse_port=True),
                loop.create_datagram_endpoint(lambda: self, local_addr=("0.0.0.0", 6667), reuse_port=True),
            )
        except Exception as exc:
            raise DiscoveryUnavailable(str(exc)) from exc

    def close(self, *args, **kwargs) -> None:
        for transport, _ in self._listeners:
            transport.close()

    def datagram_received(self, data, addr) -> None:
        if self._known_ips and addr[0] not in self._known_ips:
            return
        data = data[20:-8]
        try:
            cipher = Cipher(algorithms.AES(UDP_KEY), modes.ECB(), default_backend())
            decryptor = cipher.decryptor()
            padded = decryptor.update(data) + decryptor.finalize()
            data = padded[: -ord(padded[-1:])]
        except Exception:
            try:
                data = data.decode()
            except UnicodeDecodeError:
                return
        try:
            decoded = json.loads(data)
            device_id = decoded.get("gwId") or decoded.get("devId")
            if self._known_device_ids and device_id not in self._known_device_ids:
                return
            decoded["ip"] = addr[0]
            asyncio.ensure_future(self.discovered_callback(decoded))
        except (json.JSONDecodeError, KeyError):
            return


async def discover_local_ips(device_ids: list[str], timeout: float = 10.0) -> dict[str, str]:
    """
    Listen for Tuya UDP broadcasts and return {device_id: ip} for found devices.
    Stops early if all device_ids are resolved.
    """
    results: dict[str, str] = {}

    async def on_device(info: dict) -> None:
        dev_id = info.get("gwId") or info.get("devId", "")
        ip = info.get("ip", "")
        if dev_id and ip:
            results[dev_id] = ip

    discovery = TuyaLocalDiscovery(on_device)
    for dev_id in device_ids:
        discovery.add_device(dev_id)

    try:
        await discovery.start()
    except DiscoveryUnavailable as exc:
        _LOGGER.warning("UDP discovery unavailable: %s", exc)
        return results

    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if all(d in results for d in device_ids):
            break
        await asyncio.sleep(0.5)

    discovery.close()
    return results


class DiscoveryUnavailable(Exception):
    pass
