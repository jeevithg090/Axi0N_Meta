# Surprise Lens: a JEPA world model inside Rulecam

*Use-case brief for the Rulecam demo. It covers the warehouse scenario, the Meta models involved, how the "surprise" signal is computed, the SAM-guided tokenization experiment, the demo script, how we measure it, and the build plan for Oct 29 – Nov 4 2026.*

> **Rules for the things you can name. A world model for the things you can't.**

---

## 1. The idea in plain words

Rulecam watches for rules people write, such as "nobody within 2 m of a moving forklift". But the worst incidents are often things nobody thought to write a rule for: a box sliding off a high shelf, a pallet stack starting to lean, a forklift reversing too fast, a person tripping in an aisle.

Surprise Lens adds a second kind of watching. It uses **V-JEPA 2**, Meta's open video "world model", which has learned from about a million hours of video how the world normally moves. It watches the camera, keeps predicting what the next second should look like, and compares that prediction with what actually happens. When reality is very different from the prediction, it raises a **surprise event**.

A surprise is not yet an alert. It goes through Rulecam's usual checks:

1. Glimmer, and Spark Open if needed, looks at the clip and says what happened in words.
2. Rulecam **suggests a rule** that would catch it next time.
3. A person approves the rule. It is tested by Rule CI (generated test scenes) and becomes a normal, enforced Rulecam rule.

So the system **learns new rules from the unexpected**. The manager doesn't have to know in advance everything that can go wrong.

To keep this cheap, we use **SAM 3.1 masks to decide which parts of the picture the world model looks at**. A fixed camera's picture is mostly empty floor that never changes. Feeding the model only the regions with people, vehicles, loads and motion is a form of **adaptive pixel tokenization**. That is the research angle of this use case.

---

## 2. The market problem it answers

