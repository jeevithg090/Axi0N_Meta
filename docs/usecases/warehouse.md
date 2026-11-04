# Warehouse forklift safety

A warehouse owner says, in their own language, "keep people 4 metres from a moving forklift".
Rulecam plans the rule, says honestly what the camera cannot check, proves the rule with Rule CI,
waits for a person to approve it, and then watches. When someone gets too close, Muse Glimmer
looks at the frames before anyone is bothered; the driver sitting on the forklift is never
counted as a pedestrian. A confirmed near miss sends a spoken warning in the crew's language
and a phone alert with buttons, lands on a hash-chained evidence log, makes the operator's
refresher training due (the US forklift rule requires it after any near miss), and becomes a
two-minute toolbox talk with a drawing of the safe behaviour. A worker who thinks an alert was
wrong disputes it from the QR page, and a wrong alert becomes a permanent Rule CI regression.

Everything on the page `/usecases/warehouse` is read live from the API: there is no sample data
on it.

## The demo, step by step

The page is `/usecases/warehouse` (left column: the request and the story; right column: the
incident, the near-miss log and heatmap, voice reports, the shift report). Feed a camera first:
pair a phone (the "Pair a phone" button, or `/pair`), or play a clip in with
`rulecam eval run --clip pexels_32243654 --rule forklift_4m_es --include-unlabelled --survey-s 20`.

1. **Say the rule in Spanish.** Click *Español: 4 m de una carretilla* and *Plan this rule*, or
   pick *Español* and click *Say it instead*, speak, then *Stop and send* (Muse Voice
   Transcribe writes it down). The story shows the request and the plan id.
2. **The plan says what it cannot check.** The page shows each planner step as it finishes,
   with its time ("survey 0.0 s: reused phone-8's survey from plan-… (110 s old, same scene)",
   "probe 18.4 s", …). When the plan is ready, the "planner" step lists the rule and the
   caveats in the planner's own words (for example "calibration error 0.15 m > 0.10 m"), shown
   under *What the planner says it cannot fully check*. **Before going on stage, plan once on
   the stage camera** (any rule): the survey of that scene is then reused for 30 minutes and
   the on-stage plan skips it; keep the Glimmer GPU warm (`scripts/ops/demo_warm.sh`), since
   the audit step's back-translation waits for it.
3. **Rule CI.** The next step shows the rule's Rule CI result, e.g. "boundary 16/16, mutations
   6/6". The full report is on `/ci`.
4. **Approve.** Click *Approve this plan*. Nothing runs before this.
5. **A live near miss.** A person passes the moving forklift inside 4 m. The incident card shows
   the measured gap, the closest approach in the next second, the forklift's speed and how the
   camera was calibrated. The near-miss log and heatmap fill in.
6. **Glimmer's verdict, driver excluded.** The verdict line shows Muse Glimmer's answer and its
   reason. The seated driver is the forklift's operator and never a candidate; on the driver-only
   clip (T04) Glimmer rejects what the engine raised.
7. **Spanish warning and phone alert.** Click *Spoken warning*: Muse Spark writes the warning in
   Spanish for this incident, it is spoken (Meta MMS-TTS when installed, else the system voice,
   labelled), and Muse Voice Transcribe listens back and gives the word error rate. Play it in
   the page. Tick *and English* for a bilingual warning ("¡Montacargas…! Forklift…!"): each
   language is spoken and checked by ear on its own. *Send voice note* sends it to a role's
   phone as a WhatsApp voice note (needs `WHATSAPP_TOKEN` and `WHATSAPP_RECIPIENTS`, and the
   role must have messaged the business number in the last 24 hours). With actions live (`RULECAM_ACTIONS_DRY_RUN=false`), the phone alert appears with
   *On my way*, *False alarm* and *Escalate*; *On my way* acknowledges it on the chain.
8. **Evidence.** *Incident report* opens the one-page report with every chained record of the
   event; *Verify the chain in your browser* recomputes every hash in the browser.
9. **Refresher training due.** Click *Training duty (OSHA)*: Muse Spark looks up 29 CFR
   1910.178(l)(4)(ii)(B) with web search (the page says whether the cited page is one the
   search returned), reads the incident and its frames, says whether the clause applies, what
   the refresher covers and when it is due, and reads the forklift's painted unit number if it
   is legible. *Mark training done* chains a `label` record on the near miss.
10. **Toolbox talk.** Click *Toolbox talk*: a two-minute talk in Spanish written from the
    records and the evidence frames, with a Muse Image drawing of the safe behaviour.
11. **Worker dispute.** *Worker page (QR): rules and disputes* opens the anonymous worker page;
    a worker taps an alert and disputes it (tap or voice). Muse Spark 1.3 re-reviews it with the
    evidence frames and upholds or rejects it (live on 4 Nov: upheld in 28 s; that dispute
    exposed a strip-ruler bug, since fixed).
12. **Permanent regression.** An upheld dispute, or the owner's *False alarm* tap on the phone
    alert, adds the alert to the rule's permanent Rule CI regressions; the story's last step
    turns done.

