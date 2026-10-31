"""HTML and PNG rendering for Rule Cards and Declined Cards (task P2-10)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

import jinja2

from rulecam.cards.models import DeclinedCardData, RuleCardData
from rulecam.cards.templates import DECLINED_CARD_TEMPLATE, RULE_CARD_TEMPLATE
from rulecam.log import get_logger

_log = get_logger(__name__)

# Banned personal name / identifiable tokens guardrail check
BANNED_NAME_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\bJohn\b", re.IGNORECASE),
    re.compile(r"\bAlice\b", re.IGNORECASE),
    re.compile(r"\bBob\b", re.IGNORECASE),
    re.compile(r"\bMaria\b", re.IGNORECASE),
    re.compile(r"\bMaría\b", re.IGNORECASE),
    re.compile(r"\bCarlos\b", re.IGNORECASE),
    re.compile(r"\bemployee #\d+\b", re.IGNORECASE),
    re.compile(r"\bworker #\d+\b", re.IGNORECASE),
)

_jinja_env = jinja2.Environment(
    autoescape=True,
    undefined=jinja2.StrictUndefined,
)
_rule_template = _jinja_env.from_string(RULE_CARD_TEMPLATE)
_declined_template = _jinja_env.from_string(DECLINED_CARD_TEMPLATE)


def render_rule_card_html(card: RuleCardData) -> str:
    """Render a verified Rule Card to HTML string at 1080x1350 format."""
    return _rule_template.render(card=card)


def render_declined_card_html(
    card: DeclinedCardData,
    *,
    forbidden_tokens: tuple[str, ...] = (),
) -> str:
    """Render a Declined Request Card to HTML string.

    Strict Privacy Guardrail:
    Asserts that no individual names, track IDs, or forbidden request text appear.

    Args:
        card: DeclinedCardData containing category, reason, alternative.
        forbidden_tokens: Specific raw request substrings or worker names to assert absent.

    Returns:
        Rendered HTML string.

    Raises:
        ValueError: If any forbidden token or person name leaks into the rendered card.
    """
    html = _declined_template.render(card=card)

    # Check for forbidden names or tokens
    for token in forbidden_tokens:
        if token.strip() and token.lower() in html.lower():
            msg = (
                f"Guardrail breach: raw request token or name {token!r} "
                "leaked into declined card HTML!"
            )
            _log.error("declined_card_privacy_leak", token=token)
            raise ValueError(msg)

    # Check for common personal names in reason/alternative
    for pattern in BANNED_NAME_PATTERNS:
        match = pattern.search(html)
        if match:
            msg = (
                f"Guardrail breach: personal identifier '{match.group(0)}' "
                "found in declined card HTML!"
            )
            _log.error("declined_card_name_leak", matched=match.group(0))
            raise ValueError(msg)

    return html


async def render_card_png(
    html: str,
    output_path: Path,
    *,
    width: int = 1080,
    height: int = 1350,
) -> Path | None:
    """Render HTML card to PNG at 1080x1350 via Playwright.

    If Playwright is not installed in the environment, gracefully logs and returns None.

    Args:
        html: Full HTML document string.
        output_path: File path to save output PNG.
        width: Viewport width in pixels.
        height: Viewport height in pixels.

    Returns:
        The output Path if rendering succeeded, or None if Playwright is unavailable.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        from playwright.async_api import async_playwright  # noqa: PLC0415
    except ImportError:
        _log.info("playwright_not_installed_skipping_png_render", path=str(output_path))
        return None

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": width, "height": height})
        await page.set_content(html)
        await page.screenshot(path=str(output_path))
        await browser.close()

    _log.info("rendered_card_png", path=str(output_path))
    return output_path
