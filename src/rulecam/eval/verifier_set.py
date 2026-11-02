"""A labelled set for the Tier 1 verifier: blind labelling sheet, scorer, strip variants (1.4).

The 1 Nov live test campaign produced 29 Glimmer verdicts (27 in run 2, 2 in run 1), each on an
evidence strip still on the API pod (`<data dir>/strips/<camera>/<event>/<k>.jpg`). How many of
them were right is unmeasured, so the ≥ 90 % Tier 1 target is too. This module:

1. **collects** every verdict from the campaign JSON files (`collect_items`);
2. **writes a blind sheet** (`write_sheet`): the strips copied under neutral ids in a shuffled
   order, a `labels.csv` for each labeller and an `index.html` viewer with the request and the
   frames only; Glimmer's verdict, score and reason go to a separate `key.json` the labellers
   must not open;
3. **scores** two labellers' CSVs against the key (`score_labels`): Glimmer's accuracy against
   each labeller and against their consensus (the items both labelled the same decisive way),
   Glimmer's and each labeller's `unsure` rate, raw agreement and Cohen's kappa between the two;
4. builds the **old strip format from the new one** (`downgrade_strip`): 6 of the 8 frames,
   scaled to 640 px, faces blurred, for the 6-blurred-at-640 vs 8-raw-at-960 replay
   (`scripts/eval/verifier_replay.py`).

Labels are about whether a frame shows a rule being broken; nothing here identifies anyone.
"""

from __future__ import annotations

import csv
import html
import json
import random
import re
import shutil
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

import cv2
import numpy as np
from numpy.typing import NDArray

from rulecam.contracts import CandidateEvent, Measurement, RuleIR
from rulecam.eval.errors import EvalError
from rulecam.stats.intervals import wilson_interval

Label = Literal["violation", "no_violation", "unsure"]
LABELS: tuple[Label, ...] = ("violation", "no_violation", "unsure")
DECISIVE: tuple[Label, ...] = ("violation", "no_violation")
SHEET_COLUMNS = ("item", "request", "label", "notes")
OLD_FRAMES = 6
OLD_WIDTH = 640
Image = NDArray[np.uint8]
_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")  # as `runtime.strips.safe_name`


@dataclass(frozen=True)
class VerdictItem:
    """One Glimmer verdict of the campaign, with what it was asked about."""

    run: str
    test: str
    event_id: str
    camera_id: str
    rule_id: str
    request: str
    rule: Mapping[str, object]
    ts_start: str
    ts_end: str
    measurement: Mapping[str, object] | None
    trace: Mapping[str, object]
    verdict: Label
    score: float
    reason: str
    item: str = ""  # the blind id, set by `blind`
    strips: tuple[str, ...] = field(default_factory=tuple)  # sheet-relative frame paths


def collect_items(runs: Sequence[tuple[str, Path]]) -> list[VerdictItem]:
    """Every verdict in campaign JSON files (`reports/eval/2026-11-01-live-test-campaign*.json`).

    Args:
        runs: `(run name, path)` pairs.

    Returns:
        One item per verified candidate, in file order.

    Raises:
        EvalError: If a file is unreadable or a test's rule is missing.
    """
    items = []
    for run, path in runs:
        try:
            tests = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            msg = f"cannot read campaign file {path}: {exc}"
            raise EvalError(msg) from exc
        for test in tests:
            rules = {str(r["id"]): r for r in test.get("rules") or []}
            for event in test.get("events") or []:
                verdict = event.get("verdict")
                if not verdict:
                    continue
                rule = rules.get(str(event["rule_id"]))
                if rule is None:
                    msg = f"{path}: {test['id']} has no rule {event['rule_id']!r}"
                    raise EvalError(msg)
                items.append(
                    VerdictItem(
                        run=run,
                        test=str(test["id"]),
                        event_id=str(event["event_id"]),
                        camera_id=str(test["camera_id"]),
                        rule_id=str(event["rule_id"]),
                        request=str(test["request"]),
                        rule=rule,
                        ts_start=str(event["ts_start"]),
                        ts_end=str(event["ts_end"]),
                        measurement=event.get("measurement"),
                        trace=event.get("trace") or {},
                        verdict=verdict["verdict"],
                        score=float(verdict["score"]),
                        reason=str(verdict.get("reason", "")),
                    )
                )
    return items


