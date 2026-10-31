"""The `rulecam` command line. Feature packages register sub-commands here as they land."""

from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from rulecam.bus import Bus
from rulecam.capture import Chunker
from rulecam.clock import SystemClock
from rulecam.config import get_settings
from rulecam.contracts import CONTRACTS_VERSION
from rulecam.replay.cache import ReplayCache
from rulecam.replay.golden import GoldenRunner

if TYPE_CHECKING:
    from rulecam.eval.schema import EvalSet
    from rulecam.eval.score import EvalReport

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
CamerasOption = Annotated[float, typer.Option(help="Cameras sharing those GPU pods")]


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
    bus: Annotated[bool, typer.Option(help="Listen on /ws for alert times")] = True,
    track_store: Annotated[Path | None, typer.Option(help="The API's track store")] = None,
    cache_dir: Annotated[Path, typer.Option(help="Git-ignored clip cache")] = EVAL_CACHE,
    out: Annotated[Path, typer.Option(help="Reports folder")] = EVAL_REPORTS,
    checks: Annotated[Path | None, typer.Option(help="Checks file")] = EVAL_CHECKS,
    gpu_usd_per_hour: GpuOption = 0.0,
    cameras_per_gpu: CamerasOption = 1.0,
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
        )
    finally:
        client.close()
    config = ScoreConfig(gpu_usd_per_hour=gpu_usd_per_hour, cameras_per_gpu=cameras_per_gpu)
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
    cameras_per_gpu: CamerasOption = 1.0,
) -> None:
    """Re-score recorded runs offline (replay mode: no API, no network)."""
    from datetime import UTC, datetime  # noqa: PLC0415

    from rulecam.eval.errors import EvalError  # noqa: PLC0415
    from rulecam.eval.record import load_runs  # noqa: PLC0415
    from rulecam.eval.score import ScoreConfig, score_runs  # noqa: PLC0415

    evalset = _load_set(set_dir)
    config = ScoreConfig(
        iou=iou, gpu_usd_per_hour=gpu_usd_per_hour, cameras_per_gpu=cameras_per_gpu
    )
    try:
        report = score_runs(load_runs(runs), evalset, config=config, generated_at=datetime.now(UTC))
    except EvalError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=USAGE_ERROR) from exc
    _write_outputs(report, out, checks, f"replayed runs: {runs}")


@app.command()
def scorecard(
    *,
    report: Annotated[Path | None, typer.Option(help="Eval report JSON")] = None,
    checks: Annotated[Path | None, typer.Option(help="Checks file")] = EVAL_CHECKS,
    live_api: Annotated[str | None, typer.Option(help="Sample /runtime/metrics here")] = None,
    samples: Annotated[int, typer.Option(help="Live samples")] = 60,
    interval_s: Annotated[float, typer.Option(help="Seconds between live samples")] = 30.0,
    out: Annotated[Path | None, typer.Option(help="Write the markdown here")] = None,
) -> None:
    """Print the PLAN-9.5 §2 scorecard from an eval report, live metrics and the checks file."""
    from datetime import UTC, datetime  # noqa: PLC0415

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
    if checks and checks.is_file():
        sources.append(f"checks: {checks}")
    day = datetime.now(UTC).date().isoformat()
    card = build_scorecard(
        title=f"Rulecam 9.5 scorecard, {day}",
        report=loaded,
        checks=load_checks(checks) if checks and checks.is_file() else None,
        live=live,
        sources=sources,
    )
    text = render_markdown(card)
    typer.echo(text)
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        typer.echo(f"wrote {out}")


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


@app.callback()
def main() -> None:
    """Rulecam command line."""
