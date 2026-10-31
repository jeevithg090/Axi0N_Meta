"""Daily Merkle roots over the hash chains (task P3-07b).

The leaves of a day's root are the record hashes of every site's chain for that day, in file
order. Interior nodes are `sha256(left_hex + right_hex)` (hex text, so the browser verify page can
recompute them with Web Crypto). An odd node is paired with itself. Roots are written to
`evidence/roots/<date>.txt`, one line per site (`<site> <root> <n_records>`), and committed every
day: once a root is in git history, a chain cannot be rewritten without the diff showing.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from rulecam.evidence.chain import read_chain

EMPTY_ROOT = hashlib.sha256(b"").hexdigest()


def _pair(left: str, right: str) -> str:
    return hashlib.sha256((left + right).encode("ascii")).hexdigest()


def merkle_root(leaves: Sequence[str]) -> str:
    """Return the Merkle root of hex leaves (the hash of empty input when there are none).

    Args:
        leaves: Hex SHA-256 strings, in order.

    Returns:
        The hex root.
    """
    level = list(leaves)
    if not level:
        return EMPTY_ROOT
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [_pair(level[i], level[i + 1]) for i in range(0, len(level), 2)]
    return level[0]


def chain_root(path: Path) -> tuple[str, int]:
    """Return (root, record count) of one chain file."""
    hashes = [record.hash for record in read_chain(path)]
    return merkle_root(hashes), len(hashes)


def day_roots(chain_root_dir: Path, day: date) -> dict[str, tuple[str, int]]:
    """Return `{site: (root, n)}` for every site with a chain on `day`."""
    name = f"{day.isoformat()}.jsonl"
    return (
        {
            site_dir.name: chain_root(site_dir / name)
            for site_dir in sorted(p for p in chain_root_dir.iterdir() if p.is_dir())
            if (site_dir / name).is_file()
        }
        if chain_root_dir.is_dir()
        else {}
    )


def write_day_roots(chain_root_dir: Path, roots_dir: Path, day: date) -> Path:
    """Write `roots_dir/<day>.txt` with one `<site> <root> <n>` line per site.

    Args:
        chain_root_dir: Directory holding one folder per site.
        roots_dir: Usually `evidence/roots` in the repository.
        day: The UTC day.

    Returns:
        The written file.

    Raises:
        FileNotFoundError: If no site has a chain on `day`.
    """
    roots = day_roots(chain_root_dir, day)
    if not roots:
        msg = f"no chains for {day.isoformat()} under {chain_root_dir}"
        raise FileNotFoundError(msg)
    roots_dir.mkdir(parents=True, exist_ok=True)
    path = roots_dir / f"{day.isoformat()}.txt"
    lines = [f"{site} {root} {n}" for site, (root, n) in roots.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def read_day_roots(path: Path) -> dict[str, tuple[str, int]]:
    """Parse a roots file back into `{site: (root, n)}`."""
    out: dict[str, tuple[str, int]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        site, root, n = line.split()
        out[site] = (root, int(n))
    return out
