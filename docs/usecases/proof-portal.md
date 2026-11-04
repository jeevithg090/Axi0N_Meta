# Proof portal: evidence an inspector, insurer, head office or union rep can check

## Why

Camera products give the owner a dashboard. The people who decide money and licences get
screenshots. A health inspector, an OSHA or EHS auditor, a workers' comp or liability insurer, an
event licensing officer, a chain's head office and the crew's union representative each need
proof they can check without trusting the site.

Rulecam already writes every candidate, verdict, action, acknowledgement, dispute and plan
approval to a hash-chained log, one file per site and UTC day, with a Merkle root per day. A proof
pack turns that log into the numbers each reader asks for. It discloses the chain records each
number was counted from, with Merkle inclusion proofs. The reader's browser then recomputes
every hash, every root and every number.

A pack is **evidence for** a period. It never certifies legal compliance; the reader decides what
it proves.

## What a pack holds

- **Rules in force** during the period, from the chained plan approvals (approving a plan replaces
  the active one, so each approval runs until the next). Each rule shows:
  - the acceptance gate chained at approval (`pass`, or `amber` with reasons), which includes
    Rule CI;
  - the Rule CI detail from the plan file (for example "boundary 16/16, mutations 6/6");
  - the clause it gives evidence for:
    - Muse Spark 1.3 is asked, with web search, which published clause sets the requirement that
      the rule's alerts give evidence about. It uses the planner's standards prompt, and the
      plan's own citation is passed as a hint when the plan has one.
    - For a distance rule, the lead clauses of the forklift evidence template
      (`evidence.packs.LEAD_CLAUSES["osha_pit"]`) are passed as a hint when that template exists.
    - The quote is marked checked only when it is found: first on the cited page fetched by
      `check_citation` (`check: page`); else in the search result for that URL; else on one of
      up to three other pages Spark's search returned (`check: search`, naming that page).
      Otherwise it is "quote not confirmed", with the reason.
    - Answers are cached per rule text in `<data>/compliance/clauses.json`.
- **Alerts:** raised, confirmed by Muse Glimmer, rejected by Glimmer, Glimmer unsure, sent to a
  person, and passed on as alerts by the policy. Tamper attempts are counted too.
- **Response:** actions (and how many were dry-run), acknowledgements by role, and median and
  90th-percentile times from alert to Glimmer's verdict, to first action and to acknowledgement.
- **Worker disputes:** filed, upheld and rejected, plus false-alarm labels.
- **Governance:** plan approvals, and two-key protected privacy settings
  (`worker/two_key.py`): proposed, changed, refused, and **changed without both keys**.
- **Breakdowns** by rule, by shift (site time zone, default early 06:00, late 14:00 and night
  22:00) and by camera. Coverage per camera (time OK and time BLIND) appears when `coverage`
  records are chained.
- **Near-miss section (forklift pack):** rules that measure a distance (`distance_m`, such as
  forklift separation) are listed as near misses, with how many Glimmer confirmed ("near miss to
  follow up"). Two more numbers come from the warehouse duty tracker's chained
  `label: refresher_training_done` records (`warehouse/duty.py`): how many refreshers were
  recorded done, and the median time from the near miss to that record. Each rule's clause comes
  from Spark's checked grounding, not from code. For OSHA forklift rules, the market research
  notes that 29 CFR 1910.178(l)(4)(ii)(B) requires refresher training after a near miss. Rulecam
  never identifies the operator.
- **Crew representative certificate** (when `worker_rep` is a reader):
  - protected privacy settings changed without both keys (checked against the chained two-key
    records from `worker/two_key.py`);
  - proposals, changes and refusals;
  - the rules in force;
  - dispute counts and outcomes;
  - that no number is per person. The browser checks this on every recipe: no filter on a
    subject, track, person, worker or approver path.
- **Hazard section (retail):** time from hazard seen to cleared, when a `label` record with
  `label: hazard_cleared` (or an `action` with `kind: cleared`) follows the alert's candidate.
- **Trend:** every number for this period and for the same length of time just before it.
- **Incidents:** alerts Glimmer confirmed or the policy passed on, each with its record key.
- **Summaries** for the inspector, insurer, head office and crew representative, written by Muse
  Spark 1.3 from the pack's facts. Spark never types a digit: it writes `{metric_id}` where a
  number goes and the pack fills it in. A sentence with a typed digit or an unknown metric is
  dropped. The crew representative's summary is written in the crew's language (`crew_lang`).
- **Notes:** what the pack cannot show (no coverage records, all actions dry-run, no
  acknowledgements, no two-key activity).
- **Stills:** up to six face-blurred or skeleton stills of incidents, as the privacy mode allows,
  only from the runtime's strips folder. They are illustrations; the chain does not commit to
  images.

**Privacy.** Numbers are per rule, camera and shift, never per person or tracked individual.
Records hold no images. The disclosed records do contain rule ids, camera ids, times,
measurements, verifier reasons and the track label the rules engine wrote (a per-track pseudonym,
never an identity).

## What the recipient's browser checks

The recipient page (`/inspector/view?t=<token>`) needs no login and is read-only. It runs these
checks with Web Crypto before showing "Verified in your browser":

1. **The pack is the one the link was made for:** the SHA-256 of the pack's canonical JSON equals
   the hash in the link's token.
2. **Every disclosed record is in the log, unchanged:** its contents hash to its recorded hash,
   and its Merkle inclusion proof folds up to its day's root.
3. **Nothing inside the period was left out:** every record of each day from the first record in
   the window to the last is disclosed, plus the record just before and just after.
4. **Every number recounted:** each number's recipe (`MetricSpec`) is run again over the disclosed
   records, for this period and the previous one. Both the value and the list of records must
   match.
5. **Every summary sentence re-filled** from the recomputed numbers.
6. **Each rule's gate** matches its chained approval record.
7. **No per-person numbers:** no recipe filters on a subject, track, person, worker or approver
   path.
8. **The live log:** the browser fetches the site's public `GET /evidence/{day}/hashes`. The first
   `n` hashes must reproduce the pack's root ("extended" means records were added after the pack
   was made). When `evidence/roots/<day>.txt` was committed to git, the committed root is checked
   the same way.

