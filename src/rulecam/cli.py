"""The `rulecam` command line. Feature packages register sub-commands here as they land."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from rulecam.bus import Bus
from rulecam.capture import Chunker
from rulecam.clock import SystemClock
from rulecam.config import get_settings
from rulecam.contracts import CONTRACTS_VERSION
from rulecam.evidence.chain import EventChain, read_chain
from rulecam.replay.cache import ReplayCache
from rulecam.replay.golden import GoldenRunner

if TYPE_CHECKING:
    from datetime import datetime

    from rulecam.advisor.seal import SealBox
    from rulecam.eval.schema import EvalSet
    from rulecam.eval.score import EvalReport
    from rulecam.eval.scorecard import Checks
    from rulecam.extensions.api import Extensions

app = typer.Typer(help="Rulecam: a camera agent that plans its own job and proves its work.")


@app.command()
def version() -> None:
    """Show the package and contracts versions."""
    from importlib.metadata import version as pkg_version  # noqa: PLC0415 - fast CLI startup

    typer.echo(f"rulecam {pkg_version('rulecam')} (contracts {CONTRACTS_VERSION})")


@app.command()
def capture(
    camera_id: Annotated[str, typer.Argument(help="Camera id, e.g. cam1")],
    source: Annotated[str, typer.Argument(help="Webcam index, rtsp:// URL or file path")],
    *,
    out_dir: Annotated[Path, typer.Option(help="Where chunks are written")] = Path("data/chunks"),
    fps: Annotated[float, typer.Option(help="Output frame rate")] = 10.0,
    chunk_seconds: Annotated[float, typer.Option(help="Chunk length in seconds")] = 3.0,
    realtime: Annotated[bool, typer.Option(help="Read files at native speed")] = False,
) -> None:
    """Capture a camera, stream or file into 3 s H.264 faststart chunks (Ctrl-C to stop)."""
    chunker = Chunker(
        camera_id,
        int(source) if source.isdigit() else source,
        out_dir,
        bus=Bus(),
        clock=SystemClock(),
        fps=fps,
        chunk_seconds=chunk_seconds,
        realtime=realtime,
    )
    asyncio.run(chunker.run())
    typer.echo(f"wrote chunks for {camera_id} to {out_dir}")


golden_app = typer.Typer(help="Record and verify golden demo runs (scenarios/<name>.yaml).")
app.add_typer(golden_app, name="golden")

SCENARIOS_DIR = Path("scenarios")


def _golden_runner(scenarios_dir: Path) -> GoldenRunner:
    import rulecam.demo.steps  # noqa: F401, PLC0415 - registers the `demo.*` step kinds

    cache = ReplayCache.from_settings(get_settings(), clock=SystemClock())
    return GoldenRunner(cache, scenarios_dir)


@golden_app.command("record")
def golden_record(
    scenario: Annotated[str, typer.Argument(help="Scenario name, e.g. uc1")],
    *,
    scenarios_dir: Annotated[Path, typer.Option(help="Scenario directory")] = SCENARIOS_DIR,
) -> None:
    """Run a scenario with real model calls, store replies and write its expected outputs."""
    path = _golden_runner(scenarios_dir).record(scenario)
    typer.echo(f"recorded {scenario} -> {path}")


@golden_app.command("verify")
def golden_verify(
    scenario: Annotated[str, typer.Argument(help="Scenario name, e.g. uc1")],
    *,
    scenarios_dir: Annotated[Path, typer.Option(help="Scenario directory")] = SCENARIOS_DIR,
) -> None:
    """Replay a scenario from the cache (no network) and compare with its expected outputs."""
    report = _golden_runner(scenarios_dir).verify(scenario)
    for diff in report.diffs:
        typer.echo(f"✗ {diff.step_id}: {diff.error or 'output changed'}")
    typer.echo(f"{scenario}: {report.steps - len(report.diffs)}/{report.steps} steps reproduced")
    if not report.ok:
        raise typer.Exit(code=1)


@golden_app.command("gate")
def golden_gate(
    scenario: Annotated[
        list[str] | None, typer.Argument(help="Scenario names (default: all)")
    ] = None,
    *,
    scenarios_dir: Annotated[Path, typer.Option(help="Scenario directory")] = SCENARIOS_DIR,
) -> None:
    """Re-score every golden run (`eval score`) and fail if a `demo.score` gate is broken."""
    from rulecam.demo.gate import gate_all  # noqa: PLC0415

    results = gate_all(scenarios_dir, scenario)
    for result in results:
        score = result.score
        summary = (
            f"alerts {score.get('alerts')}, tier1 {score.get('tier1')}, "
            f"false alerts on negative {score.get('false_alerts_on_negative')}"
        )
        typer.echo(f"{'✓' if result.ok else '✗'} {result.scenario}: {summary}")
        for failure in result.failures:
            typer.echo(f"    {failure}")
    if not results:
        typer.echo(f"no scenario under {scenarios_dir}", err=True)
        raise typer.Exit(code=USAGE_ERROR)
    if not all(r.ok for r in results):
        raise typer.Exit(code=AUDIT_FAILED)


@golden_app.command("capture")
def golden_capture(
    scenario: Annotated[
        str, typer.Argument(help="Scenario name; the fixture is scenarios/<name>/")
    ],
    run: Annotated[Path, typer.Option(help="The eval run record (*.run.json) of a record run")],
    plan: Annotated[Path, typer.Option(help="The approved plan as JSON (GET /plans/{id})")],
    metrics: Annotated[
        Path, typer.Option(help="GET /runtime/metrics as JSON, taken after the run")
    ],
    *,
    chain: Annotated[Path | None, typer.Option(help="The day's chain file")] = None,
    data_dir: Annotated[Path | None, typer.Option(help="The API's data dir")] = None,
    scenarios_dir: Annotated[Path, typer.Option(help="Scenario directory")] = SCENARIOS_DIR,
) -> None:
    """Freeze a live RULECAM_MODE=record run into scenarios/<name>/ (run it on the API pod)."""
    from rulecam.demo.capture import CaptureSources, capture  # noqa: PLC0415
    from rulecam.demo.errors import DemoError  # noqa: PLC0415
    from rulecam.eval.record import RunRecord  # noqa: PLC0415
    from rulecam.rules.store import ParquetTrackStore  # noqa: PLC0415

    data = data_dir or get_settings().rulecam_data_dir
    record = RunRecord.model_validate_json(run.read_text(encoding="utf-8"))
    day = record.started_at.date().isoformat()
    store = ParquetTrackStore(data / "tracks")
    sources = CaptureSources(
        run=run,
        plan=plan,
        response=metrics,
        chain=chain or data / "evidence" / "chain" / "demo" / f"{day}.jsonl",
        tracks=store,
        strips=data / "strips",
        replay=get_settings().replay_dir if data_dir is None else data / "replay",
    )
    try:
        fixture = capture(sources, scenarios_dir / scenario)
    except DemoError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=AUDIT_FAILED) from exc
    finally:
        store.close()
    typer.echo(f"captured {record.clip} ({record.camera_id}) -> {fixture.root}")


@golden_app.command("export")
def golden_export(
    scenario: Annotated[str, typer.Argument(help="Scenario name")],
    *,
    ui_dir: Annotated[Path, typer.Option(help="The web UI folder")] = Path("ui"),
    set_dir: Annotated[Path, typer.Option("--set", help="Eval-set folder")] = Path("eval/clips"),
    scenarios_dir: Annotated[Path, typer.Option(help="Scenario directory")] = SCENARIOS_DIR,
) -> None:
    """Write the UI's REPLAY bundle of a verified scenario (ui/src/fixtures/golden/<name>.json)."""
    import json  # noqa: PLC0415

    from rulecam.demo.export import export_ui  # noqa: PLC0415
    from rulecam.demo.fixture import GoldenFixture  # noqa: PLC0415
    from rulecam.replay.golden import load_scenario  # noqa: PLC0415

    runner = _golden_runner(scenarios_dir)
    report = runner.verify(scenario)
    if not report.ok:
        typer.echo(f"{scenario} does not verify; run `rulecam golden verify {scenario}`", err=True)
        raise typer.Exit(code=AUDIT_FAILED)
    loaded = load_scenario(runner.scenario_path(scenario))
    expected = json.loads(runner.expected_path(scenario).read_text(encoding="utf-8"))["steps"]
    fixture_dir = scenarios_dir / scenario
    fixture = GoldenFixture(fixture_dir)
    clip_name = fixture.run().clip
    evalset = _load_set(set_dir)
    entry = evalset.clip(clip_name)
    clip = {
        "name": entry.name,
        "description": entry.description,
        "license": entry.license,
        "source": (entry.source.url or entry.source.path) if entry.source else None,
    }
    path = export_ui(
        fixture,
        loaded,
        expected,
        bundle_dir=ui_dir / "src" / "fixtures" / "golden",
        public_dir=ui_dir / "public",
        clip=clip,
    )
    typer.echo(f"exported {scenario} -> {path}")


