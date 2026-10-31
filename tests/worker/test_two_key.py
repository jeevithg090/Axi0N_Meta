"""Tests for two-key protected settings governance (task P2-S2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from rulecam.contracts import PrivacyMode
from rulecam.evidence.chain import EventChain, read_chain
from rulecam.worker.two_key import (
    ApprovalRole,
    ProposalClosedError,
    ProposalNotFoundError,
    ProposalStatus,
    ProtectedSettingError,
    SiteSettingsState,
    TwoKeySettingsManager,
)
from tests.fakes import EPOCH, FakeClock
from tests.worker.helpers import make_plan


def test_is_protected_identifies_worker_privacy_settings(tmp_path: Path) -> None:
    clock = FakeClock(EPOCH)
    chain = EventChain(tmp_path, "site1", clock=clock)
    manager = TwoKeySettingsManager(chain, clock)

    assert manager.is_protected("anonymous_mode")
    assert manager.is_protected("privacy_mode")
    assert manager.is_protected("retention_days")
    assert manager.is_protected("no_individual_metrics")

    assert not manager.is_protected("display_language")
    assert not manager.is_protected("camera_name")


def test_unprotected_setting_can_be_applied_directly(tmp_path: Path) -> None:
    clock = FakeClock(EPOCH)
    chain = EventChain(tmp_path, "site1", clock=clock)
    manager = TwoKeySettingsManager(chain, clock)

    manager.apply_unprotected("volume", 80)
    assert manager.get_setting("volume") == 80


def test_applying_protected_setting_directly_is_blocked(tmp_path: Path) -> None:
    clock = FakeClock(EPOCH)
    chain = EventChain(tmp_path, "site1", clock=clock)
    manager = TwoKeySettingsManager(chain, clock)

    with pytest.raises(ProtectedSettingError, match="both an owner and a crew representative"):
        manager.apply_unprotected("anonymous_mode", False)

    with pytest.raises(ProtectedSettingError, match="both an owner and a crew representative"):
        manager.apply_unprotected("retention_days", 90)

    with pytest.raises(ProtectedSettingError, match="both an owner and a crew representative"):
        manager.apply_unprotected("no_individual_metrics", False)


def test_two_key_flow_for_anonymous_mode_activation(tmp_path: Path) -> None:
    clock = FakeClock(EPOCH)
    chain = EventChain(tmp_path, "site1", clock=clock)
    manager = TwoKeySettingsManager(
        chain,
        clock,
        initial_state=SiteSettingsState(anonymous_mode=True, privacy_mode=PrivacyMode.ANONYMOUS),
    )

    # 1. Propose changing Anonymous Mode to False (e.g. for maintenance inspection)
    proposal = manager.propose_change(
        "anonymous_mode",
        False,
        "Temporary raw stream inspection for sensor calibration",
        proposed_by="owner_dave",
    )
    assert proposal.status == ProposalStatus.PENDING
    assert manager.state.anonymous_mode is True  # unchanged

    # 2. Owner approves
    prop = manager.approve(
        proposal.proposal_id,
        role=ApprovalRole.OWNER,
        approver_id="owner_dave",
        signature_token="sig-owner-12345",  # noqa: S106
    )
    assert prop.status == ProposalStatus.PENDING
    assert prop.owner_approval is not None
    assert prop.crew_approval is None
    assert manager.state.anonymous_mode is True  # still unchanged!

    # 3. Crew representative approves
    clock.advance(60.0)
    prop = manager.approve(
        proposal.proposal_id,
        role=ApprovalRole.CREW_REPRESENTATIVE,
        approver_id="rep_sarah",
        signature_token="sig-crew-67890",  # noqa: S106
    )
    assert prop.status == ProposalStatus.APPROVED
    assert prop.crew_approval is not None
    assert prop.applied_at is not None

    # Now the change is active!
    assert manager.state.anonymous_mode is False
    assert manager.state.privacy_mode == PrivacyMode.STANDARD

    # Verify audit chain records
    path = next((tmp_path / "site1").glob("*.jsonl"))
    records = read_chain(path)
    actions = [r.payload.get("action") for r in records if r.kind == "rule_change"]
    assert "setting_proposal" in actions
    assert "setting_approval" in actions
    assert "setting_applied" in actions


def test_two_key_crew_rep_approves_first_then_owner(tmp_path: Path) -> None:
    clock = FakeClock(EPOCH)
    chain = EventChain(tmp_path, "site1", clock=clock)
    manager = TwoKeySettingsManager(
        chain,
        clock,
        initial_state=SiteSettingsState(retention_days=30),
    )

    proposal = manager.propose_change(
        "retention_days",
        14,
        "Shorten retention window to 14 days per crew privacy agreement",
        proposed_by="rep_sarah",
    )

    # Crew representative signs first
    prop = manager.approve(
        proposal.proposal_id,
        role=ApprovalRole.CREW_REPRESENTATIVE,
        approver_id="rep_sarah",
        signature_token="sig-crew-111",  # noqa: S106
    )
    assert prop.status == ProposalStatus.PENDING
    assert manager.state.retention_days == 30

    # Owner signs second
    prop = manager.approve(
        proposal.proposal_id,
        role=ApprovalRole.OWNER,
        approver_id="owner_dave",
        signature_token="sig-owner-222",  # noqa: S106
    )
    assert prop.status == ProposalStatus.APPROVED
    assert manager.state.retention_days == 14


def test_rejection_by_either_role_terminates_proposal(tmp_path: Path) -> None:
    clock = FakeClock(EPOCH)
    chain = EventChain(tmp_path, "site1", clock=clock)
    manager = TwoKeySettingsManager(
        chain,
        clock,
        initial_state=SiteSettingsState(no_individual_metrics=True),
    )

    proposal = manager.propose_change(
        "no_individual_metrics",
        False,
        "Request by management to track individual pick speeds",
        proposed_by="manager_bob",
    )

    # Crew representative rejects the proposal
    prop = manager.reject(
        proposal.proposal_id,
        role=ApprovalRole.CREW_REPRESENTATIVE,
        rejecter_id="rep_sarah",
        reason="Crew agreement bans individual productivity comparisons",
    )
    assert prop.status == ProposalStatus.REJECTED
    assert "Crew agreement bans" in (prop.rejection_reason or "")
    assert manager.state.no_individual_metrics is True  # strictly unchanged

    # Subsequent attempt to approve a rejected proposal fails
    with pytest.raises(ProposalClosedError, match="already rejected"):
        manager.approve(
            proposal.proposal_id,
            role=ApprovalRole.OWNER,
            approver_id="owner_dave",
            signature_token="sig-owner-333",  # noqa: S106
        )


def test_proposal_not_found_raises(tmp_path: Path) -> None:
    clock = FakeClock(EPOCH)
    chain = EventChain(tmp_path, "site1", clock=clock)
    manager = TwoKeySettingsManager(chain, clock)

    with pytest.raises(ProposalNotFoundError):
        manager.approve("prop-doesnotexist", ApprovalRole.OWNER, "owner1", "token")

    with pytest.raises(ProposalNotFoundError):
        manager.reject("prop-doesnotexist", ApprovalRole.CREW_REPRESENTATIVE, "crew1", "reason")


def test_initialization_from_site_plan(tmp_path: Path) -> None:
    clock = FakeClock(EPOCH)
    chain = EventChain(tmp_path, "site1", clock=clock)
    plan = make_plan()
    manager = TwoKeySettingsManager(chain, clock, initial_plan=plan)

    assert manager.state.anonymous_mode is True
    assert manager.state.privacy_mode == PrivacyMode.ANONYMOUS
    assert manager.state.retention_days == plan.retention_days
    assert manager.state.no_individual_metrics is True
