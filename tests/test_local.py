"""Unit tests for the tinytuya-backed transport (api/local.py).

The transport module has no Home Assistant dependency, so these tests load it
directly by path and inject a fake ``tinytuya`` — they run without the HA test
harness. ``tinytuya`` itself is never contacted; every device response is
scripted, so no real robot is touched.
"""
import asyncio
import base64
import importlib.util
import json
import os

import pytest

# ---------------------------------------------------------------------------
# Load api/local.py in isolation (no package __init__, hence no homeassistant).
# ---------------------------------------------------------------------------
_LOCAL_PATH = os.path.join(
    os.path.dirname(__file__), "..",
    "custom_components", "eufy_x8", "api", "local.py",
)
_spec = importlib.util.spec_from_file_location("eufy_x8_local", _LOCAL_PATH)
local = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(local)


# ---------------------------------------------------------------------------
# Fake tinytuya.Device — returns scripted status()/set results per version.
# ---------------------------------------------------------------------------
class FakeDevice:
    # Class-level scripting, reset by each test.
    status_by_version: dict = {}
    set_result: dict = {"dps": {}}
    persistent = None  # records the last set_socketPersistent() argument

    def __init__(self, dev_id, host, key, version=None):
        self.dev_id = dev_id
        self.host = host
        self.key = key
        self.version = version
        self.closed = False

    def set_socketTimeout(self, *_):
        pass

    def set_socketPersistent(self, value):
        FakeDevice.persistent = value

    def set_socketRetryLimit(self, *_):
        pass

    def close(self):
        self.closed = True

    def status(self):
        return FakeDevice.status_by_version.get(
            self.version, {"Error": "conn", "Err": "905"})

    def set_multiple_values(self, data, nowait=False):
        return FakeDevice.set_result


@pytest.fixture(autouse=True)
def _fake_tinytuya(monkeypatch):
    """Point the module's ``tinytuya`` at our fake and reset scripting."""
    FakeDevice.status_by_version = {}
    FakeDevice.set_result = {"dps": {}}
    FakeDevice.persistent = None
    fake_module = type("t", (), {"Device": FakeDevice})
    monkeypatch.setattr(local, "tinytuya", fake_module)
    yield


def make_device():
    return local.TuyaDevice(
        "dev123", "10.0.0.9", timeout=5, ping_interval=10,
        update_entity_state=None, local_key="0123456789abcdef", version=None,
    )


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Version auto-detection
# ---------------------------------------------------------------------------
def test_autodetects_v35_and_reads_dps():
    FakeDevice.status_by_version = {3.5: {"dps": {"104": 88, "15": "Charging"}}}
    d = make_device()
    run(d.async_get())
    assert d._version == 3.5
    assert d.state["104"] == 88


def test_uses_first_working_version_v33():
    # v3.5/v3.4 fail, v3.3 works — the original X8 (T2262) path.
    FakeDevice.status_by_version = {
        3.5: {"Error": "payload", "Err": "904"},
        3.4: {"Error": "payload", "Err": "904"},
        3.3: {"dps": {"104": 50}},
    }
    d = make_device()
    run(d.async_get())
    assert d._version == 3.3
    assert d.state["104"] == 50


def test_uses_non_persistent_socket():
    FakeDevice.status_by_version = {3.5: {"dps": {"1": True}}}
    run(make_device().async_get())
    assert FakeDevice.persistent is False


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------
def test_key_error_on_any_candidate_raises_invalidkey():
    # v3.5 (the real version) says 914, but a later candidate says 904.
    # The 914 must NOT be masked by the last candidate's error.
    FakeDevice.status_by_version = {
        3.5: {"Error": "key", "Err": "914"},
        3.4: {"Error": "key", "Err": "914"},
        3.3: {"Error": "payload", "Err": "904"},
        3.1: {"Error": "payload", "Err": "904"},
    }
    with pytest.raises(local.InvalidKey):
        run(make_device().async_get())