evidence_app = typer.Typer(help="Evidence chain: daily Merkle roots.")
app.add_typer(evidence_app, name="evidence")


AUDIT_FAILED = 1
USAGE_ERROR = 2


def _chain_dir() -> Path:
    return get_settings().rulecam_data_dir / "evidence" / "chain"


def _parse_day(day: str) -> date:
    try:
        return date.fromisoformat(day)
    except ValueError as exc:
        msg = f"{day!r} is not a YYYY-MM-DD day"
        raise typer.BadParameter(msg, param_hint="DAY") from exc


@evidence_app.command("root")
def evidence_root(
    day: Annotated[str, typer.Argument(help="UTC day, YYYY-MM-DD")],
    *,
    roots_dir: Annotated[Path, typer.Option(help="Committed roots folder")] = Path(
        "evidence/roots"
    ),
    chain_dir: Annotated[Path | None, typer.Option(help="Chain folder (default data/…)")] = None,
) -> None:
    """Write evidence/roots/<day>.txt (one `<site> <root> <n>` line per site); commit it."""
    from rulecam.evidence.merkle import write_day_roots  # noqa: PLC0415

    try:
        path = write_day_roots(chain_dir or _chain_dir(), roots_dir, _parse_day(day))
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=USAGE_ERROR) from exc
    typer.echo(path.read_text(encoding="utf-8").rstrip())
    typer.echo(f"wrote {path}; commit it in today's evidence PR")


@app.command()
def audit(
    day: Annotated[str, typer.Argument(help="UTC day, YYYY-MM-DD")],
    site: Annotated[str, typer.Argument(help="Site name")],
    *,
    store_dir: Annotated[
        Path | None, typer.Option(help="Track store (default data/tracks)")
    ] = None,
    chain_dir: Annotated[Path | None, typer.Option(help="Chain folder (default data/…)")] = None,
) -> None:
    """Verify a day's chain and re-derive every decision from the track store (E9).

    Exits 0 when the chain is intact and every decision reproduces, 1 when either fails, and 2
    when there is nothing to audit (bad day or site, no chain file).
    """
    from rulecam.evidence.audit import audit_chain  # noqa: PLC0415
    from rulecam.rules.store import ParquetTrackStore  # noqa: PLC0415

    if not site or Path(site).name != site or site in {".", ".."}:
        msg = f"invalid site name {site!r}"
        raise typer.BadParameter(msg, param_hint="SITE")
    path = (chain_dir or _chain_dir()) / site / f"{_parse_day(day).isoformat()}.jsonl"
    if not path.is_file():
        typer.echo(f"no chain at {path}", err=True)
        raise typer.Exit(code=USAGE_ERROR)
    store = ParquetTrackStore(store_dir or get_settings().rulecam_data_dir / "tracks")
    report = audit_chain(path, store)
    typer.echo(report.summary())
    if report.failed_seqs:
        typer.echo("not reproduced: " + ", ".join(f"event {seq}" for seq in report.failed_seqs))
    if not report.ok:
        raise typer.Exit(code=AUDIT_FAILED)


eval_app = typer.Typer(help="Eval harness (WS0): play labelled clips, score them, report.")
app.add_typer(eval_app, name="eval")

EVAL_SET = Path("eval/clips")
EVAL_REPORTS = Path("reports/eval")
EVAL_CACHE = Path("data/eval_cache")
EVAL_CHECKS = Path("eval/checks.yaml")

SetOption = Annotated[Path, typer.Option("--set", help="Eval-set folder")]
GpuOption = Annotated[
    float, typer.Option(help="GPU pods' price per hour, all together (SAM + Glimmer)")
]
CamerasOption = Annotated[
    float | None,
    typer.Option(help="Cameras sharing those GPU pods (default: the site config's, else 1)"),
]
SiteOption = Annotated[
    Path | None,
    typer.Option(help="Site config with cameras_per_gpu (default: RULECAM_SITE_CONFIG)"),
]


def _cameras_per_gpu(flag: float | None, site_config: Path | None) -> tuple[float, str]:
    """`--cameras-per-gpu`, else the site config's `cameras_per_gpu`, else 1 (with the source)."""
    from rulecam.eval.score import resolve_cameras_per_gpu  # noqa: PLC0415
    from rulecam.planner.site_config import SiteConfigError, load_site_config  # noqa: PLC0415

    path = site_config or get_settings().rulecam_site_config
    site = None
    if flag is None and path is not None and path.is_file():
        try:
            site = load_site_config(path).cameras_per_gpu
        except SiteConfigError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=USAGE_ERROR) from exc
    return resolve_cameras_per_gpu(flag, site, site_source=f"site config {path}")


