# Rulecam: from 5/10 to 9.5/10

**Status on 31 Oct 2026:** Rulecam is deployed live: a Vercel UI, a RunPod API, and Glimmer on a RunPod GPU. It was tested end to end on real footage (a warehouse forklift clip and a kitchen clip). This plan fixes every problem those tests found and defines what 9.5/10 means, with numbers we can measure. Every workstream ends in merged PRs with green CI, deployed and checked live.

---

## 1. Where we are (evidence from the live tests)

| Area | Today | What we saw |
|---|---|---|
| Architecture and safety design | 8.5 | The evidence chain, approval gate, guardrails and camera scoping held up live. The gate refused rules it couldn't prove. |
| Engineering quality | 8 | About 2,300 tests and CI on every PR. 17 live bugs were found and fixed with regression tests. |
| Planner (request → rule) | 7 | Survey, 24-frame probes, pallet calibration and compile worked. The same request compiles to different rule forms. |
| Perception (SAM detections) | 7 | Hosted SAM was accurate: 24/24 frames, correct negatives. Self-hosted SAM 3.1 runs out of memory on an L4 (one video session needs about 20 GB). |
| Alert quality | **3** | 15 candidates, 0 confirmed. The forklift **driver** counted as a pedestrian. Some candidates fired at **4–6 m**. Glimmer answered "unsure" 13 times. |
| Real-time performance | **2** | Lag of 120–160 s. SAM calls take 5–24 s, but they **start minutes late**: there is a stall inside our runtime. |
| Cost efficiency | 4 | Hosted video SAM bills at least 50 frames per object per chunk. |
| Deployment and ops | 6 | Running and auto-restarting, but it took many manual fixes. Owner actions only work over SSH. |
| Demo readiness | 4 | The pipeline is proven end to end once, but there is no clean alert and no recorded replay. |
| **Overall** | **5** | |

### Root causes (from the code, not guesses)