| Problem | Evidence | What Surprise Lens does |
|---|---|---|
| Warehouses are dangerous | Warehousing and storage recorded **4.8 injuries per 100 workers** in 2024, more than double the 2.3 private-industry average ([BLS NAICS 493](https://www.bls.gov/iif/tgs/iag493.htm)). OSHA runs a National Emphasis Program for warehouses ([Safety+Health](https://safetyandhealthmagazine.com/articles/24197-OSHA-launches-National-Emphasis-Program-on-warehouse-safety)) | Watches the aisles continuously, including for events nobody configured |
| Forklifts and falling loads kill people | Forklifts killed **84 workers in 2024** ([NSC](https://injuryfacts.nsc.org/work/safety-topics/forklifts/)). There were **5,186 forklift severe-injury reports** in 2015–2024 ([AIHA](https://www.aiha.org/news/260514-osha-releases-severe-occupational-injury-report-statistics-for-2024)). Of 74 forklift deaths in 2017, 12 were people struck by a falling object ([BLS](https://www.bls.gov/iif/factsheets/fatal-occupational-injuries-forklifts-2017.htm)) | Flags loads slipping, stacks leaning and erratic vehicle motion, then turns them into rules |
| Rule-based systems only catch what was configured | Every competitor (Voxel, Protex, Intenseye, Verkada, Spot AI) alerts on predefined categories or user-written prompts. Reviewers complain about constant tuning ([G2 Intenseye](https://www.g2.com/products/intenseye/reviews)) | Covers the gap between the rules you have and the incidents you'll get, and proposes the missing rules |
| Near-misses go unrecorded | An analysis of 25,000 establishments found lower-severity incidents predict later fatalities ([Yorio & Moore, Risk Analysis 2018](https://ideas.repec.org/a/wly/riskan/v38y2018i4p839-852.html)) | Every surprise is logged as a near-miss candidate with a clip, so the near-miss record fills itself |
| Small sites have no safety staff | Firms under 20 employees account for about half of construction deaths. OSHA can inspect each workplace about once every 191 years | Needs no expert to anticipate hazards. The system notices them and drafts the rule |

**Persona.** Priya runs a 12-person warehouse with four existing cameras. She says: *"I don't know what rules to write. Just tell me when something weird happens, and help me stop it happening again."* Today no product serves her. Rulecam can.

---

## 3. Why JEPA, and why now

- **JEPA predicts meaning, not pixels.** V-JEPA predicts the *representation* of the hidden or future part of a video, not every pixel. It doesn't waste effort on leaf flicker or sensor noise, so its prediction error tracks "something structurally unexpected happened". That is exactly what we want.
- **The surprise signal is Meta's own method.** In *Intuitive physics understanding emerges from self-supervised pretraining on natural videos* (Meta FAIR, 2025), V-JEPA's prediction error ("surprise") separated physically possible videos from impossible ones. Reported accuracy on IntPhys was about **98%**, while pixel-prediction models and multimodal LLMs scored near chance ([alphaXiv 2502.11831](https://www.alphaxiv.org/overview/2502.11831)). We move that signal from a lab benchmark to a live camera.
- **V-JEPA 2 is open and ready to use.** Released June 2025 and pretrained on more than 1 million hours of video. Checkpoints from ViT-L (300M) to ViT-g (about 1B) are on Hugging Face, mostly under the MIT licence. The Hugging Face implementation **includes the predictor** and accepts `context_mask` and `target_mask` ([HF docs](https://huggingface.co/docs/transformers/model_doc/vjepa2); [Meta blog](https://ai.meta.com/blog/v-jepa-2-world-model-benchmarks/)).
- **We use it where it is strongest.** Meta's stated weaknesses for V-JEPA 2 are long-horizon planning and sensitivity to camera placement in the robot-control variant ([The Decoder](https://the-decoder.com/metas-latest-model-highlights-the-challenge-ai-faces-in-long-term-planning-and-causal-reasoning/); [InfoQ](https://www.infoq.com/news/2025/06/meta-vjepa2)). We only predict about one second ahead, from a camera that never moves.
- **Tokenization is on Meta's own roadmap.** V-JEPA 2.1 (March 2026) introduced separate tokenizers for images and video and a dense predictive loss ([arXiv 2603.14482](https://arxiv.org/abs/2603.14482)). Showing a SAM-guided tokenizer for fixed cameras speaks directly to where the field is going.

---

## 4. How it works

### 4.1 The surprise score, with no peeking

The model watches a sliding window of 16 frames from one camera. The first 12 frames are "the past" and the last 4 are "the future".

1. **Encode the past on its own.** The past frames go through the V-JEPA encoder without the future frames. This is the critical detail. If the encoder sees the whole window, the past tokens attend to the future and the "prediction" quietly cheats.
2. **Predict the future.** The V-JEPA predictor takes the past tokens and the positions of the future tokens, and outputs what it expects the future representations to be.
3. **Encode the real future.** The actual window is encoded, and we take the representations at the future positions. The standard JEPA setup encodes targets from the full clip.
4. **Compare.** Per-token error is the L1 distance between predicted and actual future representations. Averaging it gives one surprise number per window, and its spatial layout gives a **surprise heatmap**.

Token arithmetic for the ViT-L model: 256 px crops cut into 16 px patches give a 16 × 16 grid, and tubelets span 2 frames. A 16-frame window is therefore 8 × 256 = **2,048 tokens**. That is 1,536 past tokens and 512 future tokens.

### 4.2 From a number to an event

| Step | What happens |
|---|---|
| **Learn "normal" per camera** | Run for a calm period (60 s in the demo, hours in a real deployment) and keep running statistics of the surprise score (median and median absolute deviation) |
| **Trigger** | Raise a candidate when the score's robust z-score passes a threshold for at least 2 consecutive windows. Persistence filters single-frame glitches |
| **Localize** | Upsample the per-token heatmap to the frame. Intersect it with SAM 3.1 masks to **name** what was surprising (*"the cardboard box at the top of shelf 2"*) |
| **Package** | A 6-frame strip with the heatmap overlay, SAM labels, the surprise curve and the z-score. This is the same format as a normal Rulecam candidate event |
| **Verify** | Tier 1 **Muse Glimmer** (local) answers: *what happened, is it a safety concern, confidence*. Tier 2 **Muse Spark Open** takes over when confidence is low, with its reasoning shown on screen |
| **Act** | **Notify only.** A surprise never triggers an interlock, because surprise is not the same as danger. It is logged as a near-miss candidate |
| **Close the loop** | **Muse Spark 1.3** drafts a rule in the Rule IR that would catch this event. A person approves it, **Muse Image + SAM 3.1** generate and validate Rule CI test scenes, and the rule goes live |

Example of a rule drafted from a surprise:

```yaml
# warehouse/box-in-aisle.rule.yaml  (suggested by Surprise Lens, approved by Priya)
id: box-in-aisle
source: { kind: surprise_event, event_id: "srp-0142",
          summary: "A box fell from the top of shelf 2 into aisle B" }
concepts:
  box:    { prompt: "cardboard box" }
  person: { prompt: "person" }
zones: { aisle_b: polygon@cam3 }
predicate:
  any:
    - inside: { object: box, zone: aisle_b }        # anything left in the aisle
    - stack_height: { object: box, near: shelf_2, gt: 3 }
temporal: { persist_s: 5 }
severity: medium
actions: [notify(role=floor_lead), log]
verify: { tier1: glimmer, tier2: spark-open-on-disagree }
tests: { generated: 10, passed: TBD }
```

### 4.3 SAM-guided tokenization (the research part)

A fixed camera repeats itself. Most of the 2,048 tokens in each window show the same floor, racks and walls as the previous window. We choose which **past tokens** the world model sees from three sources:

1. **Object tokens.** Tokens overlapping SAM 3.1 masks (slightly dilated) for the concepts the site cares about, such as person, forklift, box, pallet and load. Rulecam already computes these masks for its rules, so they cost nothing extra.
2. **Motion tokens.** Tokens where the frame difference is above a threshold. This catches things SAM wasn't asked about, such as a rolling bottle or swinging chain. Without this, the system would be blind to exactly the "unknown unknowns" it exists to catch.
3. **A sparse background grid.** For example every 4th token in each direction (about 6% of the frame), so the model keeps scene context and can notice a change in something "static", like a shelf tilting.

Everything else is dropped.

**Why a JEPA model tolerates this.** V-JEPA is trained by hiding large parts of the video and predicting them from the rest. Its encoder is built to work from a partial view, so a reduced token set is closer to its training conditions than it would be for an ordinary video classifier. This is an argument, not a result; we have to measure it.

**Two separate questions, measured separately:**

| Question | How we test it | Engineering note |
|---|---|---|
| **Quality:** does the surprise signal survive when the predictor only sees the selected tokens? | Pass the selected token IDs as `context_mask` to the predictor | Works with the Hugging Face implementation as-is |
| **Cost:** how much encoder compute do we save? | Drop tokens **before** the encoder | In Hugging Face, `context_mask` selects encoder *outputs*, so the encoder still processes the full frame. Real savings need input-level masking, as in Meta's original `facebookresearch/vjepa2` code (its encoder applies masks after patch embedding). Confirm on day 1 |

**Illustrative budget, to be replaced by measured numbers.** Suppose objects cover about 15% of the frame, motion adds about 5% and the background grid about 6%. That keeps roughly **400 of 1,536 past tokens (about 26%)**. The future (target) pass stays full-frame in v1, so whole-window savings will be smaller than the past-token saving. Making the target pass sparse is an open question we name, not one we solve.

**The control that makes the result credible.** We compare against **random token dropping at the same budget**. If SAM-guided selection beats random selection at an equal token count, the guidance matters and it isn't just "fewer tokens is fine".

### 4.4 Code sketch (to check against the HF source on day 1)

```python
import torch
from transformers import AutoModel, AutoVideoProcessor

REPO = "facebook/vjepa2-vitl-fpc64-256"
proc = AutoVideoProcessor.from_pretrained(REPO)
model = AutoModel.from_pretrained(REPO, torch_dtype=torch.float16).cuda().eval()

T, T_PAST, TUB, GRID = 16, 12, 2, 16
N_PAST = (T_PAST // TUB) * GRID * GRID  # 1,536
N_ALL = (T // TUB) * GRID * GRID  # 2,048


@torch.no_grad()
def surprise(frames, keep_ids=None):
    """frames: 16 frames from one fixed camera. keep_ids: past-token ids to show the predictor."""
    video = proc(frames, return_tensors="pt")["pixel_values_videos"].cuda().half()

    past = model(pixel_values_videos=video[:, :T_PAST], skip_predictor=True).last_hidden_state
    actual = model(pixel_values_videos=video, skip_predictor=True).last_hidden_state[:, N_PAST:]

    ctx_ids = torch.arange(N_PAST, device="cuda") if keep_ids is None else keep_ids
    tgt_ids = torch.arange(N_PAST, N_ALL, device="cuda")
    # predict_future wraps model.predictor; check its argument names in modeling_vjepa2.py
    predicted = predict_future(model, past, ctx_ids[None], tgt_ids[None])

    err = (predicted - actual).abs().mean(-1)  # per future token
    heat = err.view((T - T_PAST) // TUB, GRID, GRID)  # 2 x 16 x 16 heatmap
    return err.mean().item(), heat
```

**Watch out for cropping.** The default processor resizes the short edge and **center-crops to a square**, which cuts off the sides of a 16:9 CCTV frame. Letterbox the frame to a square, or run two overlapping square tiles, so the aisle edges are not lost.

---

## 5. Meta tools in this use case

| Meta tool | Job here |
|---|---|
| **V-JEPA 2** (Meta FAIR, open weights) | The world model: predicts the next second and produces the surprise score and heatmap |
| **SAM 3.1** | Object masks that choose the tokens, and names for what was surprising |
| **Muse Glimmer** (local) | Tier 1 explanation of the surprise clip, privately on site |
| **Muse Spark Open** (Baseten) | Tier 2 judgment with visible `reasoning_content` when Glimmer is unsure |
| **Muse Spark 1.3** | Drafts the new rule in Rule IR from the verified surprise |
| **Muse Image** | Generates Rule CI test scenes for the suggested rule from the real camera frame |
| **Muse Code** (stretch) | Writes a missing predicate (such as `stack_height`) with unit tests in its sandbox |

**Prize note.** Per the participant-guide notes, the "Best Build on Meta Open Weight Models" bonus specifically requires Muse Glimmer or Muse Spark Open. Rulecam already qualifies through its verifier, so V-JEPA 2 is *extra* Meta tooling. It strengthens the 25% "Use of Meta AI tooling" score ("surface capabilities that surprised even us") but does not by itself qualify for that bonus. Confirm in the hackathon FAQ.

---

## 6. The demo

### Set-up and props

- A small "warehouse corner": a shelf or bookcase with a stack of 4–5 cardboard boxes, a taped aisle on the floor, the **toy forklift** (RC car with a cardboard mast) and one teammate walking through.
- A camera on a fixed tripod. Never move it after the "learn normal" step.
- A soft mat for the fall scenario.
- A GPU on Modal running V-JEPA 2 ViT-L. Rulecam's normal pipeline runs alongside.
- **Replay mode:** every model response is cached from a real run so the live finals can't break.

### 30-second segment in the main 3-minute video

| Time | On screen | Voice-over |
|---|---|---|
| 0:00–0:05 | Warehouse corner. The Rule list says **"0 rules"**. A small banner reads *Surprise Lens: learning normal…* over a time-lapse of normal traffic | "Priya doesn't know what rules to write. So she writes none." |
| 0:05–0:12 | The forklift passes normally and the surprise curve stays flat. Then the top box slides off the stack into the aisle. **The curve spikes** and a heatmap lights up on the box, labelled by SAM: *cardboard box* | "The world model predicts the next second. This wasn't it." |
| 0:12–0:20 | The verifier panel shows Glimmer: *"A box fell from the top of the shelf into the aisle. Safety concern: trip and struck-by hazard. Confidence 0.86"*. A card follows: **Suggested rule: alert if any box is in aisle B for more than 5 s** | "It explains what happened, and proposes a rule to catch it next time." |
| 0:20–0:26 | Priya taps **Approve**. The YAML appears and **10 Rule CI scenes turn green**. Rule count is now 1 | "Approved, tested, live." |
| 0:26–0:30 | A side-by-side token counter: **Full frame: 1,536 tokens / SAM-guided: ~400 tokens**. Both surprise curves spike at the same moment | "And it only looked at a quarter of the picture to do it." |

*The token figures on screen must be the measured ones from Section 7, not these placeholders.*

### Extended version for the live finals (about 90 s, if a judge asks)

1. **Three different surprises:** a box falls, the forklift reverses fast, a teammate drops onto the mat. Each produces a different heatmap and explanation.
2. **A hard negative:** someone switches a light off and on, or waves at the camera. Show that the persistence filter and the verifier keep it from becoming an alert. Showing the system *not* alerting builds trust.
3. **Ask Rulecam:** *"What surprised you this week?"* The answer is a ranked list of surprise events with clips, all of them near-miss candidates.
4. **The research slide:** the quality-versus-tokens chart from Section 7, including the random-drop control.

---

## 7. How we measure it (the numbers for the video)

**Data we record ourselves (Oct 30 – Nov 1):**
- **20 minutes of "normal"** from two fixed camera angles: people walking, forklift driving normal routes, boxes placed and picked normally.
- **30 staged events**, about 6 of each type: box falls, stack topples, forklift swerves or reverses fast, person falls onto the mat, object rolls into the aisle.
- **15 hard negatives:** light switch, a door opening, someone waving, a jacket thrown onto a chair, sun or shadow changes if near a window.

**Conditions compared:**

| Condition | Past tokens seen by the predictor |
|---|---|
| A. Full frame (baseline) | All 1,536 |
| B. SAM-guided (objects + motion + background grid) | Measured, about 25% |
| C. Random drop at the same budget as B | Same count as B, chosen randomly (the control) |
| D. Motion-only | Only tokens with frame difference |
| E. Simple baseline without JEPA | Pixel frame-difference energy with the same threshold logic |

**Metrics:**
- **Recall at a fixed false-alarm budget:** the share of staged events caught while allowing at most 1 false alarm per hour of normal video.
- **AUROC** of the surprise score for event windows against normal windows.
- **Detection delay:** seconds from the event starting to the alert.
- **Tokens kept** and **GPU milliseconds per window** for the encoder and predictor.
- **Verifier agreement:** the share of surprise candidates that Glimmer or Spark Open correctly describes, judged by two teammates.
- **Repeatability:** the full pipeline (SAM, surprise, verifier) re-run 5 times on the fixed clip set, reporting mean and spread. The hosted models are not perfectly deterministic.

**What counts as success:** B keeps at least 90% of A's recall at the same false-alarm budget, B clearly beats C, and A beats E. If B loses to C, we say so honestly and show full-frame Surprise Lens only.

---

## 8. What we will and won't claim

**We can claim:**
- "Rulecam turns a world model's surprise into a verified, human-approved rule." The loop from surprise to explanation to suggested rule to Rule CI is ours.
- "On our fixed-camera test set, SAM-guided token selection kept X% of event-detection recall using Y% of the tokens, and beat random selection at the same budget." Only with the measured numbers.
- "Built on Meta's V-JEPA 2, SAM 3.1, Glimmer, Spark Open, Spark 1.3 and Muse Image."

**We will not claim:**
- "We solved pixel tokenization." We show one narrow, measured result for one setting (fixed cameras).
- "It predicts accidents." It flags unexpected motion. A person decides what matters.
- "First to use object-guided tokens." Closest prior work: ObjectViViT uses object boxes to keep foreground tokens and downsample background ones ([arXiv 2306.11726](https://www.arxiv.org/pdf/2306.11726)); Fast SAM2 prunes tokens inside SAM 2 ([arXiv 2512.21333](https://arxiv.org/abs/2512.21333v1)); StructSAM merges tokens in SAM's encoder ([arXiv 2603.07307](https://arxiv.org/html/2603.07307v1)). In a quick search we did not find SAM masks used to select tokens for a JEPA world model's surprise signal, but the search was not exhaustive.
- Anything about V-JEPA "understanding physics" beyond what Meta's paper shows. Follow-up work questions how surprise comparisons are evaluated ([arXiv 2512.06232](https://arxiv.org/pdf/2512.06232); [NeurIPS 2026 localized VoE](https://neurips.cc/virtual/2026/165762)). That is why we report our own measured numbers.

---

## 9. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Surprise spikes on harmless things (lights, people waving, a door) | Robust z-score per camera, a persistence filter, the verifier, notify-only actions, and hard negatives in the eval set |
| Surprise doesn't spike on our staged events | Go/no-go test on **Oct 30**. Try a larger model (ViT-H), a different frame rate (4–8 fps), or a shorter future (1 tubelet) before giving up |
| Prediction "cheats" because the encoder saw the future | Encode the past separately (Section 4.1). Unit-test it: identical past with a different future must give the same prediction |
| Hugging Face masks don't save compute | Quality test uses Hugging Face. The compute test uses the original `vjepa2` repo's input masking. If neither works by Oct 31, report quality only |
| Center-crop cuts off the scene edges | Letterbox to a square, or use two overlapping tiles |
| GPU latency too high for live use | ViT-L in fp16 on a Modal GPU, a window every 0.5–1 s. The demo runs near real time plus replay mode. Report the measured latency, never a guess |
| Camera moves after calibration | Tripod. Re-learn "normal" after any move. Surprise would spike on a moved camera, which is also a useful alert in itself |
| Privacy | Surprise runs on raw frames in memory on the site machine. Anything shown off-site goes through Anonymous Mode (skeletons) as in the rest of Rulecam |
| Scope creep threatening the core Rulecam demo | One owner, a hard cut-line on **Oct 31**. If it fails, keep a 10-second "research preview" slide with whatever we measured, or drop it |
| Licence | V-JEPA 2 is mostly MIT. Check the model card for the exact checkpoint and list it in `WHAT-WE-BUILT.md` |

---

## 10. Build plan for one owner (Oct 29 – Nov 4)

| Day | Work | Done when |
|---|---|---|
| **Thu Oct 29** (today) | Modal GPU environment; load `facebook/vjepa2-vitl-fpc64-256`; run the encoder on a sample clip; implement the no-peek surprise with a unit test; record a 2-minute test clip with 3 staged events | A surprise curve plotted for the test clip |
| **Fri Oct 30** | **Go/no-go 1:** do the spikes line up with the events? Tune frame rate, window and threshold. Add the heatmap and SAM labelling | Spikes on at least 2 of 3 events, with no spike on a light switch |
| **Sat Oct 31** | SAM-guided token selection (objects + motion + grid); random-drop control; try input-level masking in the original repo. **Go/no-go 2 (Rulecam cut-line day)** | Condition B's curve looks like A's on the test clip |
| **Sun Nov 1** | Wire into Rulecam: surprise candidate → Glimmer/Spark Open → Spark 1.3 rule suggestion → approval → Rule CI. Record the full eval set (20 min normal, 30 events, 15 hard negatives) | One end-to-end surprise becomes an approved rule in the UI |
| **Mon Nov 2** | Run the eval (conditions A–E, 5 repeats); make the quality-versus-tokens chart; rehearse the 30-s segment; cache responses for replay mode | Chart and numbers final; segment rehearsed 3 times |
| **Tue Nov 3** | Record the segment from a real run; add the final numbers to the video and README | Segment cut into the main video |
| **Wed Nov 4** | Buffer, README section, `WHAT-WE-BUILT.md` entries, submit early | Submitted |

---

## 11. Glossary

| Term | Meaning |
|---|---|
| **World model** | A model that has learned how the world usually changes, so it can predict what comes next |
| **JEPA** | Joint-Embedding Predictive Architecture: Meta's approach of predicting the *representation* of missing or future content instead of its pixels |
| **V-JEPA 2** | Meta's open video JEPA model (2025), trained on more than 1 million hours of video |
| **Token** | One small piece of the input the model processes. For V-JEPA 2, a 16 × 16-pixel patch across 2 frames |
| **Tokenization** | Deciding how pixels are cut into tokens. Adaptive tokenization means spending tokens where the information is |
| **Surprise** | How far reality differs from the model's prediction. High surprise means something unexpected happened |
| **Context / target** | The part of the video the model sees (context) and the part it must predict (target) |
| **Hard negative** | A harmless event that looks unusual, used to test that the system doesn't over-alert |
| **AUROC** | A score from 0.5 (guessing) to 1.0 (perfect) for how well a signal separates events from normal footage |

---

**Sources:** [Meta V-JEPA 2 blog](https://ai.meta.com/blog/v-jepa-2-world-model-benchmarks/) · [Hugging Face V-JEPA 2 docs](https://huggingface.co/docs/transformers/model_doc/vjepa2) · [V-JEPA 2.1, arXiv 2603.14482](https://arxiv.org/abs/2603.14482) · [Intuitive physics from V-JEPA, alphaXiv 2502.11831](https://www.alphaxiv.org/overview/2502.11831) · [Opinion: intuitive physics may need more than visual data, arXiv 2512.06232](https://arxiv.org/pdf/2512.06232) · [Localized VoE evaluation, NeurIPS 2026](https://neurips.cc/virtual/2026/165762) · [The Decoder on V-JEPA 2 limits](https://the-decoder.com/metas-latest-model-highlights-the-challenge-ai-faces-in-long-term-planning-and-causal-reasoning/) · [InfoQ on V-JEPA 2](https://www.infoq.com/news/2025/06/meta-vjepa2) · [ObjectViViT, arXiv 2306.11726](https://www.arxiv.org/pdf/2306.11726) · [Fast SAM2, arXiv 2512.21333](https://arxiv.org/abs/2512.21333v1) · [StructSAM, arXiv 2603.07307](https://arxiv.org/html/2603.07307v1) · Warehouse statistics as cited in Section 2, from `research_notes/Rulecam expansion/multidomain_safety_stats.md`.