def _load_set(set_dir: Path) -> EvalSet:
    from rulecam.eval.errors import EvalSetError  # noqa: PLC0415
    from rulecam.eval.schema import load_eval_set  # noqa: PLC0415

    try:
        return load_eval_set(set_dir)
    except EvalSetError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=USAGE_ERROR) from exc


@eval_app.command("validate")
def eval_validate(*, set_dir: SetOption = EVAL_SET) -> None:
    """Validate every clip.yaml of the eval set and summarise it."""
    evalset = _load_set(set_dir)
    for clip in evalset.clips:
        rules = ", ".join(f"{r.id}={r.status}" for r in clip.rules)
        typer.echo(f"{clip.name}{' (placeholder)' if clip.placeholder else ''}: {rules}")
    typer.echo(f"{len(evalset.clips)} clips, {len(evalset.runnable())} runnable")


@eval_app.command("label")
def eval_label(
    manifest: Annotated[Path, typer.Argument(help="E1 manifest YAML (task P4-06)")],
    *,
    set_dir: SetOption = EVAL_SET,
    check_hashes: Annotated[
        bool, typer.Option(help="Also check each clip file against its pinned sha256")
    ] = False,
) -> None:
    """Check an E1 manifest's consent and list the clip/rule pairs still to be labelled.

    Every entry must carry a signed consent form, so this command refuses the whole manifest if
    any clip has none, and names the clips that need one. It changes nothing on disk.
    """
    from rulecam.eval.manifests import (  # noqa: PLC0415
        ManifestError,
        check_against_set,
        load_manifest,
        unlabelled,
    )

    evalset = _load_set(set_dir)
    try:
        loaded = load_manifest(manifest)
        check_against_set(
            loaded,
            evalset.clips,
            root=Path.cwd() if check_hashes else None,
        )
    except ManifestError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=USAGE_ERROR) from exc

    labelled = [
        (clip.name, rule.id)
        for clip in evalset.clips
        for rule in clip.rules
        if rule.status in {"labelled", "negative"}
    ]
    missing = unlabelled(loaded, labelled)
    typer.echo(f"{loaded.name}: {len(loaded.entries)} consented clip/rule pairs")
    for pair in missing:
        typer.echo(f"  to label: {pair}")
    typer.echo(
        f"{len(loaded.entries) - len(missing)} labelled, {len(missing)} still to label"
        if missing
        else "every pair is labelled"
    )


@eval_app.command("fetch")
def eval_fetch(
    *,
    set_dir: SetOption = EVAL_SET,
    cache_dir: Annotated[Path, typer.Option(help="Git-ignored clip cache")] = EVAL_CACHE,
) -> None:
    """Download (once) and sha256-check every runnable clip."""
    from rulecam.eval.errors import ClipFetchError  # noqa: PLC0415
    from rulecam.eval.fetch import resolve_clip  # noqa: PLC0415

    evalset = _load_set(set_dir)
    for clip in evalset.runnable():
        try:
            path = resolve_clip(clip, evalset.clip_dir(clip.name), cache_dir=cache_dir)
        except ClipFetchError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=AUDIT_FAILED) from exc
        typer.echo(f"{clip.name}: {path}")


def _write_outputs(report: EvalReport, out: Path, checks: Path | None, source: str) -> None:
    from rulecam.eval.score import save_report  # noqa: PLC0415
    from rulecam.eval.scorecard import (  # noqa: PLC0415
        build_scorecard,
        load_checks,
        render_markdown,
    )

    day = report.generated_at.date().isoformat()
    json_path = save_report(report, out / f"{day}.json")
    card = build_scorecard(
        title=f"Rulecam 9.5 scorecard, {day}",
        report=report,
        checks=load_checks(checks) if checks and checks.is_file() else None,
        sources=(source, f"report: {json_path}"),
    )
    md_path = out / f"{day}.md"
    md_path.write_text(render_markdown(card), encoding="utf-8")
    typer.echo(render_markdown(card))
    typer.echo(f"wrote {json_path} and {md_path}")


@eval_app.command("run")
def eval_run(
    *,
    set_dir: SetOption = EVAL_SET,
    clip: Annotated[list[str] | None, typer.Option(help="Only these clips")] = None,
    api: Annotated[str, typer.Option(help="API base URL (loopback)")] = "http://localhost:8000",
    speed: Annotated[float, typer.Option(help="Pace, 1 = real time (max ~2.8)")] = 1.0,
    survey_s: Annotated[float, typer.Option(help="Seconds fed before the request")] = 80.0,
    plan_timeout_s: Annotated[float, typer.Option(help="How long the planner may take")] = 900.0,
    include_unlabelled: Annotated[bool, typer.Option(help="Also run unlabelled rules")] = False,
    rule: Annotated[list[str] | None, typer.Option(help="Only these rule ids")] = None,
    bus: Annotated[bool, typer.Option(help="Listen on /ws for alert times")] = True,
    track_store: Annotated[Path | None, typer.Option(help="The API's track store")] = None,
    cache_dir: Annotated[Path, typer.Option(help="Git-ignored clip cache")] = EVAL_CACHE,
    out: Annotated[Path, typer.Option(help="Reports folder")] = EVAL_REPORTS,
    checks: Annotated[Path | None, typer.Option(help="Checks file")] = EVAL_CHECKS,
    gpu_usd_per_hour: GpuOption = 0.0,
    cameras_per_gpu: CamerasOption = None,
    site_config: SiteOption = None,
) -> None:
    """Play the eval set into a running API, then score it and write the scorecard."""
    from datetime import UTC, datetime  # noqa: PLC0415

    from rulecam.eval.client import HttpApiClient, NoBus, WsBusListener, ws_url  # noqa: PLC0415
    from rulecam.eval.fetch import resolve_clip  # noqa: PLC0415
    from rulecam.eval.record import load_runs  # noqa: PLC0415
    from rulecam.eval.runner import EvalRunner, RunConfig, run_eval_set  # noqa: PLC0415
    from rulecam.eval.score import ScoreConfig, score_runs  # noqa: PLC0415

    evalset = _load_set(set_dir)
    entries = [c for c in evalset.runnable() if not clip or c.name in clip]
    videos = [(c, resolve_clip(c, evalset.clip_dir(c.name), cache_dir=cache_dir)) for c in entries]
    store = None
    if track_store is not None:
        from rulecam.rules.store import ParquetTrackStore  # noqa: PLC0415

        store = ParquetTrackStore(track_store)
    client = HttpApiClient(api)
    runner = EvalRunner(
        client,
        config=RunConfig(speed=speed, survey_s=survey_s, plan_timeout_s=plan_timeout_s),
        bus=(lambda: WsBusListener(ws_url(api))) if bus else NoBus,
        tracks=store,
    )
    now = datetime.now(UTC)
    runs_dir = out / "runs" / now.strftime("%Y-%m-%dT%H%M%SZ")
    try:
        run_eval_set(
            runner,
            videos,
            work_dir=cache_dir / "chunks",
            runs_dir=runs_dir,
            include_unlabelled=include_unlabelled,
            rules=rule or (),
        )
    finally:
        client.close()
    cameras, source = _cameras_per_gpu(cameras_per_gpu, site_config)
    config = ScoreConfig(
        gpu_usd_per_hour=gpu_usd_per_hour, cameras_per_gpu=cameras, cameras_per_gpu_source=source
    )
    report = score_runs(load_runs(runs_dir), evalset, config=config, generated_at=datetime.now(UTC))
    _write_outputs(report, out, checks, f"live runs: {runs_dir} against {api}")


