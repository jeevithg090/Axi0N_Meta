"""Stored packs and signed, expiring share links.

Packs are kept as JSON under `<data>/compliance/packs/<pack_id>.json` (`PackStore`). A share link
carries a token `<payload>.<signature>`: the payload (base64url JSON) names the pack, its hash
and the expiry; the signature is HMAC-SHA256 over the payload with the site's share secret
(`RULECAM_SHARE_SECRET`, else a random key kept in `<data>/compliance/share.key`). The public
route serves a pack only for a token whose signature holds, that has not expired, whose pack hash
still matches the stored pack, and that has not been revoked. Anyone can read the payload; only
the site can mint one.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from pydantic import BaseModel

from rulecam.compliance.models import PackEntry
from rulecam.errors import RulecamError

PACK_ID: Final = re.compile(r"^pp-[0-9a-f]{12}$")
KEY_BYTES: Final = 32


class ShareError(RulecamError):
    """A share token is malformed, forged, expired or revoked."""


class TokenClaims(BaseModel):
    """What a share token says: the pack, its hash and when the link stops working."""

    p: str
    h: str
    x: int


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def load_secret(configured: str | None, key_file: Path) -> bytes:
    """The share secret: the configured one, else a random key created once in `key_file`."""
    if configured:
        return configured.encode("utf-8")
    if key_file.is_file():
        return key_file.read_bytes()
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(KEY_BYTES)
    fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(key)
    return key


class ShareSigner:
    """Mints and checks share tokens."""

    def __init__(self, secret: bytes) -> None:
        """Use `secret` for the HMAC."""
        self._secret = secret

    def _sign(self, payload: str) -> str:
        return _b64(hmac.new(self._secret, payload.encode("ascii"), hashlib.sha256).digest())

    def mint(self, pack_id: str, pack_hash: str, expires_at: datetime) -> str:
        """A token for one pack until `expires_at`."""
        claims = TokenClaims(p=pack_id, h=pack_hash, x=int(expires_at.timestamp()))
        payload = _b64(json.dumps(claims.model_dump(), separators=(",", ":")).encode())
        return f"{payload}.{self._sign(payload)}"

    def check(self, token: str, now: datetime) -> TokenClaims:
        """The token's claims when its signature holds and it has not expired.

        Raises:
            ShareError: For a malformed, forged or expired token.
        """
        payload, _, signature = token.partition(".")
        if not payload or not signature or not hmac.compare_digest(self._sign(payload), signature):
            msg = "this link is not valid"
            raise ShareError(msg)
        try:
            claims = TokenClaims.model_validate_json(_unb64(payload))
        except ValueError as exc:
            msg = "this link is not valid"
            raise ShareError(msg) from exc
        if claims.x <= int(now.timestamp()):
            msg = "this link has expired"
            raise ShareError(msg)
        return claims


class PackStore:
    """Packs and revoked links on disk."""

    def __init__(self, root: Path) -> None:
        """Keep packs under `root/packs` and revocations in `root/revoked.json`."""
        self._root = root

    def _path(self, pack_id: str) -> Path:
        if not PACK_ID.fullmatch(pack_id):
            msg = f"invalid pack id {pack_id!r}"
            raise ValueError(msg)
        return self._root / "packs" / f"{pack_id}.json"

    def save(self, entry: PackEntry) -> None:
        """Write a pack entry atomically."""
        path = self._path(entry.pack_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(entry.model_dump_json(), encoding="utf-8")
        tmp.replace(path)

    def get(self, pack_id: str) -> PackEntry | None:
        """A stored pack, or `None`."""
        try:
            path = self._path(pack_id)
        except ValueError:
            return None
        if not path.is_file():
            return None
        return PackEntry.model_validate_json(path.read_text(encoding="utf-8"))

    def list(self) -> list[PackEntry]:
        """Every pack (without its body), newest first."""
        folder = self._root / "packs"
        if not folder.is_dir():
            return []
        entries = [
            PackEntry.model_validate_json(p.read_text(encoding="utf-8")).model_copy(
                update={"pack": None}
            )
            for p in folder.glob("pp-*.json")
        ]
        return sorted(entries, key=lambda e: e.created_at, reverse=True)

    def _revoked_path(self) -> Path:
        return self._root / "revoked.json"

    def revoked(self) -> set[str]:
        """Pack ids whose links were revoked."""
        path = self._revoked_path()
        if not path.is_file():
            return set()
        data = json.loads(path.read_text(encoding="utf-8"))
        return {str(x) for x in data} if isinstance(data, list) else set()

    def revoke(self, pack_id: str) -> None:
        """Stop every link to a pack from working."""
        ids = self.revoked() | {pack_id}
        path = self._revoked_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(sorted(ids)), encoding="utf-8")


def now_utc() -> datetime:
    """The current time in UTC."""
    return datetime.now(UTC)
