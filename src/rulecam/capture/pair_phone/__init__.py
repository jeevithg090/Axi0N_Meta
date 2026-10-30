"""Pair-a-Phone: any phone becomes a camera, with a SAM placement coach (task P1-09)."""

from rulecam.capture.pair_phone.coach import (
    CoachBars,
    CoachHello,
    CoachHint,
    CoachStatus,
    FrameSegmenter,
    concept_confidence,
)
from rulecam.capture.pair_phone.errors import PairingError
from rulecam.capture.pair_phone.server import (
    MAX_UPLOAD_BYTES,
    SESSION_HEADER,
    build_router,
    require_owner,
    require_secure_transport,
)
from rulecam.capture.pair_phone.service import PairPhone, PairToken
from rulecam.capture.pair_phone.tokens import PairedCamera, PairingRegistry, RateLimiter

__all__ = [
    "MAX_UPLOAD_BYTES",
    "SESSION_HEADER",
    "CoachBars",
    "CoachHello",
    "CoachHint",
    "CoachStatus",
    "FrameSegmenter",
    "PairPhone",
    "PairToken",
    "PairedCamera",
    "PairingError",
    "PairingRegistry",
    "RateLimiter",
    "build_router",
    "concept_confidence",
    "require_owner",
    "require_secure_transport",
]
