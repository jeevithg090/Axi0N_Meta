"""Evidence packs for inspectors and surveyors (task P3-07b, MP §3.4, §3.5).

A pack maps **clause → rule → compliance rate (Wilson 95% CI) → watched fraction →
anonymised thumbnails → corrective actions**, and ends with a QR code to the verify page. Three
templates:

- `food_code`: leads with Food Code §2-103.11(G) (the person in charge monitors handwashing) and
  §2-301.12 (how to wash);
- `f689`: the nursing-home supervision log (accidents and supervision);
- `osha_pit`: forklifts (OSHA 29 CFR 1910.178, powered industrial trucks): leads with
  1910.178(l)(4)(ii)(B) (refresher training after a near miss), then (m)(1) (never drive up to
  a person standing in front of a fixed object) and (n)(4) (slow down and sound the horn where
  vision is obstructed);
- `generic`.

**Privacy:** packs never contain clip or file paths: stills are inlined as data URIs. In anonymous
and strict modes only skeleton thumbnails are kept; raw frames are never allowed in any mode. HTML
is rendered with Jinja2 (auto-escaped) and printed to PDF with Playwright (the `ui` extra).
"""

from __future__ import annotations

import base64
import io
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

import qrcode
from jinja2 import Environment, PackageLoader, select_autoescape
from pydantic import Field

from rulecam.contracts import Frozen, PrivacyMode

TemplateName = Literal["food_code", "f689", "osha_pit", "generic"]
Z_95 = 1.959963984540054
IMAGE_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}

LEAD_CLAUSES: dict[str, tuple[str, ...]] = {
    "food_code": ("§2-103.11(G)", "§2-301.12"),
    "f689": ("F689",),
    "osha_pit": ("1910.178(l)(4)(ii)(B)", "1910.178(m)(1)", "1910.178(n)(4)"),
    "generic": (),
}
TITLES: dict[str, str] = {
    "food_code": "Food Code evidence pack",
    "f689": "F689 supervision log",
    "osha_pit": "Forklift (powered industrial truck) evidence pack",
    "generic": "Rulecam evidence pack",
}
INTROS: dict[str, str] = {
    "food_code": (
        "Food Code 2026 §2-103.11(G) requires the person in charge to routinely monitor employee "
        "handwashing. This pack shows what the cameras checked for §2-301.12 and how much of each "
        "shift they actually watched."
    ),
    "f689": (
        "Supervision evidence for CMS F689 (accidents and supervision) on this unit: exit coverage "
        "and response to alerts, by corridor and role. It never covers individual residents or "
        "staff."
    ),
    "osha_pit": (
        "OSHA 29 CFR 1910.178(l)(4)(ii)(B) requires refresher training for an operator involved "
        "in an accident or near-miss incident. This pack shows every forklift near miss the "
        "cameras measured, the training each one made due and whether it was done, and how much "
        "of each shift the cameras watched. Vehicles are named by their unit numbers, never by a "
        "person."
    ),
    "generic": "Rule compliance measured by Rulecam, with the share of time each camera watched.",
}


def wilson_interval(passes: int, n: int, z: float = Z_95) -> tuple[float, float]:
    """Return the Wilson score interval for `passes` out of `n` (0..1; `(0, 1)` when `n == 0`).

    Raises:
        ValueError: If counts are negative or `passes > n`.
    """
    if n < 0 or passes < 0 or passes > n:
        msg = f"invalid counts {passes}/{n}"
        raise ValueError(msg)
    if n == 0:
        return 0.0, 1.0
    p = passes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


class Thumbnail(Frozen):
    """One still in the pack: `skeleton` (anonymised render) or `masked` (faces masked)."""

    path: str
    kind: Literal["skeleton", "masked", "raw"]


class PackRow(Frozen):
    """One clause and the rule that checks it."""

    clause: str
    rule_id: str
    rule_text: str
    checks: int = Field(ge=0)
    passes: int = Field(ge=0)
    watched_fraction: float = Field(ge=0.0, le=1.0)
    thumbnails: tuple[Thumbnail, ...] = ()
    corrective_actions: tuple[str, ...] = ()


