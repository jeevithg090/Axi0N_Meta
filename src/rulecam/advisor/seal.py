"""Sealed causes: the credibility device for the Site Advisor demo (task P3-10, SA §9).

Before a staged session, a teammate writes the hidden cause ("replenishment at 13:00 takes both
forklifts"). `seal` appends only `sha256(cause + salt)` to the chain and keeps the cause and salt in
a local file that never leaves the machine. The advisor analyses the session blind. `reveal` then
appends the plaintext and salt, and anyone can recompute the hash from the chain. A wrong cause or
salt does not verify.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Sequence
from pathlib import Path

from pydantic import Field

from rulecam.advisor.errors import AdvisorError
from rulecam.contracts import EventRecord, Frozen
from rulecam.evidence.chain import EventChain

SALT_BYTES = 16
MAX_CAUSE_CHARS = 500


class SealError(AdvisorError):
    """A seal cannot be created, found or revealed."""


class SealStatus(Frozen):
    """What the chain says about one seal."""

    seal_id: str
    sealed: bool
    revealed: bool
    matches: bool | None = None  # None until revealed
    cause: str | None = None


class _LocalSeal(Frozen):
    seal_id: str
    cause: str = Field(min_length=1, max_length=MAX_CAUSE_CHARS)
    salt: str


def seal_hash(cause: str, salt: str) -> str:
    """`sha256(cause + salt)` as hex (the cause is stripped first)."""
    return hashlib.sha256((cause.strip() + salt).encode("utf-8")).hexdigest()


class SealBox:
    """Seals and reveals causes on the chain, keeping secrets in `secrets_dir` until reveal."""

    def __init__(self, chain: EventChain, secrets_dir: Path) -> None:
        """Create the box.

        Args:
            chain: The site's evidence chain.
            secrets_dir: Local, git-ignored folder (e.g. `data/seals`) for cause and salt files.
        """
        self._chain = chain
        self._dir = secrets_dir

    def seal(self, cause: str) -> tuple[str, EventRecord]:
        """Seal a cause: append its hash, store cause and salt locally.

        Returns:
            The seal id and its chain record.

        Raises:
            SealError: For an empty or overlong cause.
        """
        text = cause.strip()
        if not text or len(text) > MAX_CAUSE_CHARS:
            msg = f"a cause must be 1-{MAX_CAUSE_CHARS} characters"
            raise SealError(msg)
        salt = secrets.token_hex(SALT_BYTES)
        seal_id = f"seal_{secrets.token_hex(6)}"
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{seal_id}.json"
        path.write_text(
            _LocalSeal(seal_id=seal_id, cause=text, salt=salt).model_dump_json(), encoding="utf-8"
        )
        path.chmod(0o600)
        record = self._chain.append(
            "sealed_cause", {"seal_id": seal_id, "hash": seal_hash(text, salt)}
        )
        return seal_id, record

    def reveal(self, seal_id: str) -> EventRecord:
        """Append the cause and salt of a sealed cause.

        Raises:
            SealError: If no local secret exists for `seal_id`.
        """
        if not seal_id.startswith("seal_") or not seal_id[5:].isalnum():
            msg = f"invalid seal id {seal_id!r}"
            raise SealError(msg)
        path = self._dir / f"{seal_id}.json"
        try:
            local = _LocalSeal.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            msg = f"no local secret for {seal_id}"
            raise SealError(msg) from exc
        return self._chain.append(
            "sealed_cause",
            {"seal_id": seal_id, "revealed": True, "cause": local.cause, "salt": local.salt},
        )


def seal_status(records: Sequence[EventRecord], seal_id: str) -> SealStatus:
    """Read a seal's state from chain records and recompute its hash on reveal.

    Args:
        records: A chain file's records.
        seal_id: The seal.

    Returns:
        Its status; `matches` is True only when the revealed cause and salt give the sealed hash.
    """
    mine = [r for r in records if r.kind == "sealed_cause" and r.payload.get("seal_id") == seal_id]
    sealed = next((r for r in mine if "hash" in r.payload), None)
    revealed = next((r for r in mine if r.payload.get("revealed") is True), None)
    if sealed is None:
        return SealStatus(seal_id=seal_id, sealed=False, revealed=revealed is not None)
    if revealed is None or revealed.seq < sealed.seq:
        return SealStatus(seal_id=seal_id, sealed=True, revealed=False)
    cause = str(revealed.payload.get("cause", ""))
    salt = str(revealed.payload.get("salt", ""))
    return SealStatus(
        seal_id=seal_id,
        sealed=True,
        revealed=True,
        matches=seal_hash(cause, salt) == sealed.payload["hash"],
        cause=cause,
    )


def seal_ids(records: Sequence[EventRecord]) -> list[str]:
    """Every seal id in the records, in first-seen order."""
    ids = [str(r.payload.get("seal_id")) for r in records if r.kind == "sealed_cause"]
    return list(dict.fromkeys(ids))


def dumps_status(status: SealStatus) -> str:
    """One line for the CLI."""
    return json.dumps(status.model_dump(), sort_keys=True)