Two more panels: **A worker reports a near miss by voice** (speak in any language; Spark
matches it to what the cameras measured in the last 30 minutes, chains the match, and proposes a
rule; *Plan and prove this rule* sends it to the planner and Rule CI), and **Shift safety
report** (Spark writes it from the day's counted chain facts and keeps the chain root).

If a live step stalls, the golden REPLAY at `/demo?demo=1` has the same forklift story recorded
(`worker_passes_4m`: verified alert; `driver_only_4m`: driver rejected).

## Planning speed

Request to plan took 291–424 s on 4 Nov before the changes below (reports/eval/2026-11-04-
warehouse-live.md has the before and after). What changed, with every check kept:

- the survey's SAM calls (up to 30 things on 8 frames) all run at once, up to 32 in flight,
  instead of 8 at a time; each probe's 24 frames run at once, two probes at a time;
- a SAM call that has not answered after 8 s gets a twin; the first answer wins;
- the geometry model's health check runs alongside the survey, and is waited on for 5 s at most;
- a camera's survey is reused for 30 minutes while its scene (median frame's perceptual hash)
  is unchanged (`PLANNER_SURVEY_REUSE_S`); the probe, the calibration check against the people
  seen now and Rule CI always run;
- compile's Spark call starts with ask's (the steps are still reported in order).

Rule CI itself takes 0.1–0.2 s (16 boundary traces, 6 mutants), so it was left as it is.

**Llama Guard 4 rule screen.** Right after the request is understood, the owner's words and the
concerns read from them go to Llama Guard 4 (`planner/screen.py`, about 0.4 s warm). If it flags
S7 Privacy or S10 Hate, every concern is declined with its reason, the survey and probes are
skipped and no rule is compiled. It runs only with `LLAMA_GUARD_BASE_URL` set; otherwise the
timeline shows "not screened: LLAMA_GUARD_BASE_URL is not set" (and the planner's own guardrails
still run). It appears on the page's timeline as the step `screen`, between understand and
survey.

## API contract

All routes are owner-only (loopback or an owner session), like `/incidents`. GET of a talk,
warning, duty or report returns the saved one (404 when there is none); POST calls the models
(503 when Spark is not configured, 502 when the call fails) and saves the result, so a client
whose proxy times out can read it back with GET. Models are in
`src/rulecam/warehouse/models.py`.

| Method and path | Body or query | Returns |
|---|---|---|
| `GET /warehouse/story` | `plan_id`, `day` (both optional) | `WarehouseStory`: plan id, request and language, rules, `cant_check`, the followed `event_id`, `steps` (`key`, `title`, `status` = done / waiting / not_yet / failed, `detail`, `refs`), the day's `near_misses`, and `planning` (each planner step's `status` and `seconds`, `total_s`). A plan still being planned has its own story (request, then its steps so far) |
| `GET /warehouse/near-misses` | `day`, `camera_id`, `plan_id` | `NearMiss[]`, newest first |
| `GET /warehouse/heatmap` | `camera_id`, `day`, `plan_id` | `Heatmap`: frame size, grid `cells`, `points`, `unplaced`, `background` |
| `GET /warehouse/heatmap/{camera_id}/frame` | `day` | a recorded JPEG of the camera |
| `POST /warehouse/incidents/{event_id}/warning` | `{"lang": "es"}`, optionally `"also": "en"` for a bilingual warning | `SpokenWarning`: `text`, `english`, `audio` (WAV name), `tts`, `heard`, `wer`, `roundtrip_ok`, and `parts` (each language spoken and checked on its own) |
| `POST /warehouse/incidents/{event_id}/voice-note` | `{"role": "supervisor", "lang": "es", "also": "en"}` | `VoiceNote`: `sent`, `message_id`, `detail`, `record_hash`. The saved warning as a WhatsApp voice note (Ogg/Opus, `voice: true`) to the role's number inside its 24-hour window; "dry-run: would send …" under `RULECAM_ACTIONS_DRY_RUN`; every attempt is an `action` record |
| `POST /warehouse/incidents/{event_id}/toolbox-talk` | `{"lang": "es", "illustrate": true}` | `ToolboxTalk`: `title`, `minutes`, `sections`, `key_points`, `questions`, `illustration` (PNG name), `incident_hash` |
| `POST /warehouse/incidents/{event_id}/duty` | none | `RefresherDuty`: `applies`, `why`, `clause` (citation, text, `source_url`, `source_retrieved`), `vehicle_unit`, `training_focus`, `due`, `status` |
| `POST /warehouse/incidents/{event_id}/duty/done` | `{"role": "owner"}` | the duty with `status: done` and `done_hash` (the chained `label` record) |
| `GET /warehouse/duties` | none | every duty, open ones first |
| `POST /warehouse/reports/voice` | raw audio body (WebM, Ogg, MP4 or WAV), `lang` query | `VoiceReport`: `transcript`, `summary_en`, `what_happened`, `matches_event_id`, `measured_m`, `proposed_rule`, `chained_hash` |
| `POST /warehouse/reports/{report_id}/propose` | none | the report with `plan_id` (the planner runs and Rule CI tests the rule) |
| `GET /warehouse/reports` | none | every report, newest first |
| `POST /warehouse/public/reports/voice` | raw audio body, `lang` query; **no login** (5 per client address and 200 per site a day) | `HeardReport`: `heard`, `what_happened`, `matched`, `measured_m` (no event id). The worker page's *Report a near miss by voice* button |
| `POST /warehouse/shift-report` | `{"day": "2026-11-04", "lang": "en"}` | `ShiftReport`: counted `counts`, `headline`, `summary`, `patterns`, `actions`, `chain_root` |
| `GET /warehouse/files/{name}` | none | a WAV or PNG made above |
| `GET /warehouse/incidents/{event_id}/warning`, `/toolbox-talk` | `lang` | the saved one |
| `GET /warehouse/incidents/{event_id}/duty`, `GET /warehouse/shift-report` | `day`, `lang` | the saved one |

