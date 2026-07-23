# -*- coding: utf-8 -*-
# Transport layer for the Eufy X8 integration.
# Part of eufy-x8 (MIT License, Copyright (c) Will Cooke); see the repo LICENSE.
#
# History: earlier versions of this file hand-rolled the Tuya v3.1/v3.3 local
# protocol. Newer Eufy models such as the X8 Pro Hybrid SES (T2276) speak Tuya
# v3.5 (0x6699 framing, AES-GCM with a session-key negotiation handshake), which
# the hand-rolled code could not do — the socket connected but every request
# timed out.
#
# This module now delegates the wire protocol to tinytuya (MIT-licensed), which
# implements v3.1 / v3.3 / v3.4 / v3.5 correctly, while preserving the exact
# public surface the rest of the integration depends on:
#   TuyaDevice(device_id, host, timeout, ping_interval, update_entity_state,
#              local_key=..., port=6668, version=...)
#     .host                      attribute (read by the coordinator)
#     .state                     property -> dict of DPS
#     .async_get()               refresh DPS from the device
#     .async_set(dps: dict)      write DPS values
#     .async_goto(x, y)          -> bool   (DPS 124 command transport)
#     .async_clear()             -> bool
#     .async_disable()           stop and close the socket
#     .update_local_key(key)     swap key after a cloud refresh (raises InvalidKey)
#     ._backoff / .reset_backoff()   consulted by the UDP discovery callback
#   plus the exception types InvalidKey and TuyaException.

import asyncio
import base64
import json
import logging
import threading
import time

import tinytuya

_LOGGER = logging.getLogger(__name__)

# Protocol versions to try, in order, when auto-detecting. A device that has
# been seen at one version is remembered and tried first on reconnect.
_VERSION_CANDIDATES = (3.5, 3.4, 3.3, 3.1)

# tinytuya error code returned when the local key or protocol version is wrong.
_ERR_KEY_OR_VERSION = "914"


class TuyaException(Exception):
    pass


class InvalidKey(TuyaException):
    pass


class ConnectionException(TuyaException):
    pass