def blind(items: Sequence[VerdictItem], *, seed: int) -> list[VerdictItem]:
    """Shuffle the items (so tests are not grouped) and give them neutral ids `v01`, `v02`, …."""
    order = list(items)
    random.Random(seed).shuffle(order)  # noqa: S311 - an ordering, not a secret
    width = max(2, len(str(len(order))))
    return [
        VerdictItem(**{**asdict(item), "item": f"v{n:0{width}d}"})
        for n, item in enumerate(order, start=1)
    ]


def strip_dir(strips_root: Path, camera_id: str, event_id: str) -> Path:
    """Where the runtime wrote a candidate's strip (`runtime.strips.safe_name` on both parts)."""
    return strips_root / _safe(camera_id) / _safe(event_id)


def _safe(text: str) -> str:
    return (_UNSAFE.sub("_", text).strip(".") or "_")[:120]


def strip_frames(folder: Path) -> list[Path]:
    """The strip's frames in order (`1.jpg`, `2.jpg`, …)."""
    return sorted(folder.glob("*.jpg"), key=lambda p: (len(p.stem), p.stem))


def write_sheet(
    items: Sequence[VerdictItem], out: Path, strips_root: Path
) -> tuple[list[VerdictItem], list[str]]:
    """Write the blind labelling sheet for blinded `items`.

    Writes `out/strips/<item>/<k>.jpg`, `out/labels.csv` (labels empty), `out/index.html` (the
    viewer; nothing in it reveals Glimmer's answer) and `out/key.json` (the answers: keep it
    away from the labellers).

    Returns:
        The items with their sheet-relative frame paths, and the item ids whose strip was not
        found under `strips_root`.
    """
    out.mkdir(parents=True, exist_ok=True)
    done, missing = [], []
    for item in items:
        frames = strip_frames(strip_dir(strips_root, item.camera_id, item.event_id))
        if not frames:
            missing.append(item.item)
        target = out / "strips" / item.item
        target.mkdir(parents=True, exist_ok=True)
        copied = []
        for k, frame in enumerate(frames, start=1):
            shutil.copyfile(frame, target / f"{k}.jpg")
            copied.append(f"strips/{item.item}/{k}.jpg")
        done.append(VerdictItem(**{**asdict(item), "strips": tuple(copied)}))
    with (out / "labels.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(SHEET_COLUMNS)
        writer.writerows([i.item, i.request, "", ""] for i in done)
    (out / "index.html").write_text(render_viewer(done), encoding="utf-8")
    key = [asdict(i) for i in done]
    (out / "key.json").write_text(json.dumps(key, indent=2) + "\n", encoding="utf-8")
    return done, missing


_VIEWER_HEAD = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Verifier labelling</title>
<style>
:root { --bg: #fff; --fg: #1a1a1a; --muted: #666; --line: #ddd; --pick: #e8f0fe; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #161616; --fg: #eee; --muted: #aaa; --line: #333; --pick: #1d2b45; }
}
body { background: var(--bg); color: var(--fg); font: 15px/1.45 system-ui, sans-serif;
  margin: 0 auto; max-width: 1100px; padding: 16px; }
.item { border-top: 1px solid var(--line); padding: 16px 0; }
.frames { display: flex; gap: 6px; overflow-x: auto; }
.frames img { height: 220px; cursor: zoom-in; }
.frames img.big { height: auto; max-width: 100%; cursor: zoom-out; }
label { margin-right: 14px; padding: 4px 8px; border-radius: 6px; }
label:has(input:checked) { background: var(--pick); }
textarea { width: 100%; max-width: 600px; background: var(--bg); color: var(--fg); }
.muted { color: var(--muted); }
</style></head><body>
<h1>Does each clip show the rule being broken?</h1>
<p class="muted">Label from the frames alone: <b>violation</b>, <b>no violation</b>, or
<b>unsure</b> when the frames cannot tell. Labels are kept in this browser; press
<b>Download CSV</b> when done and send the file back. Do not open <code>key.json</code>.</p>
<p>Labeller name: <input id="who" size="16"> <button id="dl">Download CSV</button>
<span id="count" class="muted"></span></p>
"""

_VIEWER_SCRIPT = """<script>
const KEY = "rulecam-verifier-labels";
let saved = {};
try { saved = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) { saved = {}; }
function store() { try { localStorage.setItem(KEY, JSON.stringify(saved)); } catch (e) {} }
const items = [...document.querySelectorAll(".item")];
function count() {
  const n = items.filter(el => saved[el.id] && saved[el.id].label).length;
  document.getElementById("count").textContent = n + " of " + items.length + " labelled";
}
for (const el of items) {
  const s = saved[el.id] || {};
  for (const r of el.querySelectorAll("input[type=radio]")) {
    r.checked = r.value === s.label;
    r.addEventListener("change", () => {
      saved[el.id] = { ...(saved[el.id] || {}), label: r.value }; store(); count();
    });
  }
  const t = el.querySelector("textarea");
  t.value = s.notes || "";
  t.addEventListener("input", () => {
    saved[el.id] = { ...(saved[el.id] || {}), notes: t.value }; store();
  });
}
for (const img of document.querySelectorAll(".frames img")) {
  img.addEventListener("click", () => img.classList.toggle("big"));
}
function cell(v) { return '"' + String(v || "").replace(/"/g, '""') + '"'; }
document.getElementById("dl").addEventListener("click", () => {
  const rows = [["item", "request", "label", "notes"]];
  for (const el of items) {
    const s = saved[el.id] || {};
    rows.push([el.id, el.dataset.request, s.label || "", s.notes || ""]);
  }
  const text = rows.map(r => r.map(cell).join(",")).join("\\n") + "\\n";
  const blob = new Blob([text], { type: "text/csv" });
  const a = document.createElement("a");
  const who = document.getElementById("who").value.trim() || "labeller";
  a.href = URL.createObjectURL(blob); a.download = "labels-" + who + ".csv"; a.click();
});
count();
</script></body></html>
"""


def render_viewer(items: Iterable[VerdictItem]) -> str:
    """The labelling page: per item the request and its frames, never Glimmer's answer."""
    parts = [_VIEWER_HEAD]
    for item in items:
        request = html.escape(item.request)
        frames = (
            "".join(
                f'<img src="{html.escape(p)}" alt="frame {k}" loading="lazy">'
                for k, p in enumerate(item.strips, start=1)
            )
            or '<p class="muted">No strip was found for this item.</p>'
        )
        choices = "".join(
            f'<label><input type="radio" name="{item.item}" value="{value}"> '
            f"{value.replace('_', ' ')}</label>"
            for value in LABELS
        )
        parts.append(
            f'<section class="item" id="{item.item}" data-request="{request}">'
            f"<h2>{item.item}</h2><p><b>Rule:</b> {request}</p>"
            f'<div class="frames">{frames}</div><p>{choices}</p>'
            '<textarea rows="2" placeholder="notes (optional)"></textarea></section>\n'
        )
    parts.append(_VIEWER_SCRIPT)
    return "".join(parts)


def read_labels(path: Path) -> dict[str, Label]:
    """A labeller's CSV as `item -> label` (blank labels are skipped).

    Raises:
        EvalError: If the file is unreadable, lacks the columns, or has an unknown label.
    """
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except OSError as exc:
        msg = f"cannot read {path}: {exc}"
        raise EvalError(msg) from exc
    out: dict[str, Label] = {}
    for n, row in enumerate(rows, start=2):
        if "item" not in row or "label" not in row:
            msg = f"{path}: needs 'item' and 'label' columns"
            raise EvalError(msg)
        value = (row["label"] or "").strip().lower().replace(" ", "_")
        if not value:
            continue
        if value not in LABELS:
            msg = f"{path}:{n}: unknown label {row['label']!r} (use {', '.join(LABELS)})"
            raise EvalError(msg)
        out[row["item"]] = value
    return out


@dataclass(frozen=True)
class Accuracy:
    """Glimmer's decisive verdicts against one reference."""

    reference: str
    items: int  # items with a decisive reference label
    decisive: int  # of those, where Glimmer was decisive too
    correct: int
    unsure: int  # Glimmer `unsure` on a decisive reference item
    accuracy: float | None  # correct / items (an `unsure` counts as not correct)
    accuracy_ci95: tuple[float, float]
    accuracy_when_decisive: float | None


@dataclass(frozen=True)
class LabelReport:
    """What the labelled verifier set says."""

    items: int
    glimmer_unsure_rate: float
    labeller_unsure_rate: dict[str, float]
    labelled: dict[str, int]
    agreement: float | None
    cohen_kappa: float | None
    accuracy: tuple[Accuracy, ...]

    def to_json(self) -> str:
        """The report as indented JSON."""
        return json.dumps(asdict(self), indent=2) + "\n"


def cohen_kappa(a: Sequence[str], b: Sequence[str]) -> float | None:
    """Cohen's kappa of two aligned label lists (`None` with no items or no possible chance)."""
    if not a or len(a) != len(b):
        return None
    n = len(a)
    observed = sum(x == y for x, y in zip(a, b, strict=True)) / n
    cats = set(a) | set(b)
    expected = sum((a.count(c) / n) * (b.count(c) / n) for c in cats)
    if expected >= 1.0:
        return None
    return (observed - expected) / (1.0 - expected)


def accuracy_against(
    reference: str, verdicts: Mapping[str, str], truth: Mapping[str, str]
) -> Accuracy:
    """Glimmer against one reference labelling, on the items the reference labelled decisively."""
    decisive_items = [i for i, label in truth.items() if label in DECISIVE and i in verdicts]
    correct = sum(verdicts[i] == truth[i] for i in decisive_items)
    glimmer_decisive = [i for i in decisive_items if verdicts[i] in DECISIVE]
    n = len(decisive_items)
    return Accuracy(
        reference=reference,
        items=n,
        decisive=len(glimmer_decisive),
        correct=correct,
        unsure=n - len(glimmer_decisive),
        accuracy=correct / n if n else None,
        accuracy_ci95=wilson_interval(correct, n),
        accuracy_when_decisive=(correct / len(glimmer_decisive) if glimmer_decisive else None),
    )


def score_labels(
    verdicts: Mapping[str, str], labels: Mapping[str, Mapping[str, str]]
) -> LabelReport:
    """Score Glimmer's verdicts against one or two labellers.

    Args:
        verdicts: `item -> Glimmer's verdict` (from `key.json`).
        labels: `labeller -> item -> label` (from their CSVs).

    Returns:
        The report: unsure rates, agreement and kappa (with two labellers), and Glimmer's
        accuracy against each labeller and, with two, their consensus.
    """
    n = len(verdicts)
    rows = [accuracy_against(name, verdicts, given) for name, given in labels.items()]
    agreement = kappa = None
    if len(labels) == 2:  # noqa: PLR2004 - agreement is between two labellers
        (_, first), (_, second) = labels.items()
        shared = sorted(set(first) & set(second))
        a, b = [first[i] for i in shared], [second[i] for i in shared]
        if shared:
            agreement = sum(x == y for x, y in zip(a, b, strict=True)) / len(shared)
        kappa = cohen_kappa(a, b)
        consensus = {i: first[i] for i in shared if first[i] == second[i]}
        rows.append(accuracy_against("consensus", verdicts, consensus))
    return LabelReport(
        items=n,
        glimmer_unsure_rate=(sum(v == "unsure" for v in verdicts.values()) / n) if n else 0.0,
        labeller_unsure_rate={
            name: (sum(v == "unsure" for v in given.values()) / len(given)) if given else 0.0
            for name, given in labels.items()
        },
        labelled={name: len(given) for name, given in labels.items()},
        agreement=agreement,
        cohen_kappa=kappa,
        accuracy=tuple(rows),
    )


def load_key(path: Path) -> list[VerdictItem]:
    """Read a sheet's `key.json`.

    Raises:
        EvalError: If it is unreadable or malformed.
    """
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
        return [VerdictItem(**{**row, "strips": tuple(row.get("strips", ()))}) for row in rows]
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        msg = f"cannot read key {path}: {exc}"
        raise EvalError(msg) from exc


def pick_frames(count: int, keep: int) -> list[int]:
    """Indices of `keep` frames spread evenly over `count` (first and last always kept)."""
    if count <= keep:
        return list(range(count))
    if keep == 1:
        return [count - 1]
    return sorted({round(k * (count - 1) / (keep - 1)) for k in range(keep)})


Blur = Callable[[Image], Image]


def downgrade_strip(
    frames: Sequence[Path],
    out_dir: Path,
    *,
    blur: Blur,
    keep: int = OLD_FRAMES,
    width: int = OLD_WIDTH,
) -> list[Path]:
    """The pre-prototype strip format from a raw one: `keep` frames, `width` px, faces blurred.

    The frames (with the annotation band Rulecam drew under each) are scaled down, never up,
    then `blur` runs on each.

    Raises:
        EvalError: If a frame cannot be read or written.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    out = []
    for n, index in enumerate(pick_frames(len(frames), keep), start=1):
        read = cv2.imread(str(frames[index]))
        if read is None:
            msg = f"cannot read strip frame {frames[index]}"
            raise EvalError(msg)
        image: Image = np.asarray(read, dtype=np.uint8)
        height, current = image.shape[:2]
        if current > width:
            size = (width, max(1, round(height * width / current)))
            image = np.asarray(cv2.resize(image, size, interpolation=cv2.INTER_AREA), np.uint8)
        path = out_dir / f"{n}.jpg"
        if not cv2.imwrite(str(path), blur(image), [cv2.IMWRITE_JPEG_QUALITY, 85]):
            msg = f"cannot write {path}"
            raise EvalError(msg)
        out.append(path)
    return out


def rule_ir(item: VerdictItem) -> RuleIR:
    """The item's rule as a `RuleIR` (the campaign JSON keeps the rule without its source)."""
    data = dict(item.rule)
    data.setdefault("source", {"kind": "text", "text": item.request})
    data.setdefault("actions", [])
    return RuleIR.model_validate(data)


def candidate_for(item: VerdictItem, frames: Sequence[Path], *, variant: str) -> CandidateEvent:
    """The item's candidate again, with another strip (for replaying it to Glimmer)."""
    rule = rule_ir(item)
    trace = {k: v for k, v in item.trace.items() if isinstance(v, float | int | str | bool)}
    return CandidateEvent(
        event_id=f"{item.event_id}-{variant}",
        rule_id=item.rule_id,
        camera_id=item.camera_id,
        ts_start=_utc(item.ts_start),
        ts_end=_utc(item.ts_end),
        severity=rule.severity,
        measurement=Measurement.model_validate(item.measurement) if item.measurement else None,
        predicate_trace=trace,
        strip_paths=tuple(str(p) for p in frames),
        track_window_hash="replay",
        plan_version_hash="replay",
        rule_ir_hash="replay",
    )


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))