@eval_app.command("score")
def eval_score(
    runs: Annotated[Path, typer.Argument(help="Folder of *.run.json records")],
    *,
    set_dir: SetOption = EVAL_SET,
    out: Annotated[Path, typer.Option(help="Reports folder")] = EVAL_REPORTS,
    checks: Annotated[Path | None, typer.Option(help="Checks file")] = EVAL_CHECKS,
    iou: Annotated[float, typer.Option(help="Temporal IoU to match an alert")] = 0.3,
    gpu_usd_per_hour: GpuOption = 0.0,
    cameras_per_gpu: CamerasOption = None,
    site_config: SiteOption = None,
) -> None:
    """Re-score recorded runs offline (replay mode: no API, no network)."""
    from datetime import UTC, datetime  # noqa: PLC0415

    from rulecam.eval.errors import EvalError  # noqa: PLC0415
    from rulecam.eval.record import load_runs  # noqa: PLC0415
    from rulecam.eval.score import ScoreConfig, score_runs  # noqa: PLC0415

    evalset = _load_set(set_dir)
    cameras, source = _cameras_per_gpu(cameras_per_gpu, site_config)
    config = ScoreConfig(
        iou=iou,
        gpu_usd_per_hour=gpu_usd_per_hour,
        cameras_per_gpu=cameras,
        cameras_per_gpu_source=source,
    )
    try:
        report = score_runs(load_runs(runs), evalset, config=config, generated_at=datetime.now(UTC))
    except EvalError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=USAGE_ERROR) from exc
    _write_outputs(report, out, checks, f"replayed runs: {runs}")


@eval_app.command("caviar-mot")
def eval_caviar_mot(
    xml: Annotated[list[Path], typer.Argument(help="CAVIAR ground-truth XML, in stream order")],
    *,
    out: Annotated[Path, typer.Option(help="MOT gt.txt to write")] = Path("gt.txt"),
) -> None:
    """Convert CAVIAR XML to MOT gt.txt; several files are chained into one stream in order."""
    from rulecam.eval.caviar import chain_mot, load_caviar  # noqa: PLC0415
    from rulecam.eval.errors import EvalError  # noqa: PLC0415

    try:
        rows, segments = chain_mot([load_caviar(path) for path in xml])
    except EvalError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=USAGE_ERROR) from exc
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(r + "\n" for r in rows), encoding="utf-8")
    for seg in segments:
        typer.echo(
            f"{seg.name}: frames {seg.first_frame}-{seg.first_frame + seg.frames - 1}, "
            f"{seg.start_s:.1f}-{seg.end_s:.1f} s"
        )
    typer.echo(f"wrote {len(rows)} rows to {out}")


def _parse_utc(value: str) -> datetime:
    from datetime import UTC, datetime  # noqa: PLC0415

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        typer.echo(f"not an ISO time: {value!r}", err=True)
        raise typer.Exit(code=USAGE_ERROR) from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@eval_app.command("track")
def eval_track(
    *,
    store: Annotated[Path, typer.Option(help="Track store root (data/tracks)")],
    camera: Annotated[str, typer.Option(help="Camera the stream was fed as")],
    start: Annotated[str, typer.Option(help="Camera time of the stream's first frame (ISO)")],
    gt: Annotated[Path | None, typer.Option(help="MOT gt.txt of the stream")] = None,
    caviar: Annotated[
        list[Path] | None, typer.Option(help="CAVIAR XML instead of --gt (repeat, stream order)")
    ] = None,
    end: Annotated[str | None, typer.Option(help="Stream end (default: start + GT length)")] = None,
    fps: Annotated[float, typer.Option(help="Ground-truth frame rate")] = 25.0,
    scale: Annotated[float, typer.Option(help="Stored px -> GT px (0.5 for a 2x upscale)")] = 1.0,
    concept: Annotated[str, typer.Option(help="GT class 1 is this concept")] = "person",
    identities: Annotated[
        Path | None, typer.Option(help="track_identity.jsonl (data/ask/identity) for re-ID ids")
    ] = None,
    out: Annotated[Path | None, typer.Option(help="Write the JSON report here")] = None,
) -> None:
    """Score a camera's stored tracks (HOTA, IDF1, MOTA), raw ids and re-ID identities apart."""
    from datetime import timedelta  # noqa: PLC0415

    from rulecam.eval.caviar import chain_mot, load_caviar  # noqa: PLC0415
    from rulecam.eval.errors import EvalError  # noqa: PLC0415
    from rulecam.eval.tracking import parse_mot  # noqa: PLC0415
    from rulecam.eval.trackstore import load_identities, render_markdown, score_store  # noqa: PLC0415
    from rulecam.rules.store import ParquetTrackStore  # noqa: PLC0415

    if (gt is None) == (not caviar):
        typer.echo("give exactly one of --gt or --caviar", err=True)
        raise typer.Exit(code=USAGE_ERROR)
    t0 = _parse_utc(start)
    try:
        if caviar:
            rows, _ = chain_mot([load_caviar(path) for path in caviar])
        else:
            assert gt is not None  # noqa: S101 - checked above
            rows = gt.read_text(encoding="utf-8").splitlines()
        truth = parse_mot(rows, {1: concept})
        last = max((f for frames in truth.values() for f in frames), default=1)
        t1 = _parse_utc(end) if end else t0 + timedelta(seconds=last / fps)
        with ParquetTrackStore(store) as tracks:
            report = score_store(
                tracks,
                truth,
                camera_id=camera,
                start=t0,
                end=t1,
                fps=fps,
                scale=(scale, scale),
                identities=load_identities(identities) if identities else None,
            )
    except (EvalError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=USAGE_ERROR) from exc
    typer.echo(render_markdown(report))
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report.to_json(), encoding="utf-8")
        typer.echo(f"wrote {out}")


