# rulecam.worker — worker rules page, tap disputes and poster (P2-11)

The public, worker-facing surface (MP §3.5): what the camera watches, what it never does, how long
events are kept, anonymous event cards and per-rule counts, plus an anonymous tap dispute that
Spark Open re-reviews. It is reachable by anyone holding the poster's QR link.

| Route | What |
|---|---|
| `GET /worker/{plan_id}/{camera}?lang=` | `WorkerPage`: back-translated rules (P2-07, same text as the rule cards), never list, privacy mode, retention, event cards (short code, rule, 15-minute bucket, latest dispute outcome), counts per rule (alerts, disputes, upheld) |
| `GET /worker/{plan_id}/{camera}/poster?lang=` | printable A4 HTML with a QR code to the page; deterministic; 503 without `PUBLIC_UI_URL` |
| `POST /worker/dispute {event_code, reason_choice, reason_text}` | `DisputeResult`: outcome and the rule's updated counts |

The page path matches the rule cards' QR link (`cards.build.worker_url`). Set `PUBLIC_UI_URL` to
the public origin of the web UI (`ui/src/app/worker/[site]/[camera]`), which calls this API
through its `/rulecam-api` rewrite.

## Privacy

- Event cards carry a short code (8 hex digits of a hash of the event id), the rule and a
  15-minute bucket: no event id, track id, trace, strip or clip path, or exact time. Skeleton
  thumbnails are left out until the strip renderer (P4-05) attaches skeleton-only renders.
- Disputes store the reason and the review only. The rate limit keys an HMAC of the client
  address with a random salt that is replaced, with every counter, at each UTC day boundary;
  nothing about the client is logged or written.
- Spark Open's reasoning goes into the chain, not back to the public page.
- `tests/guardrails/test_worker_anonymous.py` checks all of this.

## Disputes

1. Abuse bounds: 5 per client and 200 per site per day (`DailyLimiter`), 3 per event, 4 KiB JSON
   bodies only, `reason_text` at most 500 characters with control characters removed.
2. The event must be a candidate of the last 14 days in the site's chain (404 otherwise).
3. A `dispute` record (`pending`) is chained first, so a dispute is never lost.
4. Tier 2 re-reviews it (`review_dispute`, P3-04); the worker's text is framed as data in the
   prompt. A review that fails stays `pending` for a person, never upheld by default.
5. The reviewed `dispute` record is chained under the same `dispute_id`; an upheld dispute also
   chains a `false_alarm` `label` (`source: worker_dispute`) for rule health.

Counts and cards are re-derived from the chain on each request (`worker.log`).

```bash
uv run pytest tests/worker tests/guardrails/test_worker_anonymous.py
```