1. **Glimmer is blind.** `runtime/factory.py` still wires `NoStrips` (a placeholder until P4-05), which attaches **no frames** to a candidate. Tier 1 therefore sees no pixels and answers "unsure". This is the main reason no candidate was confirmed.
2. **Policy and actions are placeholders.** The runtime uses `PlaceholderPolicy` (never acts first; alerts only on a Tier 1 violation) and placeholder actions that do nothing. The real `VerificationPolicy` + `SceneTextFirewall` (P3-05) and `ActionRunner` / escalation / interlock (P3-06) are merged but not wired in. No notifications went out in the tests; they never would have.
3. **No idea of an "operator".** The rule is distance(person, forklift) < 2 m. In the clip, the only person is the driver, at 0–0.8 m, so every frame is a "violation".
4. **Fires at 4–6 m.** One compiled form adds time-to-collision fields (`closest_in_s`, `ttc_s`, `separation_m`). Candidates fired with `closest_m` = 6.4 m against a 2 m threshold, so the predicate's truth value does not depend on the threshold in that form. This is a rules-engine or compile bug.
5. **The compiler isn't deterministic.** The same sentence produces `distance_m < 2`, `inside(buffer 2 m)` or a TTC form. Rule CI now covers distance and buffer, but the forms behave differently live.
6. **Runtime stall.** With 4 chunks in flight, SAM calls started minutes late, and end-to-end segment p50 was 136–160 s while individual calls took 5–24 s. The likely causes, in order:
   - (a) The ordered stage runs **Glimmer verification (~10 s per candidate) under the pipeline lock**, so every candidate blocks all bookkeeping (noted in #90).
   - (b) `asyncio.to_thread` shares the default executor (`min(32, cpu+4)` = 12 threads) between SAM concept workers, the pipeline, ffmpeg and Glimmer.
   - (c) Meta-side concurrency limits: per-call latency rose from 5 s to 38 s as more calls ran in parallel.
7. **Video SAM is the wrong tool for continuous monitoring.**
   - Hosted video bills at least 50 frames per call and takes 5–24 s.
   - Self-hosted SAM 3.1 video needs about 20 GB per 30-frame session.
   - The rules only need positions a few times a second, plus identity across frames.
8. **Product gaps:**
   - no owner login (owner actions need SSH);
   - `/evidence/{day}/chain` is public;
   - planner questions (steps 5 and 8) aren't served;
   - the worker QR page needs the UI public without exposing owner routes;
   - the site name and crew languages are defaults.

---

## 2. What 9.5/10 means (target scorecard)

Every row has a test that checks it on the live deployment or on the eval set (§3).

| Area | Target | How it's measured |
|---|---|---|
| Alert quality | **9.5** | On the labelled eval set: precision ≥ 0.92 and recall ≥ 0.90 per rule. **0 false alerts** on the negative set (driver-only, pedestrians far away, lookalikes). Glimmer gives a decisive verdict (not "unsure") on ≥ 85% of candidates. |
| Real-time | **9.5** | 2 cameras for 30 minutes: p95 lag ≤ 6 s; p95 alert latency (event start → verified alert on the UI) ≤ 10 s; keyframe mode never triggered. |
| Cost | **9** | ≤ $0.15 per camera-hour of model spend at idle, ≤ $0.60 when active. A budget alarm works. Deployment assumption: one 48 GB GPU pod per site (Glimmer + SAM image mode) shared by 4–8 cameras, so the GPU share is the pod's price ÷ cameras per GPU (`--cameras-per-gpu`, or `cameras_per_gpu` in the site config; $0.59/h ÷ 6 ≈ $0.10); the scorecard needs a soak showing one pod carries that many cameras (`gpu_cameras`). |
| Perception | **9.5** | Detection agreement with hosted SAM ≥ 0.95 on the eval set. ID switches ≤ 1 per minute per camera (TrackEval HOTA ≥ 0.6 on our clips). |
| Planner | **9.5** | The same request compiles to the **same canonical rule** 20 times out of 20. 100% of eval-set requests produce activatable rules where the scene allows, and honest refusals where it doesn't. |
| Safety and privacy | **9.5** | Owner login. No public route exposes candidate traces. Guardrail tests pass. The scene-text firewall is live. |
| Deployment and ops | **9.5** | One command brings up the whole stack from a clean RunPod account. It survives a pod restart. Health checks and metrics are on a dashboard. |
| Demo | **10** | A recorded golden REPLAY on the site that anyone can open, plus a scripted 3-minute live demo with your phone. |

---

## 3. Workstream 0: measure first (eval set + scorecard), about 0.5 day

Nothing can honestly be called 9.5 without numbers.

1. **Footage.** Record at least 12 short clips (30–90 s) on your phone, or with friends acting a staged scene:
   - **Forklift positives:** a pedestrian walks within 1 m, 1.5 m and 1.9 m of a *moving* forklift (or a toy forklift or cart standing in).
   - **Forklift negatives:**
     - driver only;
     - pedestrian at 3–6 m;
     - pedestrian near a *parked* forklift;
     - a pallet jack or cart lookalike.
   - **Handwash positives and negatives:** washing ≥ 20 s, washing 10 s, two short washes with a gap, nobody at the sink.
   - **Stock clips** where licensing allows (Mixkit, Pexels), as extra negatives.
2. **Labels:** event start and end per rule (temporal labels are enough), plus box tracks on 3 clips for tracking metrics. Tools: **CVAT** or **Label Studio** (self-hostable, export to JSON).
3. **Harness (`rulecam eval`):**
   - replays each clip as a camera through the real API (the same feeder we used);
   - matches alerts to labelled events (IoU of time ≥ 0.3);
   - reports precision, recall, alert latency, false alerts on negatives, and Glimmer decisiveness;
   - computes tracking metrics with **TrackEval** or **py-motmetrics**.

   Results go to `reports/eval/<date>.md` and a CI job running in replay mode.
4. **Scorecard:** a script prints §2's table from the eval report plus live metrics. Re-run it after every workstream.

---

## 4. Workstream 1: alert correctness (3 → 9.5), about 1.5 days

**1a. Give Glimmer eyes: the real strip renderer (P4-05). Highest impact.**
- For each candidate:
  - take 4–6 keyframes spanning the episode, with the masks or boxes of the rule's subjects drawn on;
  - add a top-down "distance ruler" overlay where calibration exists;
  - apply the privacy mode: faces blurred or skeleton-only per the plan (faces via an open-source detector, e.g. YuNet in OpenCV).
- Pass the strips into `Candidate.strip_paths`; Glimmer (llama.cpp + mmproj) already accepts images.
- **Done when:** on the eval set Glimmer gives a decisive verdict on ≥ 85% of candidates and its verdict matches the label on ≥ 90%.

**1b. Wire the real policy and actions (P3-05 / P3-06) into the runtime factory.**
- Replace `PlaceholderPolicy` with `VerificationPolicy` (HIGH acts first, LOW and MEDIUM verify first, disputes go to Tier 2). Replace the placeholder actions with `ActionRunner` (notify, escalate, interlock) and `EscalationManager` as the API's ticker.
- Turn on `SceneTextFirewall` (needs `HF_TOKEN` and the `ml` extra; run Prompt Guard 2 on the GPU pod).
- Because HIGH rules act first, 1c and 1d must land **before** HIGH actions are turned on live. Otherwise false positives become real notifications.

**1c. Operator vs pedestrian (the driver problem).**
- Add an operator-exclusion step to the rules engine. A person is that vehicle's operator if, over the window:
  - their box overlaps the vehicle's box (containment ≥ 0.5 of the person's box), **and**
  - their velocity matches the vehicle's (cosine similarity ≥ 0.9, speed within 30%).