README_INTRO = (
    "Measured on the deployed stack and on public hand-labelled footage. Each row is read from "
    "the result file it cites by `uv run rulecam eval results`, with its run, date and the "
    "commit that recorded it; a test fails if a number here is not in that file. Weak results "
    "are shown as measured. The verifier is not yet scored against human labels (the blind "
    "labelling kit is in `eval/README.md`); more in [`eval/RESULTS.md`](eval/RESULTS.md)."
)
RESULTS_INTRO = (
    "Every number below is read from the result file its row cites (`uv run rulecam eval "
    "results`, facts in `eval/results.yaml`), with the run, the date and the commit that "
    "recorded the file. Weak results are shown as measured. The formal evaluations further "
    "down, on consented recordings, have not been run yet."
)


@eval_app.command("results")
def eval_results(
    *,
    facts: Annotated[Path, typer.Option(help="The facts file")] = Path("eval/results.yaml"),
    readme: Annotated[Path, typer.Option(help="README with RESULTS markers")] = Path("README.md"),
    results: Annotated[Path, typer.Option(help="RESULTS.md with markers")] = Path(
        "eval/RESULTS.md"
    ),
    check: Annotated[bool, typer.Option(help="Only report whether the files are current")] = False,
) -> None:
    """Regenerate the README and RESULTS.md results tables from reports/eval/*.json."""
    from rulecam.eval.errors import EvalError  # noqa: PLC0415
    from rulecam.eval.results_doc import load_facts, render_section, splice  # noqa: PLC0415

    try:
        found = load_facts(facts, Path.cwd())
        targets = [
            (readme, splice(readme.read_text("utf-8"), render_section(found, README_INTRO))),
            (
                results,
                splice(
                    results.read_text("utf-8"), render_section(found, RESULTS_INTRO), prefix="../"
                ),
            ),
        ]
    except (EvalError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=USAGE_ERROR) from exc
    stale = [path for path, text in targets if path.read_text("utf-8") != text]
    if check:
        for path in stale:
            typer.echo(f"{path} is out of date: run `uv run rulecam eval results`", err=True)
        raise typer.Exit(code=AUDIT_FAILED if stale else 0)
    for path, text in targets:
        path.write_text(text, encoding="utf-8")
    typer.echo(f"{len(found)} numbers written to {readme} and {results}")


CAMPAIGN = (
    "run1=reports/eval/2026-11-01-live-test-campaign-run1.json",
    "run2=reports/eval/2026-11-01-live-test-campaign.json",
)


@eval_app.command("verifier-sheet")
def eval_verifier_sheet(
    *,
    campaign: Annotated[
        list[str] | None, typer.Option(help="NAME=campaign.json (repeat; default: both 1 Nov runs)")
    ] = None,
    strips: Annotated[Path, typer.Option(help="The API's strip folder")] = Path("data/strips"),
    out: Annotated[Path, typer.Option(help="Sheet folder")] = Path("data/verifier-sheet"),
    seed: Annotated[int, typer.Option(help="Shuffle seed")] = 1101,
) -> None:
    """Write a blind labelling sheet of the campaign's Glimmer verdicts (verdicts hidden)."""
    from rulecam.eval.errors import EvalError  # noqa: PLC0415
    from rulecam.eval.verifier_set import blind, collect_items, write_sheet  # noqa: PLC0415

    runs = []
    for spec in campaign or CAMPAIGN:
        name, sep, path = spec.partition("=")
        if not sep:
            typer.echo(f"--campaign wants NAME=path, got {spec!r}", err=True)
            raise typer.Exit(code=USAGE_ERROR)
        runs.append((name, Path(path)))
    try:
        items = blind(collect_items(runs), seed=seed)
    except EvalError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=USAGE_ERROR) from exc
    done, missing = write_sheet(items, out, strips)
    typer.echo(f"{len(done)} verdicts -> {out}/index.html and {out}/labels.csv")
    typer.echo(f"answer key: {out}/key.json (do not show it to the labellers)")
    if missing:
        typer.echo(f"no strip found under {strips} for: {', '.join(missing)}", err=True)


@eval_app.command("verifier-score")
def eval_verifier_score(
    labels: Annotated[list[Path], typer.Argument(help="One or two labellers' CSVs")],
    *,
    key: Annotated[Path, typer.Option(help="The sheet's key.json")] = Path(
        "data/verifier-sheet/key.json"
    ),
    out: Annotated[Path | None, typer.Option(help="Write the JSON report here")] = None,
) -> None:
    """Score Glimmer's verdicts against the labels: accuracy, unsure rate, Cohen's kappa."""
    from rulecam.eval.errors import EvalError  # noqa: PLC0415
    from rulecam.eval.verifier_set import load_key, read_labels, score_labels  # noqa: PLC0415

    try:
        verdicts = {item.item: item.verdict for item in load_key(key)}
        given = {path.stem: read_labels(path) for path in labels}
    except EvalError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=USAGE_ERROR) from exc
    report = score_labels(verdicts, given)
    typer.echo(report.to_json())
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report.to_json(), encoding="utf-8")


@eval_app.command("ask")
def eval_ask(
    questions: Annotated[Path, typer.Argument(help="Question set YAML (eval/ask/*.yaml)")],
    *,
    api: Annotated[
        str, typer.Option(help="API base URL, or the UI's proxy (https://<ui>/rulecam-api)")
    ] = "http://localhost:8000",
    runs: Annotated[int, typer.Option(min=1, help="Times each question is asked")] = 3,
    question: Annotated[list[str] | None, typer.Option(help="Only these question ids")] = None,
    origin: Annotated[
        str | None,
        typer.Option(
            help="Origin for a logged-in session (default: PUBLIC_UI_URL, else the API's)"
        ),
    ] = None,
    out: Annotated[Path, typer.Option(help="Reports folder")] = EVAL_REPORTS / "ask",
) -> None:
    """Ask a question set of a running API N times and score the numbers against the truth.

    On the API's own machine no login is needed; elsewhere set RULECAM_OWNER_PASSPHRASE and the
    command logs in as the owner.
    """
    from datetime import UTC, datetime  # noqa: PLC0415

    from rulecam.eval.ask import (  # noqa: PLC0415
        HttpAskApi,
        load_question_set,
        render_markdown,
        run_benchmark,
        save_benchmark,
    )
    from rulecam.eval.errors import EvalError  # noqa: PLC0415

    settings = get_settings()
    passphrase = settings.rulecam_owner_passphrase
    try:
        loaded = load_question_set(questions)
        client = HttpAskApi(
            api,
            passphrase=passphrase.get_secret_value() if passphrase else None,
            origin=origin or settings.public_ui_url,
        )
        try:
            benchmark = run_benchmark(
                client,
                loaded,
                runs=runs,
                api_url=api,
                generated_at=datetime.now(UTC),
                only=question or (),
            )
        finally:
            client.close()
    except EvalError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=USAGE_ERROR) from exc
    json_path, md_path = save_benchmark(benchmark, out)
    typer.echo(render_markdown(benchmark))
    typer.echo(f"wrote {json_path} and {md_path}")


