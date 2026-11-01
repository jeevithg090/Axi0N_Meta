"""The runtime's response side (WS1b; tasks P3-05 and P3-06), built from settings.

`build_response` wires the merged P3-05/P3-06 modules for the runtime loop and the API:

- **Policy.** `VerificationPolicy`: HIGH acts first and a verifier veto demotes it to the human
  queue (never dropped); LOW and MEDIUM verify first; Tier 2 (Spark Open on Baseten) reviews
  what Tier 1 did not confirm when the rule allows it, and every dispute. Tier 2 is configured
  when `BASETEN_API_KEY` is set (or in replay mode, where recorded reviews replay). Without it
  the policy decides on Tier 1 alone, unresolved candidates go to the human queue and disputes
  stay pending for a person (`TIER2_ABSENT`): never a silent pass.
- **Tier 1 failures.** The Tier 1 verifier is a `verify.guard.GuardedVerifier` (retry, circuit
  breaker): a candidate it cannot verify is recorded with a `verifier_error` verdict and the
  policy sends it to the human queue (Tier 2 may confirm it first). The API's ticker runs its
  health probe while the circuit is open.
- **Scene-text firewall.** `SceneTextFirewall` with Llama Prompt Guard 2 when `HF_TOKEN` is set
  and the `ml` extra (transformers) is installed; the model loads at start-up. Otherwise the
  firewall is **degraded**: logged once (`firewall_degraded`), shown in `/runtime/metrics`
  (`response.firewall`), and the policy sends any candidate it would suppress on verdicts that
  report scene text to a person instead, because that text could not be screened.
- **Actions.** `ActionExecutor` (interlock first, then notify/escalate, then the slow outputs)
  with the `LanNotifier` (alerts on `/ws` and `GET /alerts`), the `EscalationManager` (the API
  ticks it once a second) and, when `KASA_PLUG_HOST` is set, the Kasa interlock, connected at
  start-up so the first cut is one round trip. `KASA_LIGHT_HOST` adds the strobe light.
- **Dry run.** `RULECAM_ACTIONS_DRY_RUN` (default **on**, until the owner turns it off) records
  every action as `dry-run: would …` in the chain and runs none of them.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from fastapi import APIRouter, params

from rulecam.act import ActionExecutor, EscalationManager, Interlock, LanNotifier, alerts_router
from rulecam.act.devices import Connectable, KasaPlug, Light, StrobeLight
from rulecam.act.errors import DeviceError
from rulecam.bus import Bus
from rulecam.clients.open_weights.spark_open import SparkOpenClient
from rulecam.config import Settings
from rulecam.contracts import Clock, Detection, Frozen, Plug, Verifier
from rulecam.contracts.core import BoxXYXY
from rulecam.evidence.chain import EventChain
from rulecam.log import get_logger
from rulecam.replay.cache import ReplayCache
from rulecam.verify.firewall import (
    BoxStripBlurrer,
    PromptGuard2,
    SceneTextFirewall,
    TextClassifier,
)
from rulecam.verify.guard import GuardedVerifier
from rulecam.verify.policy import Tier2, VerificationPolicy
from rulecam.verify.tier2 import SparkOpenVerifier

_log = get_logger(__name__)

INTERLOCK_DEVICE = "plug"
FIREWALL_CAMERA_ID = "firewall"
TIER2_ABSENT = (
    "Tier 1 decides alone: a confirmed violation alerts, a confident rejection is suppressed, "
    "anything unsure goes to the human queue; disputes stay pending for a person"
)

ClassifierFactory = Callable[[str], TextClassifier]
PlugFactory = Callable[[str], Plug]


class ImageSegmenter(Protocol):
    """Finds a concept in one image (`HostedSam31.segment_image`), for blurring signs."""

    def segment_image(self, frame: Path, concept: str, *, camera_id: str) -> list[Detection]:
        """Return the concept's detections in `frame`."""
        ...