class TuyaDevice:
    """tinytuya-backed Tuya local device with the legacy public interface."""

    def __init__(self, device_id, host, timeout, ping_interval, update_entity_state,
                 local_key=None, port=6668, gateway_id=None, version=None):
        self._LOGGER = _LOGGER.getChild(device_id)
        self.device_id = device_id
        self.host = host
        self.port = port
        self.timeout = timeout
        self.ping_interval = ping_interval
        self.update_entity_state_cb = update_entity_state

        if not local_key or len(local_key) != 16:
            raise InvalidKey("Local key should be a 16-character string")
        self._local_key = local_key

        # Version hint: accept the legacy tuple form (e.g. (3, 3)) or a float.
        if isinstance(version, (tuple, list)):
            version = float(".".join(str(int(p)) for p in version[:2]))
        self._version = version  # float or None (auto-detect)

        self._td: tinytuya.Device | None = None
        self._lock = threading.Lock()   # serialises access to the tinytuya socket
        self._dps: dict = {}
        self._enabled = True
        self._backoff = False
        self._failures = 0

    # ------------------------------------------------------------------
    # Small helpers
    # ------------------------------------------------------------------

    def reset_backoff(self) -> None:
        self._failures = 0
        self._backoff = False

    def update_local_key(self, new_key: str) -> None:
        """Replace the local key (called after a cloud key refresh)."""
        if not new_key or len(new_key) != 16:
            raise InvalidKey("Local key should be a 16-character string")
        with self._lock:
            self._local_key = new_key
            self._close_locked()  # force a rebuild with the new key

    @property
    def state(self) -> dict:
        return dict(self._dps)

    async def _run(self, fn, *args):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, fn, *args)

    # ------------------------------------------------------------------
    # tinytuya socket lifecycle (all under self._lock, in executor threads)
    # ------------------------------------------------------------------

    def _close_locked(self) -> None:
        if self._td is not None:
            try:
                self._td.close()
            except Exception:
                pass
            self._td = None

    def _build_locked(self, version: float) -> tinytuya.Device:
        d = tinytuya.Device(self.device_id, self.host, self._local_key, version=version)
        d.set_socketTimeout(self.timeout)
        # Non-persistent: open a fresh socket per operation. We only poll every
        # ~30s, so the cost of re-doing the v3.5 session-key handshake each time
        # is negligible — and it eliminates the failure mode where a long-lived
        # v3.5 session desyncs and the socket then returns 904 ("Unexpected
        # Payload") indefinitely.
        d.set_socketPersistent(False)
        d.set_socketRetryLimit(1)
        return d

    def _ensure_locked(self) -> tinytuya.Device:
        """Return a connected tinytuya device, auto-detecting the version once."""
        if self._td is not None:
            return self._td
        if not self.host:
            raise ConnectionException("No IP address configured")

        order = []
        if self._version:
            order.append(self._version)
        order += [v for v in _VERSION_CANDIDATES if v != self._version]

        last = None
        saw_key_err = False
        for v in order:
            d = self._build_locked(v)
            r = d.status()
            if isinstance(r, dict) and "dps" in r:
                if v != self._version:
                    self._LOGGER.info("Detected Tuya protocol v%s", v)
                self._version = v
                self._td = d
                self._dps.update(r["dps"])
                return d
            last = r
            # Track whether ANY candidate reported a key/version error — not just
            # the last one. A transient error on a later candidate must not mask
            # a genuine key rejection seen on the real protocol version earlier.
            if isinstance(r, dict) and str(r.get("Err")) == _ERR_KEY_OR_VERSION:
                saw_key_err = True
            try:
                d.close()
            except Exception:
                pass

        # Nothing worked. If any attempt complained about key/version, the key is
        # the likely culprit — signal the coordinator to refresh it. (tinytuya
        # also returns 914 when the v3.4/v3.5 session-key handshake fails, so this
        # can occasionally be a transient network fault rather than a bad key; the
        # coordinator's refresh path is cheap-ish and self-correcting, and its
        # cooldown prevents hammering the Eufy cloud.)
        detail = last.get("Error") if isinstance(last, dict) else last
        if saw_key_err:
            raise InvalidKey(f"Key/version rejected by device (last error: {detail})")
        raise ConnectionException(f"Could not reach device: {detail}")

    def _raise_for_error(self, r) -> None:
        """Map a tinytuya error dict to an exception."""
        err = str(r.get("Err")) if isinstance(r, dict) else None
        if err == _ERR_KEY_OR_VERSION:
            # Key may have rotated — drop the connection so we rebuild after refresh.
            with self._lock:
                self._close_locked()
            raise InvalidKey(f"Key rejected: {r.get('Error')}")
        raise TuyaException(f"Device error: {r}")

    # ------------------------------------------------------------------
    # Sync workers (run in executor)
    # ------------------------------------------------------------------

    def _note_failure(self) -> None:
        self._failures += 1
        if self._failures > 3:
            self._backoff = True

    def _sync_status(self) -> dict:
        # Two attempts: a stale/desynced socket on attempt 1 is dropped and
        # rebuilt fresh for attempt 2, so transient faults self-heal within a
        # single poll rather than sticking until the next one.
        err = None
        for attempt in (1, 2):
            try:
                with self._lock:
                    d = self._ensure_locked()
                    r = d.status()
                    if isinstance(r, dict) and "dps" in r:
                        self._failures = 0
                        self._backoff = False
                        self._dps.update(r["dps"])
                        return dict(self._dps)
                    # Error dict — drop the socket so the retry reconnects clean.
                    err = r
                    self._close_locked()
            except Exception:
                # Connection/handshake failures raised from _ensure_locked land
                # here too, so the failure counter (and thus _backoff, used by
                # the UDP discovery fast-reconnect path) stays accurate.
                with self._lock:
                    self._close_locked()
                if attempt == 2:
                    self._note_failure()
                    raise
                continue
            if attempt == 2:
                self._note_failure()
                self._raise_for_error(err)

    def _sync_set(self, dps: dict) -> None:
        with self._lock:
            d = self._ensure_locked()
            # tinytuya wants string DP indices mapped to their values.
            r = d.set_multiple_values({str(k): v for k, v in dps.items()})
            err = r if (isinstance(r, dict) and r.get("Err")) else None
            if err is None:
                # Optimistically fold the written values into the cache (inside
                # the lock, so it can't race the status-path update).
                self._dps.update({str(k): v for k, v in dps.items()})
            else:
                # Drop the socket so the next operation reconnects clean.
                self._close_locked()
        if err is not None:
            self._raise_for_error(err)

    # ------------------------------------------------------------------
    # Public async API
    # ------------------------------------------------------------------

    async def async_get(self) -> None:
        if self._enabled is False:
            return
        await self._run(self._sync_status)

    async def async_set(self, dps: dict) -> None:
        if self._enabled is False:
            return
        await self._run(self._sync_set, dps)
        # Poll again shortly so entities reflect the new state without waiting
        # for the next scheduled update.
        if self.update_entity_state_cb is not None:
            asyncio.create_task(self._delayed_refresh())

    async def _delayed_refresh(self, delay: float = 1.5) -> None:
        await asyncio.sleep(delay)
        try:
            await self.update_entity_state_cb()
        except Exception:
            pass

    async def async_disable(self) -> None:
        self._enabled = False
        await self._run(self._close_under_lock)

    def _close_under_lock(self) -> None:
        with self._lock:
            self._close_locked()

    # ------------------------------------------------------------------
    # DPS 124 command transport (goto, clear, etc.)
    # ------------------------------------------------------------------

    async def async_send_dps124(self, method: str, data: dict) -> str | None:
        """
        Send a base64-JSON command via DPS 124 and return its result code
        ('O', 'F', 'S') or None if no matching echo arrived. The echo is matched
        on method + timestamp to avoid acting on a stale value.
        """
        ts = round(time.time() * 1000)
        payload = json.dumps({"method": method, "data": data, "timestamp": ts},
                             separators=(",", ":"))
        cmd = base64.b64encode(payload.encode()).decode()
        await self.async_set({"124": cmd})
        await asyncio.sleep(1.5)
        await self.async_get()
        echo_b64 = self._dps.get("124")
        if echo_b64:
            try:
                echo = json.loads(base64.b64decode(echo_b64).decode())
                if echo.get("method") == method and echo.get("timestamp", 0) >= ts:
                    return echo.get("result")
            except Exception:
                pass
        return None

    async def async_goto(self, x: int, y: int) -> bool:
        result = await self.async_send_dps124("goto", {"target": "go", "x": x, "y": y})
        return result == "O"

    async def async_clear(self) -> bool:
        result = await self.async_send_dps124("clear", {})
        return result == "O"