The recipes are defined once in `src/rulecam/compliance/engine.py` and mirrored in
`ui/src/app/inspector/_lib/engine.ts`. `tests/compliance/test_pack.py` writes
`ui/src/app/inspector/_lib/fixture.json` from a recorded chain and a simulated one. The UI's
`engine.test.ts` checks that the browser reproduces every number and the pack hash, and that it
catches an edited record, a changed number, a withheld record and a false summary sentence.

## API

Owner-only: from the owner's own machine, or with an owner session plus CSRF on writes, like every
owner route.

| Route | What it does |
|---|---|
| `POST /proof/packs` | Body `{start, end, title?, audiences?, crew_lang?, shifts?, models?, ground_rules?}`. Replies `202` with `{pack_id, status: "building"}`. The numbers take about a second; Spark's clauses and summaries take up to a few minutes. |
| `GET /proof/packs` | Lists packs (without bodies), newest first. |
| `GET /proof/packs/{id}` | `{status: building, ready or failed, pack, pack_hash, error}` |
| `GET /proof/packs/{id}/document?format=pdf\|html` | PDF through Playwright. Without Chromium, the printable HTML (`X-Rulecam-Report-Format: html`). |
| `POST /proof/packs/{id}/share` | Body `{hours (default 72, at most 90 days), ui_origin?}`. Replies `{url, token, expires_at, pack_hash, qr}`; `qr` is a PNG data URI. |
| `POST /proof/packs/{id}/revoke` | Stops every link to the pack. |

Public (the token is the permission):

| Route | What it does |
|---|---|
| `GET /proof/shared/{token}` | `{pack, pack_hash, expires_at}`. `403` for a forged token. `410` when the link has expired or been revoked, or the stored pack no longer matches the hash in the link. |
| `GET /proof/shared/{token}/document?format=pdf\|html` | The printout, with the QR code to the link. |
| `GET /evidence/{day}/hashes` | Existing public route; the browser's live-log check. |

**Tokens.** A token is `<payload>.<signature>`. The payload is base64url JSON
`{p: pack id, h: pack hash, x: expiry}` and the signature is HMAC-SHA256 with
`RULECAM_SHARE_SECRET`. Without that setting, a random key is created once in
`<data>/compliance/share.key` (mode 0600). Packs are stored in `<data>/compliance/packs/`.

## Demo steps

1. Start the API and the UI (`make api`, `make ui`), with real chains under
   `<RULECAM_DATA_DIR>/evidence/chain/<site>/`.
2. Open `/inspector`. Pick the days (the page lists the chain days), choose the readers, set the
   crew language (for example `pl` or `hi`) and press **Create proof pack**.
3. When it is ready, the page runs the recipient's checks and shows the pack. Press **Make share
   link and QR**.
4. Scan the QR with a phone, or open the link in a private window. The page shows "✓ Verified in
   your browser" with the record, window, number, summary and rule checks, and compares each
   day's root with the site's live log.
5. To see the checks fail: in the browser's dev tools, change any number or record in the loaded
   pack and run the checks again. `engine.test.ts` does exactly this.
6. **Download PDF** gives the printable pack with the QR code and the pack hash.

For packs built outside the API (eval, demos), use `scripts/compliance/proof_pack.py`. Its
docstring shows the command line.

## Hooks other parts need

- **Coverage:** `perception/coverage.py`'s watchdog publishes `coverage.changed` on the bus, but
  nothing chains it. Coverage and BLIND hours appear as soon as the runtime appends
  `coverage` records `{camera_id, state: ok or blind, reason, ts}` on each change.
- **Acknowledgements:** `act/escalation.py` chains `ack` records; the live chains have none
  because actions ran in dry-run mode.
- **Retail time-to-clear:** chain `label` `{event_id, label: "hazard_cleared"}` when staff clear
  a spill.
- **Training records:** the warehouse branch (`p1/uc-warehouse`) chains
  `refresher_training_done`. The pack counts it already; it shows up once that branch is merged
  and used.
- **osha.gov refuses our page fetcher** (HTTP 403), and FDA Food Code pages are PDFs. Clause
  quotes from those sites stay unconfirmed unless another searched page carries the same text. A
  standards text source the fetcher can read (eCFR's API, or the text uploaded with the plan)
  would confirm them.
- **Committed roots:** commit `evidence/roots/<day>.txt` daily (`rulecam evidence root`), so the
  browser can check each day against git as well as against the live log. Chain Mode's HQ root
  (`chain/rollup.py`) can anchor many stores at once.