class ResponseStatus(Frozen):
    """How the response side is configured, served in `/runtime/metrics` (`response`).

    Attributes:
        tier2: Whether Spark Open reviews disagreements and disputes.
        tier2_absent: What happens instead when it does not (empty when configured).
        firewall: `on` (Prompt Guard 2 loaded) or `degraded`.
        firewall_reason: Why the firewall is degraded.
        actions_dry_run: Actions are recorded as `dry-run: would …` and never executed.
        interlock_devices: Configured interlock plugs.
        interlock_unreachable: Plugs that did not connect at start-up (retried on first use).
        light: Whether a strobe light is configured.
        notifier: Where notifications go.
    """

    tier2: Literal["configured", "absent"]
    tier2_absent: str = ""
    firewall: Literal["on", "degraded"]
    firewall_reason: str = ""
    actions_dry_run: bool
    interlock_devices: tuple[str, ...] = ()
    interlock_unreachable: tuple[str, ...] = ()
    light: bool = False
    notifier: Literal["lan"] = "lan"


@dataclass(frozen=True)
class Response:
    """The response services shared by the runtime loop and the API.

    Attributes:
        tier1: The Tier 1 verifier the loop calls (and the firewall re-runs).
        policy: The safety-asymmetric policy (also the dispute reviewer).
        actions: The action runner.
        notifier: LAN alerts.
        escalation: Escalation chains (`tick` once a second).
        interlock: The smart-plug interlock, when configured.
        status: What is on, off or degraded.
        chain: The evidence chain (false-alarm labels from the alert buttons).
    """

    tier1: Verifier
    policy: VerificationPolicy
    actions: ActionExecutor
    notifier: LanNotifier
    escalation: EscalationManager
    interlock: Interlock | None
    status: ResponseStatus
    chain: EventChain

    @property
    def tickers(self) -> tuple[Callable[[], object], ...]:
        """What the API calls once a second: the escalation timers, and Tier 1's health probe."""
        if isinstance(self.tier1, GuardedVerifier):
            return (self.escalation.tick, self.tier1.tick)
        return (self.escalation.tick,)

    def router(
        self,
        roles: Collection[str] | None = None,
        *,
        dependencies: Sequence[params.Depends],
    ) -> APIRouter:
        """`/alerts`, `/ack` and `/interlock/{device}/resume` (P3-06), behind `dependencies`."""
        return alerts_router(
            notifier=self.notifier,
            escalation=self.escalation,
            chain=self.chain,
            interlock=self.interlock,
            roles=roles,
            dependencies=dependencies,
        )


def prompt_guard(token: str) -> TextClassifier:
    """Load Llama Prompt Guard 2 (86M, CPU) with `HF_TOKEN` (raises `ImportError` without ml)."""
    return PromptGuard2(token=token)


def sign_boxes(segmenter: ImageSegmenter) -> Callable[[Path, str], Sequence[BoxXYXY]]:
    """The firewall's box finder: SAM boxes of `sign` / `text` in one strip frame."""

    def find(frame: Path, concept: str) -> Sequence[BoxXYXY]:
        found = segmenter.segment_image(frame, concept, camera_id=FIREWALL_CAMERA_ID)
        return [d.mask.box_xyxy for d in found]

    return find


def tier2_verifier(
    settings: Settings, *, cache: ReplayCache, clock: Clock, bus: Bus
) -> Tier2 | None:
    """Spark Open on Baseten when a key is set (or recorded reviews can replay), else `None`."""
    if settings.baseten_api_key is None and settings.rulecam_mode != "replay":
        return None
    client = SparkOpenClient.from_settings(settings, cache=cache, clock=clock, bus=bus)
    return SparkOpenVerifier(client)


def build_firewall(
    settings: Settings,
    *,
    tier1: Verifier,
    tier2: Tier2 | None,
    chain: EventChain,
    segmenter: ImageSegmenter,
    classifier: ClassifierFactory = prompt_guard,
) -> tuple[SceneTextFirewall | None, str]:
    """Build the scene-text firewall, or say why it is degraded.

    Args:
        settings: Application settings (`HF_TOKEN`).
        tier1: Re-run on the blurred strip.
        tier2: Re-run when it ran the first time.
        chain: For `tamper_attempt` records.
        segmenter: Finds the sign regions to blur.
        classifier: Loads Prompt Guard 2 from the token (a fake in tests).

    Returns:
        The firewall and `""`, or `None` and the reason it is degraded.
    """
    if settings.hf_token is None:
        return None, "HF_TOKEN is not set (Prompt Guard 2 is gated on Hugging Face)"
    try:
        loaded = classifier(settings.hf_token.get_secret_value())
    except ImportError:
        return None, "the ml extra (transformers) is not installed"
    except Exception as exc:  # downloads and model loading raise many types
        return None, f"Prompt Guard 2 did not load ({type(exc).__name__})"
    firewall = SceneTextFirewall(
        classifier=loaded,
        blurrer=BoxStripBlurrer(sign_boxes(segmenter)),
        tier1=tier1,
        tier2=tier2,
        chain=chain,
    )
    return firewall, ""


