"""Resolve a clip to a local video file: a path in its folder, or a pinned URL in the cache.

Video binaries are never committed. A URL source is downloaded once into the git-ignored cache
(`data/eval_cache/<sha256>.<ext>` by default) and checked against its sha256 every time it is
used, so a changed upstream file fails loudly instead of silently changing the eval set.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

import httpx

from rulecam.eval.errors import ClipFetchError
from rulecam.eval.schema import ClipEntry
from rulecam.log import get_logger

_log = get_logger(__name__)

DEFAULT_CACHE_DIR = Path("data/eval_cache")
MAX_CLIP_BYTES = 500 * 1024 * 1024
DOWNLOAD_TIMEOUT_S = 120.0
_READ_BLOCK = 1 << 20
_SUFFIX = re.compile(r"\.[a-z0-9]{1,5}")

Downloader = Callable[[str, Path], None]


def sha256_file(path: Path) -> str:
    """Return the hex sha256 of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(_READ_BLOCK):
            digest.update(block)
    return digest.hexdigest()


def http_download(url: str, dest: Path) -> None:
    """Stream `url` into `dest`, refusing anything over `MAX_CLIP_BYTES`.

    Raises:
        ClipFetchError: On an HTTP error or an oversized body.
    """
    received = 0
    try:
        with (
            httpx.stream("GET", url, follow_redirects=True, timeout=DOWNLOAD_TIMEOUT_S) as reply,
            dest.open("wb") as handle,
        ):
            reply.raise_for_status()
            for block in reply.iter_bytes(_READ_BLOCK):
                received += len(block)
                if received > MAX_CLIP_BYTES:
                    msg = f"{url} is larger than {MAX_CLIP_BYTES} bytes"
                    raise ClipFetchError(msg)
                handle.write(block)
    except httpx.HTTPError as exc:
        msg = f"could not download {url}: {exc}"
        raise ClipFetchError(msg) from exc


def cached_name(url: str, sha256: str) -> str:
    """Return the cache file name for a URL: its sha256 plus the URL's extension."""
    suffix = PurePosixPath(urlsplit(url).path).suffix.lower()
    return sha256 + (suffix if _SUFFIX.fullmatch(suffix) else "")


def resolve_clip(
    entry: ClipEntry,
    clip_dir: Path,
    *,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    download: Downloader = http_download,
) -> Path:
    """Return a verified local copy of the clip's video.

    Args:
        entry: The clip.
        clip_dir: The folder holding its `clip.yaml` (local paths are relative to it).
        cache_dir: Where URL sources are cached.
        download: Fetches a URL into a file (injected in tests; the only network access).

    Returns:
        The path of the video file.

    Raises:
        ClipFetchError: For a placeholder, a missing file, or a sha256 mismatch.
    """
    source = entry.source
    if source is None:
        msg = f"clip {entry.name!r} is a placeholder with no video yet"
        raise ClipFetchError(msg)
    if source.path is not None:
        path = (clip_dir / source.path).resolve()
        if not path.is_file():
            msg = f"clip {entry.name!r}: {path} does not exist"
            raise ClipFetchError(msg)
        _check(path, source.sha256, entry.name)
        return path
    if source.url is None or source.sha256 is None:  # the schema guarantees both
        msg = f"clip {entry.name!r} has an incomplete source"
        raise ClipFetchError(msg)
    cached = cache_dir / cached_name(source.url, source.sha256)
    if cached.is_file():
        _check(cached, source.sha256, entry.name)
        return cached
    cache_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=cache_dir, prefix=".download-")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        _log.info("eval_clip_download", clip=entry.name, url=source.url)
        download(source.url, tmp)
        _check(tmp, source.sha256, entry.name)
        tmp.replace(cached)
    finally:
        tmp.unlink(missing_ok=True)
    return cached


def _check(path: Path, expected: str | None, name: str) -> None:
    if expected is None:
        return
    actual = sha256_file(path)
    if actual != expected:
        msg = f"clip {name!r}: sha256 {actual} does not match the pinned {expected}"
        raise ClipFetchError(msg)
