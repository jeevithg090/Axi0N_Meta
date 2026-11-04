"""The connected warehouse story of one plan, step by step, from what the system recorded.

Each step reads its status from records that already exist (the plan, its acceptance trace,
the evidence chain, the regression store, the talks folder); nothing is inferred or simulated.
A step that needs a person (approve, acknowledge, review a dispute) is `waiting` until the
record exists. The steps, in demo order:

1. request: the owner's words (typed or spoken, any language);
2. plan: the planner's rules and what it says it cannot check;
3. rule_ci: the rule's Rule CI result (boundary traces, mutants, scenes, look-alikes);
4. approve: a person approved it and the runtime runs it;
5. violation: a near miss on a live camera (candidate on the chain);
6. verdict: the verifier's verdict on the frames (an operator on the vehicle is excluded by
   the engine, and a seated driver is rejected by the verifier);
7. warning: the actions (spoken warning, phone alert, interlock) and the spoken warning in the
   worker's language;
8. ack: someone acknowledged the alert;
9. evidence: the incident is on an intact hash chain with a report and a verify page;
10. toolbox_talk: Spark's two-minute talk in the worker's language;
11. dispute: a worker disputed an alert;
12. regression: an upheld dispute or a false-alarm tap became a permanent Rule CI regression.
"""

from __future__ import annotations

from collections.abc import Sequence

from rulecam.contracts import SitePlan
from rulecam.evidence.chain import ChainReport
from rulecam.warehouse.models import (
    CantCheck,
    NearMiss,
    SpokenWarning,
    StoryStep,
    ToolboxTalk,
    WarehouseStory,
)

CI_CHECK = "ci_green"
NOT_CHECKABLE = frozenset({"caveat", "not_observable", "declined"})


def request_text(plan: SitePlan) -> tuple[str, str | None]:
    """The owner's words and language: the first text item, else a transcript."""
    for item in plan.request:
        if item.text:
            return item.text, item.lang
        if item.extracted:
            return " ".join(item.extracted), item.lang
    return "", None


def cant_check(plan: SitePlan) -> tuple[CantCheck, ...]:
    """What the planner said it cannot check, or only with a caveat, in its own words."""
    return tuple(
        CantCheck(concern=v.concern, status=v.verdict, explanation=v.says or (v.reason or ""))
        for v in plan.feasibility
        if v.verdict in NOT_CHECKABLE
    )


def rule_ci_detail(plan: SitePlan) -> tuple[bool | None, str]:
    """Whether every rule's Rule CI passed, and the checks' details joined."""
    checks = [c for c in plan.acceptance.checks if c.check == CI_CHECK]
    if not checks:
        return None, ""
    details = "; ".join(f"{c.item}: {c.detail}" if c.detail else c.item for c in checks)
    return all(c.passed for c in checks), details


def followed_incident(near: Sequence[NearMiss]) -> NearMiss | None:
    """The incident to follow: the newest alerted near miss, else the newest one."""
    return next((n for n in near if n.alert), near[0] if near else None)


def _step(key: str, title: str, status: str, detail: str, **refs: str) -> StoryStep:
    return StoryStep.model_validate(
        {"key": key, "title": title, "status": status, "detail": detail, "refs": refs}
    )


def build_story(
    plan: SitePlan | None,
    *,
    approved: bool,
    active: bool,
    near: Sequence[NearMiss],
    chain: ChainReport | None,
    talk: ToolboxTalk | None = None,
    warning: SpokenWarning | None = None,
    regressions: int = 0,
) -> WarehouseStory:
    """Build one plan's story.

    Args:
        plan: The plan, `None` before any request.
        approved: Whether a person approved it.
        active: Whether the runtime runs it now.
        near: The day's near misses of its rules, newest first.
        chain: The day's chain verification, if the day has a chain.
        talk: The toolbox talk of the followed incident, if written.
        warning: The spoken warning of the followed incident, if made.
        regressions: Hard-negative regressions stored for its rules.

    Returns:
        The story.
    """
    if plan is None:
        first = _step("request", "Say or type the rule", "not_yet", "No request yet.")
        return WarehouseStory(
            plan_id=None,
            approved=False,
            request="",
            request_lang=None,
            rules=(),
            cant_check=(),
            event_id=None,
            steps=(first,),
            near_misses=tuple(near),
        )
    text, lang = request_text(plan)
    followed = followed_incident(near)
    steps = [
        _step("request", "Say or type the rule", "done", text or "(no text)", lang=lang or ""),
        _plan_step(plan),
        _ci_step(plan),
        _step(
            "approve",
            "A person approves; the camera starts checking",
            "done" if approved and active else ("waiting" if not approved else "failed"),
            "Approved and running."
            if approved and active
            else (
                "Waiting for a person to approve."
                if not approved
                else "Approved, but the runtime is not running it."
            ),
            plan_id=plan.plan_id,
        ),
        *_incident_steps(
            followed,
            chain=chain,
            talk=talk,
            warning=warning,
            regressions=regressions,
            near=near,
        ),
    ]
    return WarehouseStory(
        plan_id=plan.plan_id,
        approved=approved,
        request=text,
        request_lang=lang,
        rules=tuple({"id": r.id, "severity": r.severity.value} for r in plan.rules),
        cant_check=cant_check(plan),
        event_id=followed.event_id if followed else None,
        steps=tuple(steps),
        near_misses=tuple(near),
    )


