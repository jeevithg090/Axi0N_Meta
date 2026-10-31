"""The owner's passphrase hash: scrypt from the standard library (`hashlib.scrypt`).

The stored form is one line, `scrypt:<n>:<r>:<p>:<salt>:<key>` (salt and key in unpadded
base64url), so it fits in `.env` and in a shell without quoting. The defaults are OWASP's scrypt
minimum (N = 2**17, r = 8, p = 1: 128 MiB and a few hundred milliseconds per check), which makes
an offline guess as costly as an online one. `verify` compares the derived key in constant time.
`rulecam owner set-passphrase` makes the line; the passphrase itself is never stored or logged.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Final

SCHEME: Final = "scrypt"
SCRYPT_N: Final = 2**17
SCRYPT_R: Final = 8
SCRYPT_P: Final = 1
SALT_BYTES: Final = 16
KEY_BYTES: Final = 32
MAX_MEMORY_BYTES: Final = 512 * 1024 * 1024  # refuse parameters that would need more
MIN_PASSPHRASE_CHARS: Final = 12
MAX_PASSPHRASE_CHARS: Final = 1024
_FIELDS: Final = 6
_MIN_N: Final = 2


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _memory(n: int, r: int, p: int) -> int:
    """OpenSSL's scrypt memory need (its `maxmem` check), plus slack."""
    return 128 * r * (n + 2) + 128 * r * p + 1024 * 1024


@dataclass(frozen=True)
class PassphraseHash:
    """A parsed `scrypt:` line.

    Attributes:
        n: CPU/memory cost (a power of two).
        r: Block size.
        p: Parallelism.
        salt: Random salt.
        key: The derived key.
    """

    n: int
    r: int
    p: int
    salt: bytes
    key: bytes

    @classmethod
    def parse(cls, text: str) -> PassphraseHash:
        """Parse the stored line.

        Raises:
            ValueError: If it is not a well-formed `scrypt:` line with sane parameters.
        """
        parts = text.strip().split(":")
        if len(parts) != _FIELDS or parts[0] != SCHEME:
            msg = "expected scrypt:<n>:<r>:<p>:<salt>:<key>"
            raise ValueError(msg)
        try:
            n, r, p = (int(v) for v in parts[1:4])
            salt, key = _unb64(parts[4]), _unb64(parts[5])
        except ValueError as exc:
            msg = "malformed scrypt parameters"
            raise ValueError(msg) from exc
        if n < _MIN_N or n & (n - 1) or r < 1 or p < 1 or _memory(n, r, p) > MAX_MEMORY_BYTES:
            msg = "scrypt parameters out of range"
            raise ValueError(msg)
        if len(salt) < SALT_BYTES or len(key) < KEY_BYTES:
            msg = "scrypt salt or key too short"
            raise ValueError(msg)
        return cls(n=n, r=r, p=p, salt=salt, key=key)

    def derive(self, passphrase: str) -> bytes:
        """The key `passphrase` gives with this hash's salt and parameters."""
        return hashlib.scrypt(
            passphrase.encode("utf-8"),
            salt=self.salt,
            n=self.n,
            r=self.r,
            p=self.p,
            maxmem=_memory(self.n, self.r, self.p),
            dklen=len(self.key),
        )

    def verify(self, passphrase: str) -> bool:
        """Whether `passphrase` matches (constant-time comparison of the derived keys)."""
        if not passphrase or len(passphrase) > MAX_PASSPHRASE_CHARS:
            return False
        return hmac.compare_digest(self.derive(passphrase), self.key)

    def line(self) -> str:
        """The stored form."""
        return f"{SCHEME}:{self.n}:{self.r}:{self.p}:{_b64(self.salt)}:{_b64(self.key)}"


def hash_passphrase(
    passphrase: str, *, n: int = SCRYPT_N, r: int = SCRYPT_R, p: int = SCRYPT_P
) -> str:
    """Hash a passphrase with a fresh random salt.

    Args:
        passphrase: The owner's passphrase.
        n: CPU/memory cost (tests pass a small one).
        r: Block size.
        p: Parallelism.

    Returns:
        The `scrypt:` line.
    """
    template = PassphraseHash(
        n=n, r=r, p=p, salt=secrets.token_bytes(SALT_BYTES), key=bytes(KEY_BYTES)
    )
    return PassphraseHash(n=n, r=r, p=p, salt=template.salt, key=template.derive(passphrase)).line()


def passphrase_problem(passphrase: str) -> str | None:
    """Why a new passphrase is unacceptable, or `None` when it is fine."""
    if len(passphrase) < MIN_PASSPHRASE_CHARS:
        return f"use at least {MIN_PASSPHRASE_CHARS} characters (a few random words is best)"
    if len(passphrase) > MAX_PASSPHRASE_CHARS:
        return f"use at most {MAX_PASSPHRASE_CHARS} characters"
    if passphrase.strip() != passphrase:
        return "no leading or trailing spaces"
    return None