AUTO_CHECKS = "auto"
DEFAULT_UI = "https://rulecam.vercel.app"


def _auto_checks(
    *, api: str | None, ui: str | None, restart: Path | None, budget: Path | None, dry_run: bool
) -> Checks:
    from rulecam.eval.autochecks import AutoConfig, run_auto_checks  # noqa: PLC0415
    from rulecam.eval.scorecard import Checks  # noqa: PLC0415

    settings = get_settings()
    public_api = api or settings.public_tunnel_url
    if not public_api:
        typer.echo("--checks auto needs --api (the public API URL) or PUBLIC_TUNNEL_URL", err=True)
        raise typer.Exit(code=USAGE_ERROR)
    config = AutoConfig(
        api=public_api.rstrip("/"),
        ui=(ui or settings.public_ui_url or DEFAULT_UI).rstrip("/"),
        restart_evidence=restart,
        budget_evidence=budget,
        deploy_dry_run=AutoConfig.deploy_dry_run if dry_run else None,
    )
    return Checks(checks=run_auto_checks(config))


@app.command()
def scorecard(
    *,
    report: Annotated[Path | None, typer.Option(help="Eval report JSON")] = None,
    checks: Annotated[
        str, typer.Option(help="Checks file, or `auto` to run the automatable checks")
    ] = str(EVAL_CHECKS),
    checks_file: Annotated[
        Path, typer.Option(help="With --checks auto: the manual checks underneath")
    ] = EVAL_CHECKS,
    api: Annotated[str | None, typer.Option(help="Public API URL for --checks auto")] = None,
    ui: Annotated[str | None, typer.Option(help="Public site URL for --checks auto")] = None,
    restart_evidence: Annotated[
        Path | None, typer.Option(help="scripts/ops/restart_check.sh output")
    ] = None,
    budget_evidence: Annotated[
        Path | None, typer.Option(help="scripts/ops/budget_alarm_check.sh output")
    ] = None,
    deploy_dry_run: Annotated[
        bool, typer.Option(help="Run runpod_up.sh --dry-run for one_command_deploy")
    ] = True,
    live_api: Annotated[str | None, typer.Option(help="Sample /runtime/metrics here")] = None,
    samples: Annotated[int, typer.Option(help="Live samples")] = 60,
    interval_s: Annotated[float, typer.Option(help="Seconds between live samples")] = 30.0,
    out: Annotated[Path | None, typer.Option(help="Write the markdown here")] = None,
) -> None:
    """Print the PLAN-9.5 §2 scorecard from an eval report, live metrics and the checks.

    `--checks auto` runs the automatable checks (`rulecam.eval.autochecks`) on top of
    `--checks-file` and writes the merged checks with their evidence next to the markdown
    (`checks-<date>.yaml`). A check passes only on observed evidence.
    """
    from datetime import UTC, datetime  # noqa: PLC0415

    from rulecam.eval.autochecks import checks_yaml, merge_checks  # noqa: PLC0415
    from rulecam.eval.client import HttpApiClient  # noqa: PLC0415
    from rulecam.eval.live import sample_live  # noqa: PLC0415
    from rulecam.eval.score import load_report  # noqa: PLC0415
    from rulecam.eval.scorecard import build_scorecard, load_checks, render_markdown  # noqa: PLC0415

    sources, live = [], None
    loaded = load_report(report) if report else None
    if report:
        sources.append(f"report: {report}")
    if live_api:
        client = HttpApiClient(live_api)
        try:
            live = sample_live(
                client, samples=samples, interval_s=interval_s, source=f"live {live_api}"
            )
        finally:
            client.close()
        sources.append(f"live: {live_api}, {samples} samples every {interval_s:g} s")
    day = datetime.now(UTC).date().isoformat()
    manual_path = checks_file if checks == AUTO_CHECKS else Path(checks)
    manual = load_checks(manual_path) if manual_path.is_file() else None
    if manual is not None:
        sources.append(f"checks: {manual_path}")
    merged = manual
    if checks == AUTO_CHECKS:
        auto = _auto_checks(
            api=api,
            ui=ui,
            restart=restart_evidence,
            budget=budget_evidence,
            dry_run=deploy_dry_run,
        )
        merged = merge_checks(manual, auto.checks)
        evidence = (out.parent if out else EVAL_REPORTS) / f"checks-{day}.yaml"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(checks_yaml(merged), encoding="utf-8")
        sources.append(f"automated checks ({len(auto.checks)}): {evidence}")
    card = build_scorecard(
        title=f"Rulecam 9.5 scorecard, {day}",
        report=loaded,
        checks=merged,
        live=live,
        sources=sources,
    )
    text = render_markdown(card)
    try:
        typer.echo(text)
    except UnicodeEncodeError:
        sys.stdout.buffer.write(text.encode("utf-8", errors="replace") + b"\n")
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        typer.echo(f"wrote {out}")


owner_app = typer.Typer(help="Owner login for the web UI (docs/deploy.md, Owner login).")
app.add_typer(owner_app, name="owner")


def _read_passphrase(prompt: Callable[[str], str]) -> str:
    """Ask twice (never echoed) until the two match and the passphrase is acceptable."""
    from rulecam.api.passphrase import passphrase_problem  # noqa: PLC0415

    first = prompt("New owner passphrase: ")
    problem = passphrase_problem(first)
    if problem is not None:
        typer.echo(f"not set: {problem}", err=True)
        raise typer.Exit(code=USAGE_ERROR)
    if prompt("Repeat it: ") != first:
        typer.echo("not set: the two passphrases differ", err=True)
        raise typer.Exit(code=USAGE_ERROR)
    return first


@owner_app.command("set-passphrase")
def owner_set_passphrase() -> None:
    """Prompt for a passphrase and print the OWNER_PASSPHRASE_HASH line for the API's .env.

    The passphrase is read without echo and never printed, stored or logged; only its scrypt
    hash is printed. Restart the API after adding the line.
    """
    import getpass  # noqa: PLC0415

    from rulecam.api.passphrase import hash_passphrase  # noqa: PLC0415

    line = hash_passphrase(_read_passphrase(getpass.getpass))
    typer.echo(f"OWNER_PASSPHRASE_HASH={line}")


