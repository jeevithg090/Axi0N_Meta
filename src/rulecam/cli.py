"""The `rulecam` command line. Feature packages register sub-commands here as they land."""

from __future__ import annotations

import typer

from rulecam.contracts import CONTRACTS_VERSION

app = typer.Typer(help="Rulecam: a camera agent that plans its own job and proves its work.")


@app.command()
def version() -> None:
    """Show the package and contracts versions."""
    from importlib.metadata import version as pkg_version  # noqa: PLC0415 - fast CLI startup

    typer.echo(f"rulecam {pkg_version('rulecam')} (contracts {CONTRACTS_VERSION})")


@app.callback()
def main() -> None:
    """Rulecam command line."""
