"""Scene-text firewall with Llama Prompt Guard 2 (task P3-05, defences D1-D3).

Both verifier tiers copy any text they see in the frames into `scene_text`. The firewall scans it
with `meta-llama/Llama-Prompt-Guard-2-86M` (CPU, transformers). When the malicious score reaches
the threshold (`score >= threshold`; a tie is treated as an attack, the safe side):

1. the sign region is blurred (SAM concepts `sign` and `text`);
2. both tiers are re-run on the blurred strip;
3. if either tier now rejects the alert, it goes to `human_queue` (never suppressed);
4. a `tamper_attempt` record is appended to the evidence chain.

Planner intake text (P2-02) is scanned with `scan`. Built with Llama (Llama 4 Community License).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from rulecam.contracts import CandidateEvent, RuleIR, Verifier, VerifierVerdict
from rulecam.contracts.core import BoxXYXY
from rulecam.evidence.chain import EventChain
from rulecam.log import get_logger

_log = get_logger(__name__)

PROMPT_GUARD_MODEL = "meta-llama/Llama-Prompt-Guard-2-86M"
DEFAULT_THRESHOLD = 0.5
MALICIOUS_LABELS = frozenset({"LABEL_1", "MALICIOUS", "malicious"})
SIGN_CONCEPTS = ("sign", "text")
MAX_LOGGED_TEXT = 200


class TextClassifier(Protocol):
    """Scores text for prompt injection (`PromptGuard2`, or a fake)."""

    def malicious_score(self, text: str) -> float:
        """Return P(malicious) in [0, 1]."""
        ...


class StripBlurrer(Protocol):
    """Blurs sign and text regions in a candidate's strip and returns the new strip paths."""

    def blur(self, candidate: CandidateEvent) -> tuple[str, ...]:
        """Return paths of blurred copies of the strip frames."""
        ...


class ReVerifier(Protocol):
    """Tier 2 as the firewall re-runs it."""

    def verify(
        self, candidate: CandidateEvent, rule: RuleIR, tier1: VerifierVerdict | None = None
    ) -> VerifierVerdict:
        """Review a candidate."""
        ...


class PromptGuard2:
    """Llama Prompt Guard 2 (86M) on CPU via transformers (the `ml` extra; gated on HF)."""

    def __init__(self, model: str = PROMPT_GUARD_MODEL, token: str | None = None) -> None:
        """Load the classifier (downloads on first use).

        Args:
            model: Hugging Face model id.
            token: `HF_TOKEN` for the gated repo.
        """
        from transformers import pipeline  # noqa: PLC0415 - optional heavy dependency

        self._pipe = pipeline("text-classification", model=model, token=token, device="cpu")

    def malicious_score(self, text: str) -> float:
        """Return P(malicious) for `text` (empty text scores 0)."""
        if not text.strip():
            return 0.0
        result = self._pipe(text, truncation=True, top_k=None)
        scores = result[0] if result and isinstance(result[0], list) else result
        return max(
            (float(r["score"]) for r in scores if str(r["label"]) in MALICIOUS_LABELS),
            default=0.0,
        )


BoxFinder = Callable[[Path, str], Sequence[BoxXYXY]]


class BoxStripBlurrer:
    """Blurs the boxes a segmenter finds for `sign` and `text` in each strip frame (OpenCV)."""

    def __init__(self, find_boxes: BoxFinder, *, kernel: int = 51) -> None:
        """Create the blurrer.

        Args:
            find_boxes: `(frame, concept) -> boxes`, e.g. from `HostedSam31.segment_image`.
            kernel: Odd Gaussian kernel size in pixels.
        """
        self._find_boxes = find_boxes
        self._kernel = kernel | 1

    def blur(self, candidate: CandidateEvent) -> tuple[str, ...]:
        """Write `<frame>.blurred<ext>` beside each frame and return their paths."""
        import cv2  # noqa: PLC0415 - only needed when a sign is flagged

        out: list[str] = []
        for frame_path in map(Path, candidate.strip_paths):
            image = cv2.imread(str(frame_path))
            if image is None:
                msg = f"cannot read strip frame {frame_path}"
                raise OSError(msg)
            for concept in SIGN_CONCEPTS:
                for x1, y1, x2, y2 in self._find_boxes(frame_path, concept):
                    xa, ya, xb, yb = (max(0, int(v)) for v in (x1, y1, x2, y2))
                    region = image[ya:yb, xa:xb]
                    if region.size:
                        image[ya:yb, xa:xb] = cv2.GaussianBlur(region, (self._kernel,) * 2, 0)
            target = frame_path.with_name(f"{frame_path.stem}.blurred{frame_path.suffix}")
            cv2.imwrite(str(target), image)
            out.append(str(target))
        return tuple(out)


