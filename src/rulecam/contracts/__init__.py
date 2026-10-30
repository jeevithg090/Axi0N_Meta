"""Shared Rulecam contracts.

Every module codes against these models and Protocols. Changing them needs a PR labelled
`contract-change`, two approvals, and a bump of `CONTRACTS_VERSION` (task book §4).
"""

from rulecam.contracts.advisor import (
    Change,
    Explanation,
    Gain,
    MeasurementPlan,
    Recommendation,
    SafetyCheck,
    SlotClaim,
    Verdict,
)
from rulecam.contracts.core import (
    Calibration,
    Chunk,
    Detection,
    Frozen,
    Keypoint,
    Mask,
    Measurement,
    PrivacyMode,
    Provenance,
    Severity,
    Track,
    TrackPoint,
    Zone,
)
from rulecam.contracts.events import (
    Ack,
    ActionRecord,
    CandidateEvent,
    Dispute,
    EventRecord,
    VerifierVerdict,
)
from rulecam.contracts.plan import (
    AcceptanceCheck,
    AcceptanceResult,
    AuditFinding,
    FeasibilityVerdict,
    InventoryItem,
    Probe,
    RequestItem,
    SitePlan,
)
from rulecam.contracts.protocols import (
    TTS,
    Clock,
    Notifier,
    Plug,
    Segmenter,
    StructuredLLM,
    TrackStore,
    Transcriber,
    Verifier,
)
from rulecam.contracts.rule_ir import Predicate, RuleIR
from rulecam.contracts.worldmodel import SurpriseEvent, SurpriseWindow

CONTRACTS_VERSION = "1.0.0"

__all__ = [
    "CONTRACTS_VERSION",
    "TTS",
    "AcceptanceCheck",
    "AcceptanceResult",
    "Ack",
    "ActionRecord",
    "AuditFinding",
    "Calibration",
    "CandidateEvent",
    "Change",
    "Chunk",
    "Clock",
    "Detection",
    "Dispute",
    "EventRecord",
    "Explanation",
    "FeasibilityVerdict",
    "Frozen",
    "Gain",
    "InventoryItem",
    "Keypoint",
    "Mask",
    "Measurement",
    "MeasurementPlan",
    "Notifier",
    "Plug",
    "Predicate",
    "PrivacyMode",
    "Probe",
    "Provenance",
    "Recommendation",
    "RequestItem",
    "RuleIR",
    "SafetyCheck",
    "Segmenter",
    "Severity",
    "SitePlan",
    "SlotClaim",
    "StructuredLLM",
    "SurpriseEvent",
    "SurpriseWindow",
    "Track",
    "TrackPoint",
    "TrackStore",
    "Transcriber",
    "Verdict",
    "Verifier",
    "VerifierVerdict",
    "Zone",
]
