"""The pure predicate and temporal rules engine (P1-06).

`evaluate(rule, tracks, *, calibration, zones, t0, t1, hz)` turns a Rule IR and a window of
tracks into `CandidateEvent`s. See this package's README for the semantics.
"""

from rulecam.rules.engine import evaluate
from rulecam.rules.errors import RuleEvaluationError, UnsupportedPredicateError
from rulecam.rules.hashing import rule_ir_hash

__all__ = ["RuleEvaluationError", "UnsupportedPredicateError", "evaluate", "rule_ir_hash"]
