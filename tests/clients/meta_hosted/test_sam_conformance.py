"""`HostedSam31` must satisfy the `Segmenter` Protocol.

mypy checks this file in CI (`make type`): assigning the client to a `Segmenter`-typed variable
fails type-checking if its `segment` signature drifts from the contract.
"""

from __future__ import annotations

from rulecam.clients.meta_hosted.sam import HostedSam31
from rulecam.contracts import Segmenter
from tests.clients.meta_hosted.transport_fakes import ScriptedTransport
from tests.fakes import FakeClock


def test_hosted_sam_conforms_to_segmenter() -> None:
    segmenter: Segmenter = HostedSam31(ScriptedTransport(), clock=FakeClock())

    assert callable(segmenter.segment)
