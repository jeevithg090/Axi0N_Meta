# `rulecam.act`: actions, escalation, interlock, LAN alerts (P3-06)

## Purpose

This package turns a policy decision into things that happen on site: a spoken warning, a red
strobe, a station display, role notifications, escalation chains with acknowledgement timers, and
the smart-plug power cut. **Stopping is automatic. Resuming always needs a person.**

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
)
# AppServices(..., routers=[alerts_router(notifier=..., escalation=..., chain=..., interlock=...)],
#             tickers=[escalation.tick])
```

- **Order:** `interlock_cut` runs before every other action. For HIGH rules the runtime calls
  the executor before verification, so the plug is off within milliseconds (tested: under 200 ms
  with a 500 ms verifier).
- **Human queue:** only `notify` and `escalate` run, worded "Needs review". Suppressed alerts run
  nothing.
- **`escalate`:** the `role` field lists the chain, comma-separated (`"aide, charge_nurse, on_call"`),
  and `after_s` is the wait between steps (default 60 s). An ack cancels the rest.
- **Buttons** (`POST /alerts/{id}/respond`): *On my way* acks. *False alarm* acks and appends a
  `label` record for rule health. *Escalate* notifies the next role now.
- **Interlock resume:** `POST /interlock/{device}/resume {role}`. An empty role gets 403. Each resume
  is chained as `interlock_resume` with `gate: human`.
- **Devices:** a failing device gives `ok=False` on its record and never blocks the other actions.
- Every action, escalation step, ack and label is in the evidence chain.

## Tests

```bash
uv run pytest tests/act
```
