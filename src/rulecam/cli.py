"""The `rulecam` command line. Feature packages register sub-commands here as they land."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import date
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


@app.callback()
def main() -> None:
    """Rulecam command line."""
