# Changelog

This is a fork of [8none1/eufy-x8](https://github.com/8none1/eufy-x8). Only the
changes made in the fork are listed here; for the upstream history see the
original repository.

## [Unreleased] — fork: multi-version Tuya support

### Added
- **Tuya protocol v3.4 / v3.5 support**, enabling newer Eufy models such as the
  **X8 Pro Hybrid SES (T2276)**, which speaks v3.5 (AES-GCM + session-key
  negotiation) and previously timed out against the v3.3-only client.
- **Automatic protocol-version detection** at connect time (tries v3.5, v3.4,
  v3.3, v3.1 and caches the one that works). No configuration needed; original
  X8 / X8 Pro (T2262 / T2262EV, v3.3) continue to work unchanged.
- Case-insensitive mapping of the DPS 15 work-status string. Different models
  report different casing (e.g. `Sleeping` vs `completed`); `activity_for()` and
  the detailed-status lookup now normalise case.

### Changed
- **Transport layer rewritten** (`api/local.py`): the hand-rolled Tuya v3.3
  implementation is replaced by a thin wrapper around
  [`tinytuya`](https://github.com/jasonacox/tinytuya) (v3.1–v3.5). The public
  `TuyaDevice` interface, entities, services, and behaviour are unchanged, so no
  caller (coordinator/vacuum/sensor/switch/select) needed modification.
- Sockets are **non-persistent** (a fresh connection per poll). A long-lived
  v3.4/v3.5 session can desync and then return error 904 ("Unexpected Payload")
  indefinitely; a fresh socket per ~30s poll avoids this, and any error drops
  the socket and retries once so transient faults self-heal within one poll.
- `manifest.json` now declares `requirements: ["tinytuya==1.20.0"]`.

### Fixed
- Version auto-detection classifies an error as `InvalidKey` if **any** candidate
  version reported a key/version error (914), rather than only inspecting the
  last candidate tried — so a genuine key rejection on the real protocol version
  is no longer masked by a different error from a later candidate.
- The failure counter (and therefore `_backoff`, used by the UDP-discovery
  fast-reconnect path) is now incremented on total connection failure, not only
  on well-formed error responses.
- The cloud local-key refresh has a 300 s cooldown, preventing repeated Eufy
  cloud logins when a persistent fault keeps surfacing 914 (which is ambiguous
  between "bad key" and "handshake failure" on v3.4/v3.5).

### Tests
- `tests/test_local.py` rewritten to cover the new transport (version
  auto-detection, error classification, failure/backoff accounting, optimistic
  write cache, and DPS-124 goto echo parsing) using a mocked `tinytuya` — no real
  device required.