@app.command()
def openapi(
    out: Annotated[Path, typer.Argument(help="Where to write the OpenAPI JSON")],
) -> None:
    """Export the API's OpenAPI schema (input to the UI's generated client)."""
    from rulecam.api.app import app as api_app  # noqa: PLC0415 - heavy import, only here
    from rulecam.api.app import openapi_json  # noqa: PLC0415

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(openapi_json(api_app), encoding="utf-8")
    typer.echo(f"wrote {out}")


advisor_app = typer.Typer(help="Site Advisor: sealed causes for blind sessions (SA §9).")
app.add_typer(advisor_app, name="advisor")


def _seal_box(site: str) -> SealBox:
    from rulecam.advisor.seal import SealBox  # noqa: PLC0415 - only for these commands

    settings = get_settings()
    chain = EventChain(settings.rulecam_data_dir / "evidence" / "chain", site, clock=SystemClock())
    return SealBox(chain, settings.rulecam_data_dir / "seals")


@advisor_app.command("seal")
def advisor_seal(
    cause: Annotated[str, typer.Argument(help="The hidden cause, in plain words")],
    *,
    site: Annotated[str, typer.Option(help="Site name of the chain")] = "demo",
) -> None:
    """Append sha256(cause + salt) to the chain; the cause and salt stay in data/seals/."""
    seal_id, record = _seal_box(site).seal(cause)
    typer.echo(f"sealed {seal_id} at event {record.seq}; keep data/seals/ offline until reveal")


@advisor_app.command("reveal")
def advisor_reveal(
    seal_id: Annotated[str, typer.Argument(help="The seal id printed by `seal`")],
    *,
    site: Annotated[str, typer.Option(help="Site name of the chain")] = "demo",
) -> None:
    """Append the cause and salt so anyone can recompute the sealed hash."""
    record = _seal_box(site).reveal(seal_id)
    typer.echo(f"revealed {seal_id} at event {record.seq}")


@advisor_app.command("seal-status")
def advisor_seal_status(
    day: Annotated[str, typer.Argument(help="UTC day of the chain, YYYY-MM-DD")],
    *,
    site: Annotated[str, typer.Option(help="Site name of the chain")] = "demo",
) -> None:
    """Show every seal in a day's chain and whether its reveal matches."""
    from rulecam.advisor.seal import dumps_status, seal_ids, seal_status  # noqa: PLC0415

    path = get_settings().rulecam_data_dir / "evidence" / "chain" / site / f"{day}.jsonl"
    records = read_chain(path)
    for seal_id in seal_ids(records):
        typer.echo(dumps_status(seal_status(records, seal_id)))


chain_app = typer.Typer(help="Chain Mode: one rule pack, many stores (rulecam.chain).")
app.add_typer(chain_app, name="chain")


@chain_app.command("prove")
def chain_prove(
    pack: Annotated[Path, typer.Argument(help="A rule pack YAML")],
) -> None:
    """Lint a rule pack and run Rule CI at every knob value stores may pick."""
    from rulecam.chain import load_pack, prove_pack, render_proof  # noqa: PLC0415

    proof = prove_pack(load_pack(pack))
    typer.echo(render_proof(proof), nl=False)
    if not proof.releasable:
        raise typer.Exit(code=1)


@chain_app.command("fit")
def chain_fit(
    pack: Annotated[Path, typer.Argument(help="A rule pack YAML")],
    stores: Annotated[Path, typer.Argument(help="A directory of store setup YAML files")],
    *,
    out: Annotated[Path | None, typer.Option(help="Write every store's fit as JSON")] = None,
) -> None:
    """Fit a pack to every store: bind slots, apply knobs, lint and run Rule CI per store."""
    from pydantic import TypeAdapter  # noqa: PLC0415

    from rulecam.chain import StoreFit, fit_chain, load_pack, load_stores, render_fits  # noqa: PLC0415

    fits = fit_chain(load_pack(pack), load_stores(stores))
    typer.echo(render_fits(fits), nl=False)
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(TypeAdapter(tuple[StoreFit, ...]).dump_json(fits, indent=2))
        typer.echo(f"wrote {out}")


@chain_app.command("demo")
def chain_demo(
    directory: Annotated[Path, typer.Argument(help="e.g. chains/kitchen-basics")],
    *,
    out: Annotated[Path | None, typer.Option(help="Write the full report as JSON")] = None,
) -> None:
    """Prove, fit and score a chain from pack.yaml, stores/ and simulation.yaml (SIMULATED)."""
    from rulecam.chain import (  # noqa: PLC0415
        build_report,
        load_pack,
        load_simulation,
        load_stores,
        render_report,
    )

    pack = load_pack(directory / "pack.yaml")
    sim_path = directory / "simulation.yaml"
    report = build_report(
        pack,
        load_stores(directory / "stores"),
        simulation=load_simulation(sim_path) if sim_path.is_file() else None,
    )
    typer.echo(render_report(report, title=pack.title), nl=False)
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        typer.echo(f"wrote {out}")


@chain_app.command("rollup")
def chain_rollup(
    day: Annotated[str, typer.Argument(help="UTC day, YYYY-MM-DD")],
    roots: Annotated[list[Path], typer.Argument(help="Each store's evidence/roots/<day>.txt")],
    *,
    proofs: Annotated[Path | None, typer.Option(help="Write one inclusion proof per store")] = None,
) -> None:
    """Roll every store's daily evidence root up into one HQ root, with per-store proofs."""
    from rulecam.chain import chain_root, inclusion_proof, load_roots  # noqa: PLC0415

    chain = chain_root(date.fromisoformat(day), load_roots(roots))
    typer.echo(f"{chain.day.isoformat()} HQ root {chain.root} over {len(chain.stores)} stores")
    for store in chain.stores:
        typer.echo(f"  {store.store_id} {store.root} {store.n_records}")
    if proofs is not None:
        proofs.mkdir(parents=True, exist_ok=True)
        for store in chain.stores:
            path = proofs / f"{chain.day.isoformat()}-{store.store_id}.proof.json"
            path.write_text(
                inclusion_proof(chain, store.store_id).model_dump_json(indent=2), encoding="utf-8"
            )
        typer.echo(f"wrote {len(chain.stores)} proofs to {proofs}")