- Represent it as a derived track attribute, so rules don't need new contract fields. Make it the default for any `distance_m` / `inside(buffer)` rule between a person and a vehicle concept (forklift, pallet jack, cart, vehicle). Owners can turn it off.
- Add Rule CI boundary traces for operator-like overlap: a riding person must **not** fire; a pedestrian at the same distance **must** fire.
- **Done when:** 0 alerts on the driver-only clips; recall unchanged on positives.

**1d. Fix the 4–6 m firing (TTC form).**
- Reproduce it from the saved chain traces: they contain `closest_m` 6.4 with `all` = True. Write a failing test first.
- Fix the predicate or compile mapping so the TTC form fires only when `closest_m < threshold` within the horizon (or the current distance is below it).
- Add the TTC form to Rule CI generators with boundary pairs, the same way buffer traces were added in #88.

**1e. Canonical rule forms (compiler determinism).**
- Canonicalize after compile: "keep X ≥ d m from moving Y" always becomes one form, `all(moving(Y), distance_m(X, Y) < d)` with operator exclusion. Buffer zones stay for drawn areas.
- Use reasoning-effort `low` and a fixed system prefix for compile. Then test it 20 times on the same request: 20/20 must give an identical canonical rule (hash).

**1f. Hard-negative memory.**
- Wire `append_regression` (P2-09) into dispute-upheld and owner-correction paths, so every confirmed false alarm becomes a permanent Rule CI regression.

---

## 5. Workstream 2: real-time perception (2 → 9.5), about 1.5 days

**2a. Find the stall with a profiler, not by reasoning.**
- Run **py-spy** (`py-spy dump --pid <uvicorn>` and `py-spy record -o stall.svg`) on the API pod during a live run.
- Add per-stage timestamps to each chunk: queued → upload start/end → SAM start/end → reorder wait → stitch → rules → verify → chain. Expose them in `/runtime/metrics`.
- Likely fixes, applied once the profile confirms:
  - move Tier 1 verification **out of** the ordered lock (record the candidate in order, verify asynchronously, append the verdict when it returns);
  - give SAM, ffmpeg and Glimmer their own bounded `ThreadPoolExecutor`s instead of the shared default executor;
  - cap hosted SAM concurrency to what Meta sustains (measure; probably 4–6), with a queue-time metric.
- **Done when:** segment p50 is within 1.5× the raw SAM latency.

**2b. Switch the runtime from video SAM to keyframes plus a tracker. The architecture fix.**
- Runtime perception becomes:
  - **SAM image mode on keyframes** (2–4 fps per camera), the `rulecam` prompts per concept;
  - an **open-source multi-object tracker** between and across keyframes, for stable identities and speeds;
  - the motion gate and static cache as before.
- **Tracker:** Roboflow **trackers** (ByteTrack / OC-SORT re-implementations, Apache-2.0), or **supervision** (MIT) with its ByteTrack wrapper.
- **Why:**
  - image calls are small (no 50-frame minimum);
  - self-hosted SAM 3.1 *image* mode fits easily on the L4 (the 20 GB problem was video sessions);
  - latency per keyframe is sub-second on a GPU;
  - rules only need positions and velocities at 2–4 Hz.
- Keep video SAM for **offline** use: probes, Rule CI scenes, and evidence re-checks of a candidate's window, where accuracy matters more than speed.
- **Fast path, optional:** a small Apache-2.0 detector (**RF-DETR** N/S, Apache-2.0 weights; or RT-DETR via `transformers`) for COCO `person` at 10+ fps. SAM is then called only for non-COCO concepts (forklift, sink, gloves). Never Ultralytics YOLO (AGPL, banned in this repo).
- **Done when:** 2 cameras for 30 minutes, p95 lag ≤ 6 s, keyframe mode never triggered, ID switches ≤ 1 per minute.

