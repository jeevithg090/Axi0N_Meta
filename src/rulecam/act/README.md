# `rulecam.act`: actions, escalation, interlock, LAN alerts (P3-06)

## Purpose

This package turns a policy decision into things that happen on site: a spoken warning, a red
strobe, a station display, role notifications, escalation chains with acknowledgement timers, and
the smart-plug power cut. **Stopping is automatic. Resuming always needs a person.**

The runtime builds all of this from settings in `rulecam.runtime.response.build_response`
(WS1b), with `ActionExecutor(dry_run=True)` until the owner sets `RULECAM_ACTIONS_DRY_RUN=false`:
a dry run chains every allowed action as `dry-run: would …` and executes none.

## Public API

```python
from rulecam.act import (
    ActionExecutor,
    EscalationManager,
    Interlock,
    LanNotifier,
    PhraseBook,
    alerts_router,
)
from rulecam.act.devices import KasaPlug, StrobeLight, CommandSpeaker

notifier = LanNotifier(bus=bus, clock=clock)
escalation = EscalationManager(notifier=notifier, clock=clock, chain=chain, bus=bus)
interlock = Interlock(
    {"forklift": KasaPlug(settings.kasa_plug_host)}, clock=clock, chain=chain, bus=bus
)
executor = ActionExecutor(
    clock=clock,
    notifier=notifier,
    escalation=escalation,
    interlock=interlock,
    speaker=CommandSpeaker("afplay"),  # "aplay" on Linux
    light=StrobeLight(KasaPlug(settings.kasa_light_host)),
    phrases=PhraseBook.from_prerendered(rule_phrases, prerendered, plan.languages),
    bus=bus,  # coaching station display (`station.coach`, mirrored on /ws)
)
interlock.connect()  # at start-up: discovery now, so the first cut is one round trip
# AppServices(..., routers=[alerts_router(notifier=..., escalation=..., chain=..., interlock=...,
#                                         roles=plan_roles, dependencies=[<real auth>])],
#             tickers=[escalation.tick])
```

- **Order:** `interlock_cut` first, then `notify`/`escalate`, then the blocking outputs (strobe,
  speaker, station). For HIGH rules the runtime calls the executor before verification, so the plug
  is off within milliseconds (tested: under 200 ms with a 500 ms verifier), and people are told
  before a multi-second warning plays.
- **Cut:** every plug is tried even if one fails; failures are named in one `DeviceError`.
- **Human queue:** only `notify` and `escalate` run, worded "Needs review". Suppressed alerts run
  nothing. When a verifier demotes a HIGH alert that already acted, the runtime chains the decision
  and runs the human-queue actions so a person reviews it.
- **`escalate`:** the `role` field lists the chain, comma-separated (`"aide, charge_nurse, on_call"`),
  and `after_s` is the wait between steps (default 60 s). An ack cancels the rest. After the last
  role the chain stays open (no more timers) until acknowledged.
- **Buttons** (`POST /alerts/{id}/respond`, on `notify` and escalation alerts): *On my way* acks.
  *False alarm* acks and appends a `label` record (`event_id`, `rule_id`, `role`) for rule health.
  *Escalate* notifies the next role now. Every ack is chained, with or without an open chain.
- **Interlock resume:** `POST /interlock/{device}/resume {role}`. An empty role gets 403; a plug
  that does not respond gets 502. Each resume is chained as `interlock_resume` with `gate: human`.
- **Input validation:** roles are identifiers (`^[A-Za-z][A-Za-z0-9_-]*$`, at most 64 chars) and
  event ids are bounded; with `roles=` given, any other role gets 403.
- **Devices:** a failing device gives `ok=False` on its record and never blocks the other actions.
  `KasaPlug` wraps every python-kasa error as `DeviceError` and cancels a timed-out command.
- Every action, escalation step, ack and label is in the evidence chain.

## Tests

```bash
uv run pytest tests/act
```