A near miss, as the API returns it (T02, recorded 2 Nov 2026):

```json
{"event_id": "ev-49940b44a5ce03bffe26313f", "camera_id": "phone-97",
 "rule_id": "keep-people-4m-from-moving-forklift", "limit_m": 4.0, "separation_m": 3.991,
 "closest_m": 3.815, "ttc_s": null, "vehicle_speed_mps": 0.328,
 "calibration": "person_height_1.7m", "verdict": "violation", "verdict_score": 0.99,
 "verifier": "muse-glimmer-30b", "alert": true, "route": "auto",
 "actions": ["dry-run: would notify supervisor"], "acked_by": [], "disputes": [],
 "regression": false, "training_done": false, "foot_px": null}
```

The routes this flow also uses, unchanged: `POST /plans/requests`, `POST /plans/uploads`,
`POST /plans/{id}/approve`, `GET /alerts`, `POST /alerts/{id}/respond`, `GET /incidents/{id}/report`,
`/verify/`, `/worker/{plan}/{camera}`, `POST /worker/dispute`.

## Where each number comes from

- **Distances, closest approach, time to collision, speed**: the rules engine's own trace of the
  candidate (`rules/near_miss.py`), the same ground geometry the rule fired on. Nothing is
  re-measured or re-judged here, and the only distance is the owner's.
- **Positions on the heatmap**: the person's foot point at the firing time, from the track
  store. It counts events, never people.
- **Verdicts and decisions**: the evidence chain (`verdict` records).
- **Talks, warnings, duties, reports**: Muse Spark 1.3, schema-validated, saved as JSON under
  `<data>/warehouse/`. Text from frames, verdicts and transcripts is passed as data, never as
  instructions.
- **Shift counts**: counted from the chain records of the day.

## Meta models used here

| Model | What it does in this flow |
|---|---|
| Muse Spark 1.3 | Plans the rule; writes the spoken warning, the toolbox talk, the training duty and the shift report; looks up the OSHA clause with web search; matches a spoken report to measured near misses |
| Muse Glimmer 30B | Tier 1 verdict on the evidence strip before anyone is alerted |
| SAM 3.1 | Survey and probes, keyframe tracking, crop-zoom |
| Muse Voice Transcribe | Spoken requests and reports; plays the spoken warning back to check it |
| Muse Image | The toolbox talk's drawing of the safe behaviour (from words only: no worker's picture is sent) |
| MMS-TTS | Speaks the warning when `transformers` is installed (the system voice otherwise, labelled as not Meta's) |

## Privacy

No face, name or person is identified. A forklift is named by its painted unit number, read from
the frames only when legible. Voice reports keep the transcript, never the audio. The OSHA duty
names the vehicle; the operator is found through the fleet's own records, not by the camera.

## Limits (honest)

- Tier 2 (Spark Open) is not configured live. Worker disputes are reviewed by Muse Spark 1.3
  with up to four evidence frames instead (`worker/spark_review.py`,
  `RULECAM_DISPUTE_SPARK_FALLBACK`); if that call fails the dispute stays `pending` for a person.
- With actions live and no speaker or phrase book configured, the rule's own "play the warning"
  action fails; the warning played on the page is the Spark-written, MMS-TTS-spoken WAV.
- Latency on 4 Nov was dominated by the serverless SAM timing out (2 x 60 s) before hosted SAM
  took over: candidates reached the chain about 2 minutes after the event; Glimmer's verdict took
  12–22 s (reports/eval/2026-11-04-warehouse-live.md).
- The OSHA clause comes from Spark's web search; when the page it cites is not among the pages
  the search returned, a second Spark call picks one of the returned pages or the citation is
  shown as unverified.
- Speed is the vehicle's ground speed from a calibrated single camera (the calibration's stated
  error applies); there is no separate "speeding" rule unless the owner asks for one, in which
  case the planner compiles it (e.g. the *Speed near people* preset).
- The worker's spoken near-miss report is an owner route on this page; a public worker-page
  version would reuse the worker page's rate limits.