**2c. Self-hosted SAM that works.**
- Image mode on the L4: load the SAM 3.1 image predictor and serve `/segment_image` from the same server (same key, same health and memory guard).
- Video mode, offline only: 10-frame windows, `max_num_objects` 4–8, or a 48 GB GPU (A6000 / L40S) when available. Keep a soak test in CI (fake) and on the pod (`scripts/sam_pod/soak.py`).
- Tune the hotstart and new-detection thresholds with `scripts/sam_pod/compare.py` against hosted SAM; target agreement ≥ 0.95.
- **Fallback chain:** self-hosted image → hosted image → hosted video, each counted in metrics.

---

## 6. Workstream 3: cost (4 → 9), about 0.5 day (mostly falls out of WS2)

- Image keyframes plus a tracker instead of video sessions: about 10× fewer billed frames on hosted, $0 marginal on self-hosted.
- Make the existing per-camera budget an hourly **dollar** budget, with an alarm on the UI and a degrade path.
- A cost line per camera-hour on `/runtime/metrics` and the UI. Stop the GPU pods automatically when no camera has an active plan (RunPod REST API + idle timer).
- **One GPU per site:** Glimmer and SAM image mode on one 48 GB pod (`runpod_up.sh --one-gpu`, `ghcr.io/jeevithg090/rulecam-gpu`) instead of two pods ($1.18/h → about $0.59/h); the per-camera GPU share is the pod's price ÷ cameras per GPU, and the deployment assumes 4–8 (docs/deploy.md "One GPU per site").

---

## 7. Workstream 4: product and safety (6 → 9.5), about 1 day

1. **Owner login.** Passkey or emailed magic link (e.g. **Auth.js** on the Next.js side), with a signed, HTTP-only session cookie that FastAPI verifies. Owner routes accept loopback **or** a valid owner session, with CSRF protection. This removes the need for SSH.
2. **Close data exposure:** `/evidence/*` read routes need owner login. The public verify page only gets roots and hashes, not traces.
3. **Serve planner questions** (steps 5 and 8) through the API, show them in the planner UI, and feed the answers back into a re-run (frame-tap answers, known-distance marking for calibration).
4. **Site config UI:** site name, crew languages, roles, cameras, owner-drawn zones and click calibration. Written to `RULECAM_SITE_CONFIG`; no hardcoded "demo".
5. **Worker page:** UI served publicly with owner routes protected by login; real per-client rate limits behind the proxy.
6. **Alerts UI:** live alert cards with strips, the verdict and its reasoning, the evidence-chain link, and a dispute button.

---

## 8. Workstream 5: deployment and ops (6 → 9.5), about 0.5 day

