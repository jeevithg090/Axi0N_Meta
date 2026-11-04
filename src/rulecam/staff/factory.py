"""Builds the staff companion from settings and the services the API already has.

The app passes in what it built once (the response side with its notifier, escalation chains
and evidence chain; the Ask agent; hosted SAM 3.1; the *False alarm* sink), so the staff app
acts on the same alerts and chains as every other channel instead of running its own copies.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter

from rulecam.act.api import FalseAlarmSink
from rulecam.ask.agent import camera_names
from rulecam.bus import Bus
from rulecam.clients.errors import ClientConfigError
from rulecam.clients.meta_hosted import SparkClient, VoiceTranscribeClient
from rulecam.config import Settings
from rulecam.contracts import Clock
from rulecam.log import get_logger
from rulecam.replay.cache import ReplayCache
from rulecam.runtime.response import Response
from rulecam.shelf.gaps import ImageSegmenter, ShelfScanner
from rulecam.staff.api import StaffConfig, staff_router
from rulecam.staff.asking import Asker, VoiceAsk
from rulecam.staff.chainview import ChainView
from rulecam.staff.feed import StaffFeed
from rulecam.staff.models import LANGS
from rulecam.staff.signal import Signal
from rulecam.staff.speech import Speaker, default_engines
from rulecam.staff.store import StaffStore

_log = get_logger(__name__)


def plan_roles(plans_dir: Path) -> Callable[[], set[str]]:
    """Roles named by the notify and escalate actions of the site's plan files."""

    def read() -> set[str]:
        found: set[str] = set()
        for path in sorted(plans_dir.glob("*.plan.yaml")) if plans_dir.is_dir() else []:
            try:
                plan = yaml.safe_load(path.read_text("utf-8")) or {}
            except (OSError, yaml.YAMLError):
                continue
            rules = plan.get("rules") if isinstance(plan, dict) else None
            for rule in rules if isinstance(rules, list) else []:
                actions = rule.get("actions") if isinstance(rule, dict) else None
                for action in actions if isinstance(actions, list) else []:
                    role = action.get("role") if isinstance(action, dict) else None
                    if isinstance(role, str):
                        found.update(r.strip() for r in role.split(",") if r.strip())
        return found

    return read


def _spark(settings: Settings, *, cache: ReplayCache, clock: Clock, bus: Bus) -> SparkClient | None:
    try:
        return SparkClient.from_settings(settings, clock=clock, bus=bus, cache=cache)
    except ClientConfigError as exc:
        _log.warning("staff_without_spark", error=str(exc))
        return None


def _voice(
    settings: Settings, *, cache: ReplayCache, clock: Clock, bus: Bus
) -> VoiceTranscribeClient | None:
    try:
        return VoiceTranscribeClient.from_settings(settings, clock=clock, bus=bus, cache=cache)
    except ClientConfigError as exc:
        _log.warning("staff_without_voice", error=str(exc))
        return None


def build_staff(
    settings: Settings,
    *,
    cache: ReplayCache,
    clock: Clock,
    bus: Bus,
    response: Response,
    chain_root: Path,
    segmenter: ImageSegmenter | None,
    ask_agent: Asker | None,
    false_alarm: FalseAlarmSink | None,
    plans_dir: Path,
    near_miss_voice: bool = False,
    dependencies: Sequence[Any] = (),
) -> APIRouter:
    """The `/staff` router, wired to the site's alerts, chains, models and speech.

    Args:
        settings: Application settings.
        cache: Replay cache for the model clients.
        clock: Time source.
        bus: Event bus.
        response: The response side (notifier, escalation, evidence chain, dry run).
        chain_root: Root of the per-site chain folders.
        segmenter: Hosted SAM 3.1 for shelf scans (`None`: no shelf scans).
        ask_agent: The Ask agent (`None`: no push-to-talk Ask).
        false_alarm: Records *False alarm* taps as Rule CI regressions.
        plans_dir: The plan files (the roles they name).
        near_miss_voice: The API also serves the warehouse's voice near-miss reports.
        dependencies: Authentication for every route.

    Returns:
        The router.
    """
    data = settings.rulecam_data_dir
    staff_dir = data / "staff"
    names = camera_names(settings.rulecam_camera_names)
    feed = StaffFeed(
        notifier=response.notifier,
        escalation=response.escalation,
        chain=response.chain,
        view=ChainView(chain_root / response.chain.site, today=lambda: clock.now().date()),
        store=StaffStore(staff_dir / "state.jsonl"),
        clock=clock,
        bus=bus,
        signal=Signal(),
        media_roots=(data / "strips", data / "crowd", data / "shelf", staff_dir),
        camera_names=names,
        dry_run=response.status.actions_dry_run,
        on_false_alarm=false_alarm,
        timezone=settings.rulecam_site_timezone,
        plan_roles=plan_roles(plans_dir),
    )
    spark = _spark(settings, cache=cache, clock=clock, bus=bus)
    engines = default_engines(bus)
    speaker = Speaker(spark=spark, engines=engines, audio_dir=staff_dir / "audio")
    voice = _voice(settings, cache=cache, clock=clock, bus=bus)
    asker = (
        VoiceAsk(transcriber=voice, agent=ask_agent, speaker=speaker)
        if ask_agent is not None
        else None
    )
    shelf = (
        ShelfScanner(spark=spark, files=spark, sam=segmenter, out_dir=staff_dir / "shelf")
        if spark is not None and segmenter is not None
        else None
    )
    config = StaffConfig(
        langs=LANGS,
        dry_run=response.status.actions_dry_run,
        ask=asker is not None,
        listen=asker is not None and voice is not None,
        shelf=shelf is not None,
        speech=tuple(engine.name for engine, _ in engines),
        near_miss_voice=near_miss_voice,
    )
    return staff_router(
        feed,
        bus=bus,
        clock=clock,
        speaker=speaker,
        config=config,
        photos_dir=staff_dir / "photos",
        asker=asker,
        shelf=shelf,
        dependencies=dependencies,
    )
