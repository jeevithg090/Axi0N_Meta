# Crowd early warning (festivals, stadium gates, stations)

Inquiry after inquiry (Hillsborough, Love Parade, Itaewon, Astroworld) found that warnings
existed but did not become action. Rulecam's crowd watch is built around the step after the
alert: a measured, checked warning with a reason, a named steward who has to answer, a calm
announcement in the crowd's languages, a route that is checked before anyone is sent down it,
and a record of each step.

The code measures; Meta's models judge. No density threshold is written into the code: Muse
Spark reads the measurements against the operator's own instruction and decides the level,
the forecast, the staff actions and the announcements.

## What happens, in order

1. **The operator writes one instruction**, in any language ("यह कैमरा गेट को ऊपर से देखता है;
   गेट के सामने भीड़ खतरनाक होने से पहले चेतावनी दें"). `POST /crowd/setup` shows Muse Spark 1.3
   the instruction and the camera's newest frame; Spark proposes named zones drawn on the frame,
   what SAM should count from this angle (heads from above, people from the side), the
   announcement languages, who is alerted first and who next, the venue places the instruction
   names, and what the floor calibration needs. The operator edits and starts it
   (`POST /crowd/watch`).
2. **The floor is calibrated**: from the operator's floor points (an overhead or fisheye view),
   else the active plan, else MoGe-2's metric point map with SAM's floor mask, else the crowd's
   own people. Every reading says which one it used and its error.
3. **Every few seconds, each watched camera is measured**: SAM 3.1 counts on tiles; per zone and
   per grid cell come people, square metres, density, speed, direction coherence, counterflow,
   crowd pressure (Helbing 2007) and the people crossing each zone's edge in and out per minute
   (optical flow). Each zone also gets a **projection**: the last minute's density line, the net
   inflow, and the seconds until each published reference density (2, 4, 5, 6 per m²) by the
   trend and by the fill clock (edge flow), plus how long it has already been above each.
4. **Muse Spark 1.3 assesses** with the instruction, the series, the projection, the venue layout
   with every camera's newest numbers, and the alerts still open (who was told, who answered,
   whether a steward was seen). It answers with the level, why (with numbers), when and where it
   becomes dangerous, staff actions tied to venue places, and the announcements.
5. **Route guard**: every action that moves people names its destination. The destination's
   camera numbers are read; if it is as dense as where people are coming from, or getting denser,
   Spark is asked again with those numbers before anything goes out. A destination no camera
   sees is marked unmeasured on the alert.
6. **Muse Glimmer checks the frames** (the newest frame, its heatmap and one from 30 s earlier)
   within 20 s. A Glimmer that does not see it holds the alert. A Glimmer with no warm worker is
   not waited for (a cold start outlasts the budget); its verdict is chained when it comes.
7. **Stewards are alerted with buttons** (*On my way*, *False alarm*, *Escalate*) through the
   site's notifier (phone app, Telegram, WhatsApp). Nobody tapping within `ack_within_s` sends it
   to the next role (`escalate_to`). After *On my way*, SAM 3.1 looks for the steward
   (`high-visibility vest`) in the alert's zone on each new reading for five minutes.
8. **Announcements are spoken** with Meta MMS-TTS into WAVs the page plays (voices are loaded
   when the watch starts, so the first alert does not wait for them).
9. **Everything is chained**: the candidate, Glimmer's verdict, each notification, escalation,
   tap and arrival, on the hash-chained evidence log.
10. **After the event**, `GET /crowd/report` gives the timeline, peaks, time above each reference
    density, latency per alert, the chain records and whether the chain verifies, the score
    against ground truth when it is registered, and (with `narrate=true`) Spark's after-action
    summary.

## Demo, step by step

Locally, against the live Meta models (`.env` with the Meta key; prototype mode on):

```bash
RULECAM_MODE=live RULECAM_PROTOTYPE_MODE=true RULECAM_CROWD_EVERY_S=5 make api
cd ui && npm run dev          # http://localhost:3000/usecases/crowd
```

1. Feed the Jülich gate clip as a phone camera, in real time, with its sample watch, the SAMPLE
   venue layout and the ground truth:

   ```bash
   uv run python scripts/crowd/feed_clip.py --clip small-030_c_56_h0.mp4 \
     --sample samples/crowd/juelich-030 --out /tmp/feed030 --label "Main gate" --setup
   ```

   The first chunk pairs the camera, `PUT /crowd/venue`, `PUT /crowd/truth/{camera}` and
   `POST /crowd/watch` follow, and `--setup` saves Spark's own proposal for the camera.