@chain_app.command("verify-proof")
def chain_verify_proof(
    proof: Annotated[Path, typer.Argument(help="A store's .proof.json from `chain rollup`")],
) -> None:
    """Check that a store's evidence root is inside the HQ root it names."""
    from rulecam.chain import InclusionProof, verify_inclusion  # noqa: PLC0415

    checked = InclusionProof.model_validate_json(proof.read_text(encoding="utf-8"))
    if not verify_inclusion(checked):
        typer.echo(f"FAIL: {checked.store.store_id} is not in HQ root {checked.root}")
        raise typer.Exit(code=1)
    typer.echo(
        f"OK: {checked.store.store_id} root {checked.store.root} is in HQ root {checked.root}"
    )


@app.command()
def context(
    vertical: Annotated[str, typer.Argument(help="festival, supermarket or general")],
    *,
    city: Annotated[str | None, typer.Option(help="Overrides RULECAM_SITE_CITY")] = None,
    country: Annotated[str | None, typer.Option(help="ISO 3166-1 alpha-2, e.g. GB")] = None,
    notes: Annotated[str, typer.Option(help="The event's name, the store's street")] = "",
    site: Annotated[str, typer.Option(help="Site name for the prompt")] = "demo",
) -> None:
    """Fetch live site context with search-grounded Muse Spark; print the checked items."""
    from rulecam.api.site_tools import site_location  # noqa: PLC0415 - heavy imports, only here
    from rulecam.clients.meta_hosted.spark import SparkClient  # noqa: PLC0415
    from rulecam.context import SiteContextService  # noqa: PLC0415

    if vertical not in {"festival", "supermarket", "general"}:
        raise typer.BadParameter("festival, supermarket or general", param_hint="VERTICAL")
    settings = get_settings()
    clock = SystemClock()
    where = site_location(settings)
    where = where.model_copy(
        update={
            k: v
            for k, v in {"city": city, "country": country.upper() if country else None}.items()
            if v
        }
    )
    service = SiteContextService(
        SparkClient.from_settings(settings, clock=clock),
        clock=clock,
        out_dir=settings.rulecam_data_dir / "context",
        site_name=site,
        location=where,
    )
    found = service.refresh(vertical, notes=notes)  # type: ignore[arg-type]
    typer.echo(found.model_dump_json(indent=2))


@app.command("shelf-scan")
def shelf_scan(
    image: Annotated[Path, typer.Argument(help="A JPEG or PNG of a shelf")],
) -> None:
    """Find shelf gaps on an image: Muse Spark proposes them, SAM 3.1 confirms them."""
    from rulecam.clients.meta_hosted.sam import HostedSam31  # noqa: PLC0415 - heavy imports
    from rulecam.clients.meta_hosted.spark import SparkClient  # noqa: PLC0415
    from rulecam.shelf import ShelfScanner  # noqa: PLC0415

    settings = get_settings()
    clock = SystemClock()
    spark = SparkClient.from_settings(settings, clock=clock)
    scanner = ShelfScanner(
        spark=spark,
        files=spark,
        sam=HostedSam31.from_settings(settings, clock=clock),
        out_dir=settings.rulecam_data_dir / "shelf" / "marked",
    )
    typer.echo(scanner.scan_image(image).model_dump_json(indent=2))


extension_app = typer.Typer(
    help="Rule extensions: Muse Code writes a new check type; prove, review, approve it."
)
app.add_typer(extension_app, name="extension")


def _extensions(site: str) -> Extensions:
    from rulecam.extensions.api import build_extensions  # noqa: PLC0415 - heavy imports

    settings = get_settings()
    clock = SystemClock()
    chain = EventChain(settings.rulecam_data_dir / "evidence" / "chain", site, clock=clock)
    cache = ReplayCache.from_settings(settings, clock=clock)
    return build_extensions(settings, cache=cache, clock=clock, bus=Bus(), chain=chain)


def _line(text: str) -> tuple[tuple[float, float], tuple[float, float]]:
    """`name=x1,y1,x2,y2` → the line's two points."""
    values = [float(v) for v in text.split(",")]
    if len(values) != 4:  # noqa: PLR2004 - two points
        raise typer.BadParameter("a line is x1,y1,x2,y2 in pixels", param_hint="--line")
    return (values[0], values[1]), (values[2], values[3])


@extension_app.command("new")
def extension_new(
    request: Annotated[str, typer.Argument(help="The owner's words, in quotes")],
    *,
    concept: Annotated[str, typer.Option(help="Rule concept the check sees, e.g. pallet")],
    camera: Annotated[str, typer.Option(help="Camera the line is drawn on")] = "cam1",
    line: Annotated[
        list[str] | None, typer.Option(help="name=x1,y1,x2,y2 in pixels (repeatable)")
    ] = None,
    name: Annotated[str | None, typer.Option(help="Extension name (default: from request)")] = None,
    site: Annotated[str, typer.Option(help="Site whose evidence chain records it")] = "demo",
) -> None:
    """Have Muse Code write a check, prove and review it, and store it (proposed or rejected)."""
    from rulecam.extensions.service import ExtensionRequest  # noqa: PLC0415

    pairs = [item.split("=", 1) for item in (line or []) if "=" in item]
    lines = {key: _line(value) for key, value in pairs}
    service = _extensions(site).service
    if service is None:
        typer.echo("no generator: install the Muse Code CLI or set META_API_KEY", err=True)
        raise typer.Exit(code=2)
    record = service.propose(
        ExtensionRequest(request=request, concept=concept, camera_id=camera, lines=lines, name=name)
    )
    for step in record.proof.steps:
        typer.echo(f"{step.status:>7}  {step.name}: {step.detail}")
    typer.echo(f" review  {record.review.reviewer}: {record.review.verdict}")
    typer.echo(f"{record.status}: {record.ref} (generator {record.generator}, {record.model})")


@extension_app.command("list")
def extension_list(
    site: Annotated[str, typer.Option(help="Site whose evidence chain records it")] = "demo",
) -> None:
    """The newest version of every extension, with its status and ref."""
    for record in _extensions(site).registry.all_latest():
        typer.echo(f"{record.status:>9}  {record.ref}  {record.request}")


@extension_app.command("approve")
def extension_approve(
    name: Annotated[str, typer.Argument(help="Extension name")],
    sha: Annotated[str, typer.Argument(help="At least 12 hex characters of its sha256")],
    site: Annotated[str, typer.Option(help="Site whose evidence chain records it")] = "demo",
) -> None:
    """Approve a proposed version that passed its proof and review (as the owner)."""
    record = _extensions(site).registry.approve(name, sha, by="owner")
    use = f"StateCheck(object={record.concept!r}, state={record.ref!r})"
    typer.echo(f"approved {record.ref}: use {use}")


@app.callback()
def main() -> None:
    """Rulecam command line."""
