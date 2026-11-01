"""Prototype mode (`RULECAM_PROTOTYPE_MODE=true`): the models decide, nothing blocks them.

In prototype mode:

- the planner's guardrails decline only requests that single people out by race, ethnicity,
  skin colour, religion, nationality, immigration status or sexual orientation
  (`planner.guardrails.profiling_check`); identity, health, per-person, private-space, covert
  and speech requests are compiled like any other, and Spark's guardrail pass is skipped;
- every concern is compiled, also one the feasibility gauge could not observe;
- lint does not flag zones named after private spaces;
- every compiled rule activates: failed acceptance and Rule CI checks are caveats (`amber`);
- the verifier's verdict is final (`verify.policy`): a confirmed violation alerts, a rejection
  is suppressed whatever the severity, and an unsure verdict alerts when its score is at least
  one half. A person reviews only a candidate no model could judge;
- evidence strips are the raw frames with the rule's overlays, without face blurring.

It is off by default; the CI suites run with it off.
"""

from __future__ import annotations

from rulecam.config import get_settings


def prototype_mode() -> bool:
    """Whether prototype mode is on (`RULECAM_PROTOTYPE_MODE`)."""
    return get_settings().rulecam_prototype_mode
