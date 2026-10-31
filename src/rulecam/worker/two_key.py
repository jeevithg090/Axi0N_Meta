"""Two-key protected settings: dual-authorization governance for worker privacy (task P2-S2).

Changing high-impact worker privacy and surveillance parameters requires approval
from BOTH an owner and a crew representative. Unilateral changes are blocked.

Protected settings:
1. `anonymous_mode` (or `privacy_mode`): skeleton/blurring vs raw video stream
2. `retention_days`: duration video/events remain before automatic deletion
3. `no_individual_metrics`: fundamental protection banning individual performance rankings
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Final

from pydantic import BaseModel

from rulecam.contracts import Clock, PrivacyMode, SitePlan
from rulecam.evidence.chain import EventChain
from rulecam.log import get_logger

_log = get_logger(__name__)

PROTECTED_SETTINGS: Final[frozenset[str]] = frozenset(
    {"anonymous_mode", "privacy_mode", "retention_days", "no_individual_metrics"}
)


class ApprovalRole(StrEnum):
    """The two required governing roles for protected settings."""

    OWNER = "owner"
    CREW_REPRESENTATIVE = "crew_representative"


class ProposalStatus(StrEnum):
    """Lifecycle status of a two-key setting proposal."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ApprovalRecord(BaseModel):
    """An authorization key from an owner or crew representative."""

    role: ApprovalRole
    approver_id: str
    approved_at: datetime
    signature_token: str


class TwoKeyProposal(BaseModel):
    """A proposed change to a protected setting requiring dual authorization."""

    proposal_id: str
    setting_name: str
    current_value: Any
    proposed_value: Any
    reason: str
    proposed_by: str
    created_at: datetime
    status: ProposalStatus = ProposalStatus.PENDING
    owner_approval: ApprovalRecord | None = None
    crew_approval: ApprovalRecord | None = None
    rejection_reason: str | None = None
    applied_at: datetime | None = None


class TwoKeyError(Exception):
    """Base error for two-key protected settings governance."""


class ProtectedSettingError(TwoKeyError):
    """Raised when an attempt is made to bypass dual-key authorization."""


class ProposalNotFoundError(TwoKeyError):
    """Raised when a proposal ID is unknown."""


class ProposalClosedError(TwoKeyError):
    """Raised when an action is attempted on an already approved or rejected proposal."""


@dataclass
class SiteSettingsState:
    """Active runtime site settings guarded by two-key authorization."""

    anonymous_mode: bool = True
    privacy_mode: PrivacyMode = PrivacyMode.ANONYMOUS
    retention_days: int = 30
    no_individual_metrics: bool = True