- **One-command deploy:** `scripts/deploy/runpod_up.sh` creates or reuses the network volume and the three pods (API CPU, SAM GPU, Glimmer GPU) from templates. It writes the `.env` files and sets the Vercel env var, failing over to another data centre or GPU when stock runs out (we hit this three times).
- **Prebuilt images** instead of setup scripts: a Docker image for the API (uv-installed, ffmpeg) and one for SAM (torch cu128 + sam3 + deps), pushed to GHCR. No more ffmpeg 4.4, venv or missing-dependency surprises (bugs #68, #85, #86, #95).
- **Monitoring:** `/metrics` in Prometheus format plus a tiny Grafana dashboard (or the UI's own metrics page) showing lag, cost, fallbacks, GPU memory and verdict mix. Alerts when lag exceeds 10 s or a pod is unhealthy.
- **Restart tests:** a CI job that runs the setup scripts in a container twice (idempotency) and simulates container-disk loss.

---

## 9. Workstream 6: demo (4 → 10), about 0.5 day, after WS1–WS2

1. **Golden runs:** for UC1 (handwash) and the forklift case, a `record` run on the eval clips → `rulecam golden verify` in CI → the site's REPLAY mode plays them. That costs nothing, gives the same result every time, and works when the pods are off.
2. **Live script (3 minutes):**
   1. type the rule;
   2. the planner shows the survey, probes, calibration and Rule CI;
   3. approve;
   4. your phone at a sink and a cart acting as the forklift;
   5. a verified alert with strips appears on the UI;
   6. open the evidence chain and verify page;
   7. dispute a deliberate false alarm and show it become a regression test.
3. **Rehearsal:** 3 full dry runs on the deployed stack. Each must meet the §2 latency targets.

---

## 10. Order and parallelism

```
Day 1   WS0 eval set + harness ─┬─ WS2a profile and fix the stall
                                └─ WS1a strip renderer
Day 2   WS1c operator exclusion ─┬─ WS1d TTC fix ─┬─ WS2b keyframes + tracker
        WS1e canonical forms ────┘                └─ WS2c self-hosted SAM image mode
Day 3   WS1b real policy/actions (after 1c, 1d) ─┬─ WS4 owner login, evidence, questions
        WS3 cost                                 └─ WS5 images and deploy script
Day 4   Re-run eval → scorecard → fix gaps → WS6 golden runs + rehearsal
```

Each item is one PR (or a few small ones). Agents work in their own worktrees, merge only on green CI, and every merge is deployed and checked live with the eval harness. The scorecard is re-run at the end of each day.

---

## 11. Open-source building blocks (check each LICENSE before adding)

| Need | Option | License (as published) | Notes |
|---|---|---|---|
| Multi-object tracking | [roboflow/trackers](https://github.com/roboflow/trackers) (ByteTrack, OC-SORT) | Apache-2.0 | Clean-room re-implementations. |
| Detection utilities and annotation | [roboflow/supervision](https://github.com/roboflow/supervision) | MIT | Its ByteTrack wrapper is deprecated in favour of `trackers`. |
| Fast person detector (optional) | [roboflow/rf-detr](https://github.com/roboflow/rf-detr) N/S/M/L | Apache-2.0 | XL/2XL and `rfdetr_plus` are PML 1.0: **don't use those.** |
| Alternative detector | RT-DETR via Hugging Face `transformers` | Apache-2.0 | |
| **Banned** | Ultralytics YOLO, YOLO-World | AGPL / GPL | Repo rule: never imported at runtime. |
| SAM image/video | `facebook/sam3.1` + `sam3` (pinned) | SAM license (gated) | Already integrated; add image mode. |
| SAM 2 streaming (fallback idea) | [facebookresearch/sam2](https://github.com/facebookresearch/segment-anything-2) | Apache-2.0 / BSD-3 | Smaller "tiny" models with streaming memory. Check any real-time fork's own license. |
| Face blur in strips | OpenCV YuNet face detector | Apache-2.0 (OpenCV) | No identity, blur only. |
| Profiling | py-spy | MIT | Attaches to the live process without restarting it. |
| Labelling | CVAT / Label Studio | MIT / Apache-2.0 | For eval-set ground truth. |
| Tracking metrics | TrackEval / py-motmetrics | MIT | HOTA, IDF1, ID switches. |
| Auth | Auth.js (NextAuth) | ISC | Passkeys / magic links. |
| Monitoring | Prometheus client + Grafana | Apache-2.0 / AGPL (Grafana server) | Or use the UI's own metrics page to avoid AGPL hosting concerns. |

---

## 12. Risks and decisions

| Risk | Mitigation |
|---|---|
| SAM image mode alone gives jittery positions | The tracker smooths them; if it isn't enough, use 4 fps keyframes plus the RF-DETR fast path for people. |
| Glimmer still indecisive with strips | Tune the prompt with eval-set examples; use Spark Open Tier 2 (needs a **Baseten key**). |
| Operator heuristic misses edge cases (a pedestrian walking alongside a forklift) | Rule CI traces cover both cases, the threshold is tuned on the eval set, and the owner can override per rule. |
| GPU stock (we hit it three times) | The deploy script tries several data centres and GPU types; L4 is enough for SAM image mode. |
| Hosted Meta API limits | Image mode cuts volume about 10×; concurrency is capped and measured. |
| Eval set too small to trust 9.5 | At least 12 clips × 2 use cases now; add each disputed false alarm (WS1f). |

**Decisions needed from you:**
1. Record the eval clips yourself, or with helpers.
2. A Baseten key for Tier 2.
3. Whether to keep a 48 GB GPU for offline video SAM, or go image-mode only.
4. Login method: passkey or magic link.

---

## 13. Definition of done (9.5/10)

- [ ] The §2 scorecard meets every target, generated by the harness from the eval set and a 30-minute live run.
- [ ] Every workstream's PRs are merged with green CI and deployed. `docs/deploy.md` lets a teammate rebuild the stack from scratch.
- [ ] The golden REPLAY demo is live on the site, and the 3-minute live demo has been rehearsed 3 times.
- [ ] No open security items: owner login, evidence routes protected, rotated keys, no secrets in the repo or chat logs.