2. Open `/usecases/crowd`. The camera tab turns `critical` about 15 s after the first reading.
   Show the frame overlay (cells tinted by density, motion arrows, counterflow borders, the
   named zones), the zone cards (density, edge flow, the countdown), Spark's card (why, when,
   actions tied to places, the route guard), the venue map (Spark's targets in orange), and the
   alert (Glimmer's check or why there is none, latency, who was notified).
3. Press **play new announcements** before the alert, or play the Hindi and English WAVs.
4. Wait 45 s without tapping: the alert escalates from `stewards` to `safety_officer`. Then tap
   **On my way** as `safety_officer`: the escalation stops and SAM starts looking for the
   steward.
5. In **Set up a watch from plain words**, pick the camera and press **Ask Muse Spark to set it
   up** with a Hindi instruction: Spark draws its zones on the frame and fills the watch.
6. In **After the event**, press **Build report**, then **+ Muse Spark after-action**: peaks,
   time above each density, the latency table, the evidence seal, the timeline, and the live
   readings against the published trajectories.
7. Route guard with two cameras: `samples/crowd/two-gates` plays the Jülich queue run as side
   door B next to the gate run, with a venue that lets Spark send people to side door B. See
   `scripts/crowd/feed_clip.py --alias`.

## API

All routes are owner-only (loopback or an owner session) and answer 503 while crowd watch is off
(it needs `RULECAM_PROTOTYPE_MODE=true` and `RULECAM_CROWD_EVERY_S` > 0).

| Route | What it does |
|---|---|
| `POST /crowd/setup` | `{camera_id, instruction}` → Spark's proposal: `draft` (summary in the operator's language, view, concept, grid, languages, role, escalate_to, zones as fractions, places, calibration), `zones` in pixels, `frame` (a file name), `width`, `height`, `seconds`. 422 without recorded video; 502 when Spark fails. |
| `POST /crowd/watch` | Starts (or replaces) a watch. Body below. Returns the `Watch`. 422 for floor points that do not calibrate. |
| `DELETE /crowd/watch/{camera_id}` | `{stopped: bool}` |
| `GET /crowd/venue`, `PUT /crowd/venue` | The venue layout (body below). |
| `GET /crowd/status` | Everything the live page shows (shape below). |
| `GET /crowd/stream` | The same object as Server-Sent Events: `event: status` on every change, a `: keep-alive` comment every 15 s. `?limit=N` closes after N events. Sent with `Cache-Control: no-store, no-transform` so compressing proxies pass it through. |
| `GET /crowd/alerts?limit=50` | Alerts sent and held, newest first (`CrowdAlert`). |
| `POST /crowd/alerts/{alert_id}/ack` | `{role, button}` with `button` one of `On my way`, `False alarm`, `Escalate` → `AckState`. Same path as a tap in the phone app or Telegram: chained, stops or advances the escalation. 404 for an unknown alert. |
| `GET /crowd/files/{name}` | A heatmap or recorded frame (`.jpg`) or an announcement (`.wav`). |
| `PUT /crowd/truth/{camera_id}`, `GET /crowd/truth/{camera_id}` | Ground truth for a camera (body below). |
| `GET /crowd/report?camera_id=&since=&until=&narrate=` | The after-event report (shape below). |
| `POST /crowd/scan` | (site tools) Density spells over a past window, each watched on video by Spark. |

**Watch** (`POST /crowd/watch`):

```json
{
  "camera_id": "phone-2",
  "instruction": "गेट के सामने भीड़ खतरनाक होने से पहले चेतावनी दें …",
  "languages": ["hi", "en"],
  "role": "stewards",
  "escalate_to": ["safety_officer"],
  "ack_within_s": 45,
  "floor": {"image_px": [[667.7, 616.5], [665.4, 78.4], [245.4, 166.7], [260.5, 538.7]],
            "ground_m": [[-2, 0], [2, 0], [2, 4], [-2, 4]]},
  "zones": [{"name": "gate approach (2 x 2 m)",
             "polygon_px": [[667.2, 486.7], [666.0, 217.8], [419.3, 243.9], [424.9, 463.8]]}],
  "concept": "head",
  "grid": 1
}
```

**Venue** (`PUT /crowd/venue`): `description` (plain words), `sample` (true for demo layouts,
shown as SAMPLE), and `places`, each `{name, kind, leads_to: [names], camera_id, zone, note}`.
A place with a `camera_id` and `zone` is measured; the others are shown dashed and any route to
them is marked unmeasured.

**Status** (`GET /crowd/status`, each SSE event):

- `cameras[]`: `camera_id`, `name`, `watched` (the `Watch`), `latest` (the newest reading:
  `at`, `people`, `concept`, `calibration`, `calibration_err_m`, `zones[]` with `people`,
  `area_m2`, `density_ppm2`, `speed_mps`, `coherence`, `counterflow_share`, `pressure`,
  `in_per_min`, `out_per_min`; `cells[]` with `row`, `col`, `people`, `density_ppm2`, `flow_px`
  (image motion, px/s), `counterflow_share`, `pressure`; `heatmap` and `frame` file names),
  `series` (the whole view over ten minutes), `zone_series` (per zone: `t`, `people`,
  `density`, `speed`, `counterflow`), `forecast[]` (per zone: `density_ppm2`, `per_min`,
  `inflow_per_min`, `in_per_min`, `out_per_min`, `eta_s` and `fill_eta_s` keyed by reference
  density, `above_s`), `assessment` (Spark's: `level`, `zones`, `why`, `forecast`,
  `minutes_to_danger`, `alert`, `staff_actions`, `actions[]` with `do`, `target`, `role`,
  `why`, `to`, `announce[]`, `check`), `assessed_at`, `assess_seconds`, `last_alert`.
- `alerts[]` (`CrowdAlert`): `alert_id`, `camera_id`, `at` (the reading), `level`,
  `assessment`, `check` (Glimmer: `agrees`, `seen`, `confidence`) or `check_note`, `sent`,
  `held_reason`, `heatmap`, `audio[]` (`lang`, `text`, `audio` file), `chain_hash`, `notified`,
  `assessed_at`, `notified_at`, `sent_at`, `roles`, `route_checks[]` (`to`, `measured`,
  `density_ppm2`, `per_min`, `filling`, `note`, `replaced`).
- `acks{alert_id}` (`AckState`): `status` (`open`, `acknowledged`, `false_alarm`), `escalating`,
  `notified[]` (`role`, `at`, `status`), `taps[]`, `arrival` (`concept`, `zone`, `checks`,
  `seen_at`, `people`, `gave_up`).
- `venue`, and `places[]`: each place with `now` (people, density, speed) when a camera sees it.

**Truth** (`PUT /crowd/truth/{camera_id}`): `source`, `licence`, optional `clip_start_utc`
(default: the camera's first recorded chunk), `zones{name: {area_m2, t[], people[]}}` with `t`
in clip seconds, optional `view`. `scripts/crowd/juelich_truth.py` builds one from the Jülich
trajectories.

**Report** (`GET /crowd/report`): `readings`, `clip_start`, `series` per zone, `zones[]`
(`peak_density_ppm2`, `peak_at`, `peak_heatmap`, `mean_density_ppm2`, `above_s`),
`timeline[]` (`at`, `kind`: assessment, alert, held, announcement, notified, tap, peak; `level`,
`text`, `ref`), `latency[]` (`assess_s` and `alert_s` from the reading, `checked`), `evidence`
(`chain_ok`, `chain_files`, `records[]` with `seq`, `kind`, `hash`), `truth[]` (`count_ratio`,
`count_mae`, `density_ratio_median`, `sys_peak_ppm2`, `true_peak_ppm2`, and the series for the
chart), `after_action` (`headline`, `narrative`, `worked`, `change`).

## Files Ask reads

Under `<data>/crowd/`: `readings.jsonl` (one row per zone per reading, Ask's `crowd_readings`),
`assessments.jsonl` (`crowd_assessments`, with each route check and whether Spark re-chose),
`alerts.jsonl` (`crowd_alerts`), `acks.jsonl` (taps made on the page), `watches.json`,
`venue.json`, `truth/<camera>.json`, `audio/*.wav`.

## Settings

`RULECAM_CROWD_EVERY_S` (seconds between readings; 0 turns crowd watch off),
`RULECAM_CROWD_ASSESS_EVERY_S` (between Spark assessments of one camera, default 10),
`RULECAM_CROWD_CHECK_TIMEOUT_S` (Glimmer's budget, default 20), `RULECAM_CROWD_CONCEPT`,
`RULECAM_CROWD_GRID`, `RULECAM_CROWD_CAMERAS`, `RULECAM_CROWD_INSTRUCTION`,
`RULECAM_CROWD_LANGUAGES`.

## Limits

- Counts come from SAM 3.1 on what the camera sees; people hidden behind others at a shallow
  angle are missed. From above, 95–99% of the heads were found on the Jülich footage.
- Densities are only as good as the floor calibration; a zone reaching past the marked floor
  points is extrapolated (the corridor read 22% low on Jülich).
- The edge flow is optical flow over half a second, projected two seconds ahead; it is noisy in
  a crowd that barely moves.
- The steward check looks for a vest in the alert's zone; a steward standing just outside it,
  or wearing something else, is not seen.
- The demo venue layouts are SAMPLE data drawn over an experiment, not a real venue.