class TwoKeySettingsManager:
    """Dual-key authorization manager for protected privacy settings."""

    def __init__(
        self,
        chain: EventChain,
        clock: Clock,
        *,
        initial_plan: SitePlan | None = None,
        initial_state: SiteSettingsState | None = None,
    ) -> None:
        """Create the manager.

        Args:
            chain: Audit evidence chain.
            clock: Time source.
            initial_plan: Optional SitePlan to initialize settings from.
            initial_state: Optional explicit runtime settings state.
        """
        self._chain = chain
        self._clock = clock
        self._proposals: dict[str, TwoKeyProposal] = {}

        if initial_state is not None:
            self._state = initial_state
        elif initial_plan is not None:
            self._state = SiteSettingsState(
                anonymous_mode=initial_plan.privacy_mode == PrivacyMode.ANONYMOUS,
                privacy_mode=initial_plan.privacy_mode,
                retention_days=initial_plan.retention_days,
                no_individual_metrics=True,
            )
        else:
            self._state = SiteSettingsState()

    @property
    def state(self) -> SiteSettingsState:
        """Current active settings."""
        return self._state

    def is_protected(self, setting_name: str) -> bool:
        """Return whether `setting_name` requires dual-key authorization."""
        return setting_name.lower() in PROTECTED_SETTINGS

    def get_setting(self, setting_name: str) -> Any:
        """Get the current value of a protected or general setting."""
        norm = setting_name.lower()
        if hasattr(self._state, norm):
            return getattr(self._state, norm)
        msg = f"unknown setting: {setting_name}"
        raise KeyError(msg)

    def apply_unprotected(self, setting_name: str, value: Any) -> None:
        """Apply an unprotected setting directly.

        Raises:
            ProtectedSettingError: If the setting is dual-key protected.
        """
        if self.is_protected(setting_name):
            msg = (
                f"setting {setting_name!r} is protected; "
                f"changing it requires approval from both an owner and a crew representative"
            )
            raise ProtectedSettingError(msg)
        setattr(self._state, setting_name.lower(), value)

    def propose_change(
        self,
        setting_name: str,
        proposed_value: Any,
        reason: str,
        *,
        proposed_by: str,
    ) -> TwoKeyProposal:
        """Propose a change to a protected setting.

        The proposal is recorded in the evidence chain and stays PENDING until
        both an owner and a crew representative have provided valid approvals.
        """
        norm = setting_name.lower()
        if not self.is_protected(norm):
            msg = f"setting {setting_name!r} is not a two-key protected setting"
            raise ValueError(msg)

        current = self.get_setting(norm)
        proposal_id = f"prop-{secrets.token_hex(6)}"
        proposal = TwoKeyProposal(
            proposal_id=proposal_id,
            setting_name=norm,
            current_value=current,
            proposed_value=proposed_value,
            reason=reason,
            proposed_by=proposed_by,
            created_at=self._clock.now(),
        )
        self._proposals[proposal_id] = proposal

        self._chain.append(
            "rule_change",
            {
                "action": "setting_proposal",
                "proposal_id": proposal_id,
                "setting_name": norm,
                "current_value": str(current),
                "proposed_value": str(proposed_value),
                "reason": reason,
                "proposed_by": proposed_by,
                "timestamp": proposal.created_at.isoformat(),
            },
        )
        _log.info(
            "two_key_setting_proposed",
            proposal_id=proposal_id,
            setting_name=norm,
            proposed_by=proposed_by,
        )
        return proposal

    def approve(
        self,
        proposal_id: str,
        role: ApprovalRole | str,
        approver_id: str,
        signature_token: str,
    ) -> TwoKeyProposal:
        """Record an approval key from an owner or crew representative.

        If both owner and crew representative approvals are now present, the
        proposal transitions to APPROVED and the setting is automatically applied.
        """
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            msg = f"no proposal with id {proposal_id!r}"
            raise ProposalNotFoundError(msg)

        if proposal.status != ProposalStatus.PENDING:
            msg = f"proposal {proposal_id} is already {proposal.status.value}"
            raise ProposalClosedError(msg)

        app_role = ApprovalRole(role)
        record = ApprovalRecord(
            role=app_role,
            approver_id=approver_id,
            approved_at=self._clock.now(),
            signature_token=signature_token,
        )

        if app_role == ApprovalRole.OWNER:
            proposal.owner_approval = record
        elif app_role == ApprovalRole.CREW_REPRESENTATIVE:
            proposal.crew_approval = record

        self._chain.append(
            "rule_change",
            {
                "action": "setting_approval",
                "proposal_id": proposal_id,
                "role": app_role.value,
                "approver_id": approver_id,
                "timestamp": record.approved_at.isoformat(),
            },
        )

        # Check if both keys are present
        if proposal.owner_approval is not None and proposal.crew_approval is not None:
            proposal.status = ProposalStatus.APPROVED
            proposal.applied_at = self._clock.now()
            self._apply_setting(proposal.setting_name, proposal.proposed_value)

            self._chain.append(
                "rule_change",
                {
                    "action": "setting_applied",
                    "proposal_id": proposal_id,
                    "setting_name": proposal.setting_name,
                    "old_value": str(proposal.current_value),
                    "new_value": str(proposal.proposed_value),
                    "applied_at": proposal.applied_at.isoformat(),
                },
            )
            _log.info(
                "two_key_setting_applied",
                proposal_id=proposal_id,
                setting_name=proposal.setting_name,
                new_value=proposal.proposed_value,
            )

        return proposal

    def reject(
        self,
        proposal_id: str,
        role: ApprovalRole | str,
        rejecter_id: str,
        reason: str,
    ) -> TwoKeyProposal:
        """Reject a proposal. A rejection from either role terminates the proposal."""
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            msg = f"no proposal with id {proposal_id!r}"
            raise ProposalNotFoundError(msg)

        if proposal.status != ProposalStatus.PENDING:
            msg = f"proposal {proposal_id} is already {proposal.status.value}"
            raise ProposalClosedError(msg)

        app_role = ApprovalRole(role)
        proposal.status = ProposalStatus.REJECTED
        proposal.rejection_reason = f"Rejected by {app_role.value} ({rejecter_id}): {reason}"

        self._chain.append(
            "rule_change",
            {
                "action": "setting_rejected",
                "proposal_id": proposal_id,
                "rejected_by_role": app_role.value,
                "rejecter_id": rejecter_id,
                "reason": reason,
                "timestamp": self._clock.now().isoformat(),
            },
        )
        _log.info(
            "two_key_setting_rejected",
            proposal_id=proposal_id,
            role=app_role.value,
            rejecter_id=rejecter_id,
        )
        return proposal

    def _apply_setting(self, setting_name: str, value: Any) -> None:
        """Internal helper to mutate state upon verified two-key approval."""
        norm = setting_name.lower()
        if norm == "anonymous_mode":
            self._state.anonymous_mode = bool(value)
            self._state.privacy_mode = PrivacyMode.ANONYMOUS if value else PrivacyMode.STANDARD
        elif norm == "privacy_mode":
            mode = PrivacyMode(value)
            self._state.privacy_mode = mode
            self._state.anonymous_mode = mode == PrivacyMode.ANONYMOUS
        elif norm == "retention_days":
            self._state.retention_days = int(value)
        elif norm == "no_individual_metrics":
            self._state.no_individual_metrics = bool(value)