def _plan_step(plan: SitePlan) -> StoryStep:
    limits = cant_check(plan)
    honest = (
        f" It says it cannot fully check {len(limits)} thing(s): "
        + "; ".join(f"{c.concern} ({c.status})" for c in limits)
        if limits
        else " It says it can check everything asked."
    )
    return _step(
        "plan",
        "The planner writes the rule and says what it cannot check",
        "done" if plan.rules else "failed",
        f"{len(plan.rules)} rule(s): " + ", ".join(r.id for r in plan.rules) + "." + honest,
        plan_id=plan.plan_id,
    )


def _ci_step(plan: SitePlan) -> StoryStep:
    passed, detail = rule_ci_detail(plan)
    status = "waiting" if passed is None else ("done" if passed else "failed")
    return _step(
        "rule_ci",
        "Rule CI tries to break the rule before anyone trusts it",
        status,
        detail or "Rule CI has not run yet.",
    )


def _incident_steps(
    followed: NearMiss | None,
    *,
    chain: ChainReport | None,
    talk: ToolboxTalk | None,
    warning: SpokenWarning | None,
    regressions: int,
    near: Sequence[NearMiss],
) -> list[StoryStep]:
    if followed is None:
        return [
            _step(
                "violation",
                "Someone gets too close to a moving forklift",
                "not_yet",
                "No near miss on this plan's rules today.",
            )
        ]
    event = followed.event_id
    disputed = [n for n in near if n.disputes]
    regressed = [n for n in near if n.regression]
    return [
        _violation_step(followed),
        _verdict_step(followed),
        _warning_step(followed, warning),
        _step(
            "ack",
            "Someone answers the phone alert",
            "done" if followed.acked_by else ("waiting" if followed.alert else "not_yet"),
            "Acknowledged by " + ", ".join(followed.acked_by) + "."
            if followed.acked_by
            else "Waiting for an acknowledgement.",
            event_id=event,
        ),
        _step(
            "evidence",
            "Evidence: hash chain, incident report, verify page",
            "done" if chain is not None and chain.ok else "failed",
            f"Chain intact, {chain.n} records today."
            if chain is not None and chain.ok
            else (
                f"Chain broken at record {chain.first_bad_seq}: {chain.reason}"
                if chain is not None
                else "No chain for the day."
            ),
            event_id=event,
        ),
        _step(
            "toolbox_talk",
            "A two-minute toolbox talk in the worker's language",
            "done" if talk is not None else "not_yet",
            f"{talk.title} ({talk.lang}, about {talk.minutes:g} min)"
            if talk
            else "Not written yet.",
            event_id=event,
        ),
        _step(
            "dispute",
            "A worker disputes an alert from the QR page",
            "done" if disputed else "not_yet",
            f"{len(disputed)} disputed: "
            + ", ".join(f"{n.event_id} ({'/'.join(n.disputes)})" for n in disputed)
            if disputed
            else "No dispute yet.",
        ),
        _step(
            "regression",
            "A wrong alert becomes a permanent Rule CI regression",
            "done" if regressed or regressions else "not_yet",
            f"{len(regressed)} false alarm(s) labelled today; {regressions} regression(s) stored."
            if regressed or regressions
            else "No regression yet.",
        ),
    ]


def _violation_step(n: NearMiss) -> StoryStep:
    parts = [f"{n.separation_m:.2f} m" if n.separation_m is not None else "distance unknown"]
    if n.limit_m is not None:
        parts.append(f"limit {n.limit_m:g} m")
    if n.vehicle_speed_mps is not None:
        parts.append(f"vehicle at {n.vehicle_speed_mps:.2f} m/s")
    if n.ttc_s is not None:
        parts.append(f"contact in {n.ttc_s:.1f} s on this course")
    if n.calibration:
        err = f" ±{n.calibration_err_m:.2f} m" if n.calibration_err_m is not None else ""
        parts.append(f"calibrated from {n.calibration}{err}")
    return _step(
        "violation",
        "Someone gets too close to a moving forklift",
        "done",
        f"{n.camera_id}: " + ", ".join(parts) + ".",
        event_id=n.event_id,
        camera_id=n.camera_id,
    )


def _verdict_step(n: NearMiss) -> StoryStep:
    if n.verdict is None:
        return _step(
            "verdict",
            "Muse Glimmer looks at the frames before anyone is bothered",
            "waiting",
            "Waiting for the verifier.",
            event_id=n.event_id,
        )
    score = f" ({n.verdict_score:.2f})" if n.verdict_score is not None else ""
    outcome = (
        "alert sent" if n.alert else ("suppressed" if n.alert is False else "pending decision")
    )
    route = " and queued for a person" if n.route == "human_queue" else ""
    return _step(
        "verdict",
        "Muse Glimmer looks at the frames before anyone is bothered",
        "done",
        f"{n.verdict}{score}: {n.verdict_reason or ''} -> {outcome}{route}.",
        event_id=n.event_id,
        model=n.verifier or "",
    )


def _warning_step(n: NearMiss, warning: SpokenWarning | None) -> StoryStep:
    spoken = f" Spoken warning ({warning.lang}): “{warning.text}”" if warning is not None else ""
    status = "done" if n.actions or warning is not None else "not_yet"
    return _step(
        "warning",
        "Spoken warning, phone alert and interlock",
        status,
        ("; ".join(n.actions) or "No action recorded.") + spoken,
        event_id=n.event_id,
    )
