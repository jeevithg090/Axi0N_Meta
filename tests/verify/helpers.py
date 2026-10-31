"""Shared builders for verification tests."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path

import yaml

from rulecam.contracts import CandidateEvent, Measurement, RuleIR, Severity
from tests.fakes import EPOCH

RULES = Path(__file__).parent.parent / "fixtures" / "rules"
PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)


def load_rule(name: str = "wash-before-line") -> RuleIR:
    return RuleIR.model_validate(yaml.safe_load((RULES / f"{name}.rule.yaml").read_text()))


def make_strip(directory: Path, n: int = 6) -> tuple[str, ...]:
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(n):
        path = directory / f"f{i}.png"
        path.write_bytes(PNG_1PX)
        paths.append(str(path))
    return tuple(paths)


def make_candidate(
    strip: tuple[str, ...] = (), severity: Severity = Severity.MEDIUM, event_id: str = "ev-1"
) -> CandidateEvent:
    return CandidateEvent(
        event_id=event_id,
        rule_id="wash-before-line",
        camera_id="cam1",
        ts_start=EPOCH,
        ts_end=EPOCH + timedelta(seconds=8),
        severity=severity,
        measurement=Measurement(value=8.0, unit="s"),
        predicate_trace={"dwell_s": 8.0, "required_s": 20.0},
        strip_paths=strip,
        track_window_hash="t" * 64,
        plan_version_hash="p" * 64,
        rule_ir_hash="r" * 64,
    )


def chat_reply(content: object, status: int = 200) -> Mapping[str, object]:
    text = content if isinstance(content, str) else json.dumps(content)
    return {
        "status": status,
        "headers": {},
        "json": {"choices": [{"message": {"role": "assistant", "content": text}}]},
    }