def connect_devices(interlock: Interlock | None, light_plug: Plug | None) -> tuple[str, ...]:
    """Connect the interlock plugs and the light's plug now (the P3-06 start-up connect).

    Returns:
        Devices that did not connect (logged; each is retried on first use).
    """
    failed = list(interlock.connect()) if interlock is not None else []
    if isinstance(light_plug, Connectable):
        try:
            light_plug.connect()
        except DeviceError as exc:
            failed.append("light")
            _log.warning("light_connect_failed", error=str(exc))
    return tuple(failed)


def build_response(
    settings: Settings,
    *,
    tier1: Verifier,
    segmenter: ImageSegmenter,
    cache: ReplayCache,
    clock: Clock,
    bus: Bus,
    chain: EventChain,
    classifier: ClassifierFactory = prompt_guard,
    plug: PlugFactory = KasaPlug,
    tier2: Tier2 | None = None,
) -> Response:
    """Build the policy, firewall, actions, notifier, escalation and interlock from settings.

    Args:
        settings: Application settings.
        tier1: The Tier 1 verifier (Glimmer).
        segmenter: Hosted SAM for the firewall's sign blur.
        cache: Replay cache for Tier 2.
        clock: Time source.
        bus: Event bus (alerts, action records).
        chain: Evidence chain.
        classifier: Loads Prompt Guard 2 (a fake in tests).
        plug: Builds a smart plug from a host (`KasaPlug`; a fake in tests).
        tier2: Tier 2 to use instead of the one built from settings (tests).

    Returns:
        The response services; their status is logged once.
    """
    tier2 = tier2 or tier2_verifier(settings, cache=cache, clock=clock, bus=bus)
    firewall, reason = build_firewall(
        settings, tier1=tier1, tier2=tier2, chain=chain, segmenter=segmenter, classifier=classifier
    )
    policy = VerificationPolicy(tier1=tier1, tier2=tier2, firewall=firewall, screen_required=True)
    notifier = LanNotifier(bus=bus, clock=clock)
    escalation = EscalationManager(notifier=notifier, clock=clock, chain=chain, bus=bus)
    interlock = None
    if settings.kasa_plug_host:
        plugs = {INTERLOCK_DEVICE: plug(settings.kasa_plug_host)}
        interlock = Interlock(plugs, clock=clock, chain=chain, bus=bus)
    light_plug = plug(settings.kasa_light_host) if settings.kasa_light_host else None
    light: Light | None = StrobeLight(light_plug) if light_plug is not None else None
    unreachable = connect_devices(interlock, light_plug)
    dry_run = settings.rulecam_actions_dry_run
    actions = ActionExecutor(
        clock=clock,
        notifier=notifier,
        escalation=escalation,
        interlock=interlock,
        light=light,
        bus=bus,
        dry_run=dry_run,
    )
    status = ResponseStatus(
        tier2="configured" if tier2 is not None else "absent",
        tier2_absent="" if tier2 is not None else TIER2_ABSENT,
        firewall="on" if firewall is not None else "degraded",
        firewall_reason=reason,
        actions_dry_run=dry_run,
        interlock_devices=interlock.devices if interlock is not None else (),
        interlock_unreachable=unreachable,
        light=light is not None,
    )
    _log_status(status)
    return Response(
        tier1=tier1,
        policy=policy,
        actions=actions,
        notifier=notifier,
        escalation=escalation,
        interlock=interlock,
        status=status,
        chain=chain,
    )


def _log_status(status: ResponseStatus) -> None:
    if status.tier2 == "absent":
        _log.warning("tier2_absent", behaviour=status.tier2_absent)
    if status.firewall == "degraded":
        _log.warning(
            "firewall_degraded",
            reason=status.firewall_reason,
            behaviour="unscreened scene text never suppresses an alert; a person reviews it",
        )
    _log.info(
        "response_built",
        tier2=status.tier2,
        firewall=status.firewall,
        actions_dry_run=status.actions_dry_run,
        interlock=list(status.interlock_devices),
        unreachable=list(status.interlock_unreachable),
    )
