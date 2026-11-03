# `rulecam.evidence`: hash-chained event log (P3-07a)

## Purpose

Every candidate, verdict, action, ack, dispute, plan, advice and tamper attempt is appended to an
append-only JSONL chain, one file per site and UTC day (`<root>/<site>/<YYYY-MM-DD>.jsonl`).
Editing, deleting or reordering any record breaks the chain at that record (MP §0, §7.4).

Each line is an `EventRecord` (contracts §4.4) in canonical JSON: sorted keys, no whitespace,
UTF-8, keys sorted by UTF-16 code units, UTC timestamps (`…Z`), and numbers written exactly as JavaScript `JSON.stringify` writes them,
so the browser verify page (P3-07b) recomputes the same hashes.

```
hash = sha256(prev_hash + canonical(record without "hash"))   # genesis prev_hash = "0" * 64, seq starts at 1
```

## Public API

```python
from rulecam.evidence import EventChain, verify_chain

chain = EventChain(settings.rulecam_data_dir / "evidence" / "chain", "main-st-kitchen", clock=clock)
record = chain.append("verdict", verdict)  # a contract model or a JSON mapping
report = verify_chain(chain.path_for(day))  # ChainReport(ok, first_bad_seq, n, reason)
```

Writers hold an exclusive `flock` on `<file>.lock` (POSIX), so concurrent appenders stay linked.
Payloads must be JSON: no NaN or infinity, no sets, no lone surrogates, integers within
JavaScript's safe range (|n| <= 2**53 - 1), and aware datetimes (written as UTC `…Z`, in
payloads too). Keys sort by UTF-16 code units, as JavaScript's default `sort()` does, so the
browser verify page must sort the same way.

## Tests

```bash
uv run pytest tests/evidence
```

## Roots, verify page, packs, audit (P3-07b)

- **Daily Merkle root** (`merkle.py`). The leaves are a day's record hashes; each node is
  `sha256(left_hex + right_hex)`, and an odd node is paired with itself. Run
  `uv run rulecam evidence root <YYYY-MM-DD>` to write `evidence/roots/<date>.txt`
  (`<site> <root> <n>` per site), then **commit it every day**.
- **Verify page** (`verify_page/`, served at `/verify/?day=YYYY-MM-DD`). `chain.js` recomputes
  every hash and the Merkle root in the browser with Web Crypto, then shows either
  "✓ Chain intact · N records · watched X%" or "✗ Chain broken at event N". It also compares the
  result with the committed root (`GET /evidence/{day}/root`): the chain's first `committed_n`
  records must reproduce it, so a day that kept growing after its root was committed still
  verifies, while dropped or rewritten records do not. Anyone can drop a `.jsonl` file on it.
  Both verifiers accept only the record shape writers produce (integer `seq`, `ts` exactly as
  `utc_iso` writes it), so they flag the same first bad event. Cross-language check: `tests/evidence/test_verify_page.py` writes the fixture, and
  `cd src/rulecam/evidence/verify_page && npm install && npx vitest run` checks the JS against it.
  That pytest also runs the JS directly when `node` is on PATH.
- **Evidence packs** (`packs.py`, `templates/pack.html.j2`).
  - Each row runs clause → rule → compliance (Wilson 95% CI) → watched fraction → thumbnails →
    corrective actions, and the pack ends with a QR code to the verify page.
  - Templates: `food_code` (leads with §2-103.11(G), then §2-301.12), `f689` and `generic`.
  - Packs never hold clip or file paths: stills are inlined as data URIs. Anonymous and strict modes keep skeleton thumbnails only, and raw
    frames are never allowed in any mode.
  - `render_pdf` prints the pack through Playwright (`uv sync --extra ui` and
    `playwright install chromium`).
- **Incident reports** (add-on 5.2, `incident.py`, `templates/incident.html.j2`). One event's
  pack: clause and rule, the measurement, and every `candidate`, `verdict`, `action`, `ack`,
  `label` and `dispute` record for it in chain order (each with its hash), the stills the privacy
  mode allows (only from the runtime's `<data>/strips`, inlined), the day's chain status and the
  verify-page QR code.
  - Owner-only routes: `GET /incidents?day=` (a day's events, newest first; default the latest
    day), `GET /incidents/{event_id}` (JSON), and `GET /incidents/{event_id}/report`
    (`?format=pdf|html`). The PDF goes through `render_pdf`. When Playwright or Chromium is
    missing, the same route returns the printable HTML (`X-Rulecam-Report-Format: html`).
  - The UI shows them at `/incidents` and `/incidents/<event id>`, with a download button.
  - The QR points at `<PUBLIC_UI_URL>/rulecam-api/verify/?day=…`, or at the API's own `/verify/`
    when `PUBLIC_UI_URL` is unset.
- **Audit:** `uv run rulecam audit <YYYY-MM-DD> <site>` verifies the chain, then runs P1-07's
  `rederive` on every candidate, giving "chain intact (N records); K/K decisions reproduced" (E9).
  The runtime loop now chains candidates as `candidate_payload(candidate, DecisionContext)` so
  they can be re-derived.
