"""The `rulecam` command line. Feature packages register sub-commands here as they land."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from rulecam.bus import Bus
from rulecam.capture import Chunker
from rulecam.clock import SystemClock
from rulecam.contracts import CONTRACTS_VERSION

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


@app.callback()
def main() -> None:
    """Rulecam command line."""
