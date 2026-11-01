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

## WhatsApp escalation (P3-S1)

Set `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID` and `WHATSAPP_RECIPIENTS`
(`aide=15551234567,charge_nurse=…`, kept only in `.env`). `build_response` then sends escalations
and notifications through `WhatsAppNotifier`:

- **The LAN app always gets the alert** (it is the fallback and holds the alert id).
- A role whose phone messaged "start" in the last **24 hours** also gets an interactive
  **reply-button** message. The skeleton clip or still is uploaded as WhatsApp media and used as
  the header, never exposed at a public URL. Outside the window the Cloud API allows only
  templates, so the LAN app carries it alone.
- **Webhook:** with `WHATSAPP_APP_SECRET` and `WHATSAPP_VERIFY_TOKEN` set,
  `/whatsapp/webhook` is mounted outside owner auth, because Meta calls it.
  - The `GET` handshake checks the verify token.
  - Every `POST` must carry a valid `X-Hub-Signature-256`.
  - Taps run the same `handle_button` as the app (ack, false-alarm label, escalate).
  - Numbers that aren't a configured role are ignored.
- Phone numbers are never logged or chained: only roles are.

## Response drills (P3-S2)

`POST /drills {roles, after_s, scenario}` (owner-only, under the alerts router) starts a **labelled
practice alert through the real escalation chain**. Every message says "DRILL", and the start is
chained as `label {drill: true}`. `GET /drills/report?target_s=60` measures the time from each
drill's start to its first `ack`, from the chain alone, **by role, never by person** (acks carry
only roles): counts, missed drills, how many met the target, and the median, p90 and max per role.
`f689_row(report)` turns this into an F689 evidence-pack row.