@dataclass(frozen=True)
class Screened:
    """The firewall's result.

    Attributes:
        flagged: Whether scene text scored as an injection attempt.
        tier1: Tier 1 verdict to use (re-run on the blurred strip when flagged).
        tier2: Tier 2 verdict to use, if Tier 2 ran.
        force_human: Flagged and a tier rejected the alert after blurring: a person decides.
        score: The highest malicious score seen.
    """

    flagged: bool
    tier1: VerifierVerdict
    tier2: VerifierVerdict | None
    force_human: bool = False
    score: float = 0.0


class SceneTextFirewall:
    """Scans scene text, blurs and re-verifies on injection attempts, and logs them."""

    def __init__(
        self,
        *,
        classifier: TextClassifier,
        blurrer: StripBlurrer,
        tier1: Verifier,
        chain: EventChain,
        tier2: ReVerifier | None = None,
        threshold: float = DEFAULT_THRESHOLD,
    ) -> None:
        """Create the firewall.

        Args:
            classifier: Prompt Guard 2 (or a fake).
            blurrer: Blurs sign regions.
            tier1: Glimmer verifier, re-run on the blurred strip.
            chain: Evidence chain for `tamper_attempt` records.
            tier2: Spark Open verifier, re-run when it ran the first time.
            threshold: Malicious score at or above which text is treated as an attack (a score
                exactly at the threshold is flagged, which errs towards scrutiny).
        """
        self._classifier = classifier
        self._blurrer = blurrer
        self._tier1 = tier1
        self._tier2 = tier2
        self._chain = chain
        self._threshold = threshold

    def scan(self, text: str) -> float:
        """Return the malicious score of any text (scene text or planner intake)."""
        return self._classifier.malicious_score(text)

    def screen(
        self,
        candidate: CandidateEvent,
        rule: RuleIR,
        tier1: VerifierVerdict,
        tier2: VerifierVerdict | None = None,
    ) -> Screened:
        """Check both tiers' scene text and handle an injection attempt.

        Args:
            candidate: The candidate whose strip was verified.
            rule: Its rule.
            tier1: Glimmer's verdict.
            tier2: Spark Open's verdict, if it ran.

        Returns:
            Unchanged verdicts when nothing is flagged; otherwise re-run, tamper-marked verdicts
            and whether a person must decide.
        """
        texts = [v.scene_text for v in (tier1, tier2) if v is not None and v.scene_text]
        score = max((self.scan(t) for t in texts), default=0.0)
        if score < self._threshold:  # flagged at or above the threshold (ties are attacks)
            return Screened(flagged=False, tier1=tier1, tier2=tier2, score=score)
        blurred = candidate.model_copy(update={"strip_paths": self._blurrer.blur(candidate)})
        new1 = self._tier1.verify(blurred, rule).model_copy(update={"tamper": True})
        new2 = None
        if tier2 is not None and self._tier2 is not None:
            new2 = self._tier2.verify(blurred, rule, new1).model_copy(update={"tamper": True})
        force_human = any(v.verdict == "no_violation" for v in (new1, new2) if v is not None)
        self._chain.append(
            "tamper_attempt",
            {
                "event_id": candidate.event_id,
                "rule_id": rule.id,
                "score": round(score, 4),
                "scene_text": " | ".join(texts)[:MAX_LOGGED_TEXT],
                "force_human": force_human,
            },
        )
        _log.warning("tamper_attempt", event_id=candidate.event_id, score=round(score, 3))
        return Screened(flagged=True, tier1=new1, tier2=new2, force_human=force_human, score=score)