class EvidencePack(Frozen):
    """Everything a pack shows."""

    template: TemplateName
    site: str
    period: str
    privacy_mode: PrivacyMode
    rows: tuple[PackRow, ...]
    verify_url: str
    chain_records: int = Field(ge=0)
    chain_ok: bool


def allowed_thumbnails(thumbnails: Sequence[Thumbnail], mode: PrivacyMode) -> tuple[Thumbnail, ...]:
    """Drop every thumbnail the privacy mode forbids (raw frames are never allowed)."""
    allowed = {"skeleton"} if mode is not PrivacyMode.STANDARD else {"skeleton", "masked"}
    return tuple(t for t in thumbnails if t.kind in allowed)


def ordered_rows(pack: EvidencePack) -> list[PackRow]:
    """Rows with the template's lead clauses first, then the rest in their given order."""
    leads = LEAD_CLAUSES[pack.template]

    def rank(row: PackRow) -> int:
        return next((i for i, c in enumerate(leads) if c in row.clause), len(leads))

    return sorted(pack.rows, key=rank)


def qr_data_uri(url: str) -> str:
    """Return a PNG QR code for `url` as a data URI."""
    image = qrcode.make(url)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def image_data_uri(path: Path) -> str:
    """Inline a still as a data URI, so a pack is self-contained and names no file on disk.

    Playwright prints from `about:blank`, where Chromium refuses `file://` images, and a shared
    pack must not reveal where the server keeps its renders.

    Raises:
        ValueError: If the file is not a PNG, JPEG or WebP image.
        FileNotFoundError: If the file does not exist.
    """
    mime = IMAGE_TYPES.get(path.suffix.lower())
    if mime is None:
        msg = f"thumbnail {path.name!r} is not a PNG, JPEG or WebP image"
        raise ValueError(msg)
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def _row_view(row: PackRow, mode: PrivacyMode) -> dict[str, object]:
    lo, hi = wilson_interval(row.passes, row.checks)
    rate = row.passes / row.checks if row.checks else None
    return {
        "clause": row.clause,
        "rule_id": row.rule_id,
        "rule_text": row.rule_text,
        "checks": row.checks,
        "rate": None if rate is None else f"{rate:.0%}",
        "ci": f"{lo:.0%} to {hi:.0%}",
        "watched": f"{row.watched_fraction:.0%}",
        "thumbnails": [
            image_data_uri(Path(t.path)) for t in allowed_thumbnails(row.thumbnails, mode)
        ],
        "actions": list(row.corrective_actions),
    }


_env = Environment(
    loader=PackageLoader("rulecam.evidence", "templates"),
    autoescape=select_autoescape(["html", "j2"]),
    keep_trailing_newline=True,
)


def render_template(name: str, **context: object) -> str:
    """Render one of this package's auto-escaping templates (`templates/<name>`)."""
    return _env.get_template(name).render(**context)


def render_html(pack: EvidencePack) -> str:
    """Render the pack as a self-contained HTML page.

    Args:
        pack: The pack contents.

    Returns:
        HTML (auto-escaped) with the QR code inlined.
    """
    template = _env.get_template("pack.html.j2")
    return template.render(
        title=TITLES[pack.template],
        intro=INTROS[pack.template],
        pack=pack,
        rows=[_row_view(r, pack.privacy_mode) for r in ordered_rows(pack)],
        qr=qr_data_uri(pack.verify_url),
    )


def render_pdf(html: str, out: Path) -> Path:  # pragma: no cover - needs Playwright browsers
    """Print HTML to PDF with headless Chromium (install with `uv sync --extra ui`).

    Args:
        html: The rendered pack.
        out: Destination PDF.

    Returns:
        `out`.
    """
    from playwright.sync_api import sync_playwright  # noqa: PLC0415 - optional dependency

    out.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.set_content(html, wait_until="load")
        page.pdf(path=str(out), format="A4", print_background=True)
        browser.close()
    return out
