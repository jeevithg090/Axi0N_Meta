# Supermarket: from a question to a fixed problem, with a receipt

**The pitch.** Your cameras already see the queue, the empty shelf and the spill. Rulecam tells
the right person in their own language, checks it twice, and keeps a receipt that it was fixed.

Everything here runs on the cameras a shop already has. Every judgement is made by a Meta model
from the raw evidence (frames, SAM 3.1 boxes, the recorded tracks, earlier readings); the code
only gathers that evidence, stores what the models said, and refuses numbers the evidence does
not contain.

## The story in one minute

1. **Ask.** The owner types or says a question in any language ("When was the checkout queue
   longest, and do we need another till?"). Muse Spark 1.3 answers with its own tool plan: SQL
   over the SAM 3.1 tracks and the stored checks, SAM 3.1 counts, Muse Glimmer looks, Spark
   watching the recorded clip. The answer is read aloud and carries a **receipt**: the browser
   re-hashes the logged answer itself and a QR code lets a judge check it on a phone.
2. **Advice.** One tap turns the answer into a Site Advisor card: what was seen (every number
   must appear in the answer's evidence, or it is hidden), one change (open another till), who
   does it and how to undo it, and what queueing models (M/M/c, Allen-Cunneen) say one more till
   would save, computed by code from the measured arrival rate and service time.
3. **Accept.** One tap pre-registers a switchback test on the hash-chained evidence log (blocks
   of the change on and off, the decision rule, a hash of the plan) *before anything changes*,
   and turns on a standing watch on the camera that measures it.
4. **The watch tells staff, with the photo.** Every few minutes the watch runs its check. When
   the models say act (open another till, a customer waiting with nobody serving, a spill, an
   empty facing), staff get an alert with the marked photo, *On my way* / *False alarm* buttons
   and, for a till, an announcement for customers in the store's languages.
5. **Fixed, and proven fixed.** Shelf gaps and hazards open staff tasks. A task closes when a
   staff photo convinces Muse Spark (and Muse Glimmer, when it answers) that every gap is filled
   or the hazard is gone, or when the next check of the same camera sees it fixed. The close is
   written to the evidence chain with *minutes from seen to fixed*.
6. **Brief.** Muse Spark writes the owner's brief from the stored checks, alerts, tasks, the
   questions asked and the running test, in the owner's language, and the page reads it aloud.
   Ask can query the same records (`retail_findings`, `retail_tasks`).

## Which model does what

| Step | Model | What it decides |
|---|---|---|
| Queue check | SAM 3.1 (people on 3 frames, in parallel) → Muse Spark 1.3 (frames + numbered boxes + earlier readings) | lanes open, customers waiting, queue moving, rough wait, a customer needing help, open another till, alert now, the announcement |
| Hazard check | Muse Spark 1.3 → SAM 3.1 (the phrase Spark names, inside Spark's box) and Muse Glimmer (second look at the marked frame) | spill, broken glass, fallen stock, obstruction. Counted when Glimmer agrees; SAM stands in only when Glimmer gives no answer within 25 s |
| Shelf check | Muse Spark 1.3 (gaps on a 0-1000 grid, products read from packs) → SAM 3.1 (`product package` must cover at most 25% of a gap) | which facings are empty and what goes there |
| Task proof | Muse Spark 1.3 and Muse Glimmer (before image with numbered boxes + staff photo) | every gap filled / hazard cleared |
| Ask | Muse Spark 1.3 tool loop, SAM 3.1, Muse Glimmer, Muse Spark 1.2 (video), Spark Open review | the answer, with the steps and a receipt |
| Spoken question | Muse Voice Transcribe | the words and language; the browser speaks the answer |
| Advice card | Muse Spark 1.3 (from the Ask run) | the change, the metric, the switchback size, which watch to run |
| Brief | Muse Spark 1.3 | the brief's words; the counts are counted by code |

## API contract (all routes owner-only, under `/retail`)

| Method and path | Body | Returns |
|---|---|---|
| `GET /retail/overview` | | `{cameras, watches, findings (with alert_status), tasks, cards (with progress), asks, brief}`: everything the page shows |
| `GET /retail/cameras` | | `[{camera_id, name, first, last}]`: cameras with recorded video |
| `POST /retail/checks/{queue\|hazard\|shelf}` | `{camera_id, at?, role?}` | `Finding`: the check on the recorded instant `at` (default: newest), alerting `role` (default `tills`, `floor`, `stock`) if the models say act |
| `POST /retail/checks/{hazard\|shelf}/image` | multipart `image` (JPEG/PNG), `role?` | `Finding` for a staff photo |
| `GET /retail/findings?kind=&limit=` | | `[Finding]`, newest first |
| `GET /retail/files/{name}` | | a frame, marked image or photo a finding or task names |
| `GET /retail/watches` · `POST /retail/watches` | `{kind, camera_id, every_s (15-86400), role, focus}` | `Watch` |
| `POST /retail/watches/{id}/run` · `DELETE /retail/watches/{id}` | | `Finding` · the stopped `Watch` |
| `GET /retail/tasks?role=&open_only=` | | `[StaffTask]`, newest first: **the staff app's feed** |
| `POST /retail/tasks/{id}/proof` | multipart `image` | `StaffTask`: closed (`closed_by: photo`, `minutes_to_fix`, `fixed_seq`) only if the models agree it is fixed |
| `POST /retail/tasks/{id}/status` | `{status: open\|on_my_way\|done\|false_alarm}` | `StaffTask` (`done` by hand is `closed_by: staff`, unchecked) |
| `GET /retail/asks?limit=` | | the newest Ask answers with their receipts |
| `GET /retail/receipts/{sha}?link=` | | `{receipt, prev_sha256, hashed_text, question, answer, models, rows_verified, qr_png_base64}`; `sha256(prev_sha256 + hashed_text) == receipt` |
| `POST /retail/advice` | `{receipt, camera_id?}` | `RetailCard`: the card (also on `/advice`), its numbers with grounding, the what-if, the switchback plan and the proposed watch |
| `POST /retail/advice/{id}/accept` | | `RetailCard` with `preregistered_seq` and `watch_id` |
| `POST /retail/advice/{id}/blocks` | `{state: on\|off, start, end}` | `RetailCard` with `blocks` (each the mean of the watch's readings in it, written to the chain as an `advice` record of `type: block`) and, once a pair is done, `verdict` by the pre-registered rule; after the planned pairs the card becomes `adopted`, `reverted` or `rejected` and a `type: verdict` record is written |
| `POST /retail/brief` · `GET /retail/brief` | `{lang}` | `Brief` |
| `POST /retail/ask/voice` | multipart `audio` (16-bit PCM WAV), `lang?`, `conversation_id?` | `{transcript, lang, transcribe_seconds, answer}` |

`Finding`: `{id, kind, camera_id, camera_name, at, checked_at, watch_id, act, headline,
staff_message, detail (the models' structured output), evidence {frames, marked}, calls [{model,
role, seconds, error}], seconds, alert_id, task_id, metric, chain_seq}`. `metric` is what a switchback reads:
customers waiting, confirmed hazards, shelf gaps.

`StaffTask`: `{id, kind (shelf|hazard), finding_id, camera_id, where, items, marked, seen_at,
created_at, status, done_at, closed_by, minutes_to_fix, alert_id, role, proof_image, proof,
fixed_seq, detected_seq}`.

Evidence chain records (all of kind `advice`; the `type` labels are stable, proof packs for
inspectors and insurers look for them):

| `type` | Written when | Fields |
|---|---|---|
| `retail_finding` | staff are alerted (a detection) | `finding_id, kind, camera_id, at, watch_id, headline, staff_message, role, alert_id, task_id, models` |
| `hazard_cleared` | a hazard task closes | `detected_seq` (the seq of its `retail_finding` record), `task_id, kind, finding_id, camera_id, items, seen_at, fixed_at, minutes_to_fix, seconds_to_fix, closed_by` (`photo`, `camera` or `staff`) |
| `gap_refilled` | a shelf task closes | the same fields as `hazard_cleared` |

An accepted card writes a `measurement_plan` record (kind `measurement_plan`) before its watch
starts. A finding carries its `retail_finding` seq as `chain_seq`; a task carries
`detected_seq` and, once fixed, `fixed_seq`, so "seen at 10:02, cleared at 10:09 (7 min)" can be
shown with a Merkle proof of both records. Staff alerts go through the site's
outbound notifier: the alerts app (`/alerts`), plus Telegram or WhatsApp when configured.

Stored under `<data>/retail/`: `findings.jsonl`, `tasks.json` and `tasks.events.jsonl` (every
change; Ask reads its latest state as `retail_tasks`), `watches.json`, `cards.json`, `marked/`,
`frames/`, `uploads/`.

## Demo steps (about one minute, on the page `/usecases/supermarket`)

Before: warm the GPUs (`scripts/ops/warm.sh on`), pair or feed the checkout and aisle cameras,
approve a tracking plan for them ("Keep track of every customer, shopping cart and cashier at
the checkout lanes and tills"), and ask one question in advance so Ask has a receipt.

1. Step 1, click **Ask** on the first sample question (or press **Speak** and ask in Spanish).
   Point at the live steps; the answer is read aloud. Click **Verify in this browser**.
2. Step 2, **Turn the answer into advice**, then **Accept: pre-register and start watching**.
   Point at "Pre-registered on the evidence chain at record #N".
3. Step 3, on the checkout camera click **queue** (or **Run now** on the new watch): the marked
   photo and "Staff told: …" appear; the alert is on the staff phone (`/alerts`). Upload the
   floor photo: Spark finds the spill, the second model confirms, staff are sent.
4. Step 4, on the hazard or restock task, **Close with a photo**: the before/after verdict and
   "fixed in N min, chain record #M".
5. Step 5, **Write today's brief** in Spanish and **Read aloud**.

The same flow by API: `POST /ask` → `POST /retail/advice {receipt}` →
`POST /retail/advice/{id}/accept` → `POST /retail/watches/{watch_id}/run` →
`POST /retail/tasks/{id}/proof` → `POST /retail/brief {lang}`.

## Limits (honest)

- The live test footage is stock video looped as phone cameras, so "how long" and "how many
  per minute" questions describe repeats of the same people; the models say so.
- Muse Glimmer runs on a serverless GPU that scales from zero; a cold start takes minutes. Warm
  it before a demo. Without it, hazards fall back to SAM 3.1's confirmation and proofs to Spark
  alone, and each finding records which model answered.
- The switchback test needs someone to switch the change on and off by block and press ON or
  OFF on the card when a block ends; a real test takes the planned pairs of 30-minute blocks
  (hours), so in a one-minute demo the card honestly says "too early to tell".
