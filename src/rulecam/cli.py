"""The `rulecam` command line. Feature packages register sub-commands here as they land."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from rulecam.bus import Bus
from rulecam.capture import Chunker
from rulecam.clock import SystemClock
from rulecam.config import get_settings
from rulecam.contracts import CONTRACTS_VERSION
from rulecam.replay.cache import ReplayCache
from rulecam.replay.golden import GoldenRunner

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


@app.callback()
def main() -> None:
    """Rulecam command line."""