def test_no_key_error_raises_connection_exception():
    FakeDevice.status_by_version = {
        v: {"Error": "payload", "Err": "904"} for v in (3.5, 3.4, 3.3, 3.1)
    }
    with pytest.raises(local.ConnectionException):
        run(make_device().async_get())


# ---------------------------------------------------------------------------
# Failure counting / backoff (consumed by the UDP discovery fast-reconnect)
# ---------------------------------------------------------------------------
def test_failures_counted_and_backoff_set_on_total_failure():
    FakeDevice.status_by_version = {
        v: {"Error": "payload", "Err": "904"} for v in (3.5, 3.4, 3.3, 3.1)
    }
    d = make_device()
    for _ in range(5):
        with pytest.raises(local.TuyaException):
            run(d.async_get())
    assert d._failures == 5
    assert d._backoff is True


def test_success_resets_failures():
    FakeDevice.status_by_version = {3.5: {"dps": {"1": True}}}
    d = make_device()
    d._failures = 3
    d._backoff = True
    run(d.async_get())
    assert d._failures == 0
    assert d._backoff is False


def test_reset_backoff():
    d = make_device()
    d._failures = 9
    d._backoff = True
    d.reset_backoff()
    assert d._failures == 0
    assert d._backoff is False


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------
def test_set_folds_values_into_cache_with_string_keys():
    FakeDevice.status_by_version = {3.5: {"dps": {"1": True}}}
    FakeDevice.set_result = {"dps": {"2": False}}
    d = make_device()
    run(d.async_get())
    run(d.async_set({"2": False, 5: "auto"}))  # mixed key types on purpose
    assert d.state["2"] is False
    assert d.state["5"] == "auto"


def test_set_error_raises_invalidkey():
    FakeDevice.status_by_version = {3.5: {"dps": {"1": True}}}
    FakeDevice.set_result = {"Error": "key", "Err": "914"}
    d = make_device()
    run(d.async_get())
    with pytest.raises(local.InvalidKey):
        run(d.async_set({"2": True}))


# ---------------------------------------------------------------------------
# update_local_key validation
# ---------------------------------------------------------------------------
def test_update_local_key_rejects_bad_length():
    d = make_device()
    with pytest.raises(local.InvalidKey):
        d.update_local_key("too-short")


def test_constructor_rejects_bad_key():
    with pytest.raises(local.InvalidKey):
        local.TuyaDevice("dev", "10.0.0.9", timeout=5, ping_interval=10,
                         update_entity_state=None, local_key="short")


# ---------------------------------------------------------------------------
# DPS 124 command transport (goto / clear) echo parsing
# ---------------------------------------------------------------------------
def test_goto_accepted_when_echo_result_ok():
    d = make_device()

    async def scenario():
        await d.async_get()  # detect version first

        sent = {}

        async def fake_set(dps):
            sent.update(dps)

        async def fake_get():
            # Echo back a matching goto result 'O' in dps 124.
            cmd = json.loads(base64.b64decode(sent["124"]).decode())
            echo = {"method": cmd["method"], "timestamp": cmd["timestamp"],
                    "result": "O"}
            d._dps["124"] = base64.b64encode(
                json.dumps(echo).encode()).decode()

        d.async_set = fake_set
        d.async_get = fake_get
        return await d.async_goto(2283, -363)

    FakeDevice.status_by_version = {3.5: {"dps": {"1": True}}}
    assert run(scenario()) is True


def test_goto_rejected_when_no_echo():
    d = make_device()

    async def scenario():
        await d.async_get()

        async def fake_set(dps):
            pass

        async def fake_get():
            pass  # no echo written

        d.async_set = fake_set
        d.async_get = fake_get
        return await d.async_goto(1, 2)

    FakeDevice.status_by_version = {3.5: {"dps": {"1": True}}}
    assert run(scenario()) is False
