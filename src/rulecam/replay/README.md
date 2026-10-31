# `rulecam.replay`: replay cache, golden runs, preflight (P3-02)

## Purpose

Every model call goes through the replay cache so the demo can run with or without the network, and
the UI can always say which one it is doing (the LIVE/REPLAY badge, MP §1).

| Mode (`RULECAM_MODE`) | Calls the model | Stores the reply | Badge |
|---|---|---|---|
| `live` | yes | no | LIVE |
| `record` | yes | yes | LIVE |
| `replay` (default) | **never** (a miss raises `ReplayMiss`) | — | REPLAY |

Key: `sha256(provider | model | canonical_json(request))`. Canonical JSON sorts keys, drops
whitespace and replaces any `bytes` value by `{"__sha256__": …}`. Entries live in
`<RULECAM_REPLAY_DIR or data/replay>/<provider>/<key>.json` with `recorded_at` and `latency_ms`,
and are written atomically (temp file + `os.replace`).

## Public API

```python
from rulecam.replay import ReplayCache, cached_call, ReplayMiss
from rulecam.replay.cache import using

# Simple: the process-wide cache built from settings.
reply = cached_call("meta", "muse-spark-1.3", request, lambda: transport(request))

# Clients built on `Transport` (see rulecam.clients.transport) wrap their transport once:
cache = ReplayCache.from_settings(settings, clock=clock, bus=bus)
transport = cache.wrap_transport("meta", "sam-3.1", HttpxTransport(base_url, key))

cache.set_mode("replay")  # publishes mode.changed {mode, badge}

with using(cache):  # make `cache` the process-wide one for a block (golden runs do this)
    ...
```

`wrap_transport` records **only 2xx replies** (429 and 5xx go back to the client's retry loop and
are never stored), and a replay hit carries the recorded latency in the
`x-rulecam-recorded-latency-ms` reply header so `relay.model_used` stays truthful in REPLAY.
`HostedSam31.from_settings` always wraps its transport this way; `replay` needs no API key.
Provider names may not be empty, `.`/`..`, or contain `|` or path separators; model IDs may not
contain `|`. NaN and infinity in a request raise `ValueError`.

Golden runs (`scenarios/README.md`): packages register step kinds with
`rulecam.replay.golden.register_step("<kind>")`, then

```bash
make golden SCENARIO=uc1                 # RULECAM_MODE=record, writes scenarios/uc1.expected.json
uv run rulecam golden verify uc1         # replay only; exit 1 and a diff per changed step
```

Preflight: `make preflight` prints a ✓/✗ table (`-` skipped, `!` optional failure) and exits 1 on
any required failure: env keys, Meta API, Baseten Spark Open `reasoning_content`, Glimmer
`/v1/models`, Modal CLI, fresh camera chunks, Kasa plug and light, speaker tone, ≥ 10 GB free.

## Tests

```bash
uv run pytest tests/replay
```
