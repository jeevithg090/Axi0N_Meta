"""Camera source URLs: what kind each is, how ffmpeg reads it, and how to keep secrets out of logs.

A small site's CCTV speaks RTSP (Hikvision, Dahua, Reolink, Axis, almost every NVR), some
cameras and relays serve HLS or MJPEG over HTTP, public live streams (YouTube live, Twitch) are
web pages that `yt-dlp` turns into an HLS URL, and a local clip can be looped as a "virtual
camera". `classify` names the kind from the URL; `input_args` gives ffmpeg's input options for
it (TCP RTSP with a socket timeout, HTTP with reconnects, a looped file read at its native rate).

Credentials are never part of a stored URL: `split_credentials` takes `user:pass@` out of a
pasted URL, `with_credentials` puts them back only when ffmpeg is started, and `redact` / `scrub`
remove them (and secret-looking query values) from anything shown or logged.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Final
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel

from rulecam.connectors.errors import SourceError

RTSP_SCHEMES: Final = frozenset({"rtsp", "rtsps"})
RTMP_SCHEMES: Final = frozenset({"rtmp", "rtmps"})
HTTP_SCHEMES: Final = frozenset({"http", "https"})
# Web pages (not media URLs) that yt-dlp resolves to a stream; any other http(s) URL is handed to
# ffmpeg as it is (MJPEG, HLS, MP4, FLV...).
PUBLIC_PAGE_HOSTS: Final = (
    "youtube.com",
    "youtu.be",
    "twitch.tv",
    "vimeo.com",
    "dailymotion.com",
    "kick.com",
    "facebook.com",
)
SECRET_QUERY_KEYS: Final = frozenset(
    {"user", "usr", "username", "pass", "passwd", "password", "pwd", "token", "key", "auth"}
    | {"sig", "signature", "access_token", "apikey", "api_key", "secret"}
)
RTSP_SOCKET_TIMEOUT_US: Final = 10_000_000
HTTP_RW_TIMEOUT_US: Final = 15_000_000
RESOLVE_TIMEOUT_S: Final = 45.0
REDACTED: Final = "***"
MIN_SECRET_CHARS: Final = 3
_USERINFO: Final = re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*://)[^/@\s'\"]+@")


class SourceKind(StrEnum):
    """How a camera's video reaches Rulecam."""

    RTSP = "rtsp"
    HLS = "hls"
    HTTP = "http"
    RTMP = "rtmp"
    PUBLIC = "public"
    FILE = "file"


class BrandTemplate(BaseModel):
    """A camera maker's usual RTSP URL, for the owner to fill in (`GET /connectors/brands`)."""

    brand: str
    main: str
    sub: str
    note: str = ""


BRAND_TEMPLATES: Final = (
    BrandTemplate(
        brand="Hikvision",
        main="rtsp://{host}:554/Streaming/Channels/101",
        sub="rtsp://{host}:554/Streaming/Channels/102",
        note="Channel N of an NVR is N01 (main) and N02 (sub).",
    ),
    BrandTemplate(
        brand="Dahua / Amcrest / Lorex",
        main="rtsp://{host}:554/cam/realmonitor?channel=1&subtype=0",
        sub="rtsp://{host}:554/cam/realmonitor?channel=1&subtype=1",
        note="On an NVR, channel is the camera number.",
    ),
    BrandTemplate(
        brand="Reolink",
        main="rtsp://{host}:554/h264Preview_01_main",
        sub="rtsp://{host}:554/h264Preview_01_sub",
        note="Newer H.265 models: h265Preview_01_main. Turn RTSP on in the app first.",
    ),
    BrandTemplate(
        brand="Axis",
        main="rtsp://{host}/axis-media/media.amp",
        sub="rtsp://{host}/axis-media/media.amp?resolution=640x360",
    ),
    BrandTemplate(
        brand="Uniview",
        main="rtsp://{host}:554/unicast/c1/s0/live",
        sub="rtsp://{host}:554/unicast/c1/s1/live",
    ),
    BrandTemplate(
        brand="TP-Link Tapo",
        main="rtsp://{host}:554/stream1",
        sub="rtsp://{host}:554/stream2",
        note="Uses the camera account made in the Tapo app, not the cloud login.",
    ),
    BrandTemplate(
        brand="Ubiquiti UniFi Protect",
        main="rtsps://{host}:7441/<token from Protect>",
        sub="rtsps://{host}:7441/<token from Protect>",
        note="Enable RTSPS per camera in Protect and copy its link (no user or password).",
    ),
)


class LivePreset(BaseModel):
    """A public live webcam for one-click demos (`GET /connectors/presets`).

    These are public broadcasts by their publishers (a TV network, webcam and tourism channels),
    connected like any `public` camera: read live, kept only as the normal rolling chunks, never
    used for face recognition. Their links can go offline; the camera tile then says so.
    """

    preset_id: str
    title: str
    url: str
    source: str
    shows: str
    label: str = "public live webcam"


LIVE_PRESETS: Final = (
    LivePreset(
        preset_id="shibuya-crossing",
        title="Shibuya Scramble Crossing, Tokyo",
        url="https://www.youtube.com/watch?v=5B4LmH4JbnY",
        source="ANN News (TV Asahi) on YouTube",
        shows="crowd: one of the busiest pedestrian crossings in the world",
    ),
    LivePreset(
        preset_id="times-square",
        title="Times Square Crossroads, New York",
        url="https://www.youtube.com/watch?v=KHy_n-EMWY8",
        source="EarthCam on YouTube",
        shows="crowd: a busy public square with crosswalks and traffic",
    ),
    LivePreset(
        preset_id="fishermans-village",
        title="Fisherman's Village shop street, Koh Samui",
        url="https://www.youtube.com/watch?v=bbBGNNPu0rg",
        source="The Real Samui Webcam on YouTube",
        shows="shops: a shopping street with a pharmacy and shopfronts",
    ),
)


def classify(url: str) -> SourceKind:
    """Name the kind of source a URL or path is.

    Args:
        url: An `rtsp://`, `rtmp://`, `http(s)://` URL, a `file://` URL or a local path.

    Returns:
        The kind; `http(s)` URLs ending in `.m3u8` are HLS, known video sites are `public`
        (resolved by yt-dlp), anything else over HTTP is handed to ffmpeg as it is.

    Raises:
        SourceError: For an empty value or a scheme ffmpeg is not allowed to read here.
    """
    text = url.strip()
    if not text:
        msg = "give a camera URL or a clip"
        raise SourceError(msg)
    parts = urlsplit(text)
    scheme = parts.scheme.lower()
    if scheme in RTSP_SCHEMES:
        return SourceKind.RTSP
    if scheme in RTMP_SCHEMES:
        return SourceKind.RTMP
    if scheme in HTTP_SCHEMES:
        host = (parts.hostname or "").lower()
        if any(host == h or host.endswith(f".{h}") for h in PUBLIC_PAGE_HOSTS):
            return SourceKind.PUBLIC
        if parts.path.lower().endswith(".m3u8"):
            return SourceKind.HLS
        return SourceKind.HTTP
    if scheme in ("", "file") or (len(scheme) == 1 and sys.platform == "win32"):
        return SourceKind.FILE
    msg = f"unsupported source scheme {scheme!r}: use rtsp, rtmp, http(s) or a video file"
    raise SourceError(msg)


def split_credentials(url: str) -> tuple[str, str | None, str | None]:
    """Take `user:password@` out of a URL.

    Args:
        url: A URL, possibly with user info.

    Returns:
        The URL without user info, the user name and the password (`None` when absent).
    """
    parts = urlsplit(url.strip())
    if parts.username is None and parts.password is None:
        return url.strip(), None, None
    host = parts.hostname or ""
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    netloc = f"{host}:{parts.port}" if parts.port is not None else host
    clean = urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    username = unquote(parts.username) if parts.username else None
    password = unquote(parts.password) if parts.password else None
    return clean, username, password


def with_credentials(url: str, username: str | None, password: str | None) -> str:
    """Put a user name and password into a URL (only ever handed to ffmpeg).

    Args:
        url: A URL without user info.
        username: The user name, or `None`.
        password: The password, or `None`.

    Returns:
        The URL with percent-encoded user info, or `url` unchanged without a user name.
    """
    if not username:
        return url
    parts = urlsplit(url)
    userinfo = quote(username, safe="")
    if password:
        userinfo += ":" + quote(password, safe="")
    netloc = f"{userinfo}@{parts.netloc.rsplit('@', 1)[-1]}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def redact(url: str) -> str:
    """A URL that is safe to show and log: no user info, secret-looking query values hidden.

    Args:
        url: Any URL or path.

    Returns:
        The redacted URL.
    """
    clean, username, _ = split_credentials(url)
    parts = urlsplit(clean)
    if parts.query:
        pairs = parse_qsl(parts.query, keep_blank_values=True)
        query = urlencode(
            [(k, REDACTED if k.lower() in SECRET_QUERY_KEYS and v else v) for k, v in pairs],
            safe="*",
        )
        clean = urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))
    if username:
        parts = urlsplit(clean)
        clean = urlunsplit(
            (parts.scheme, f"{REDACTED}@{parts.netloc}", parts.path, parts.query, parts.fragment)
        )
    return clean


def scrub(text: str, secrets: Iterable[str | None]) -> str:
    """Remove secrets from text ffmpeg or a camera wrote (errors often echo the URL).

    Args:
        text: The text.
        secrets: Passwords, user names and full URLs to hide.

    Returns:
        The text with every secret replaced and any `scheme://user:pass@` user info hidden.
    """
    out = _USERINFO.sub(lambda m: f"{m['scheme']}{REDACTED}@", text)
    for secret in sorted(
        {s for s in secrets if s and len(s) >= MIN_SECRET_CHARS}, key=len, reverse=True
    ):
        out = out.replace(secret, REDACTED).replace(quote(secret, safe=""), REDACTED)
    return out


@dataclass(frozen=True)
class ResolvedInput:
    """What one ffmpeg start reads: its input options and what must never be shown.

    Attributes:
        args: ffmpeg input options, ending with `-i <input>`.
        secrets: Strings to scrub from ffmpeg's output (passwords, resolved URLs).
        title: What the source calls itself (a public stream's title), if anything.
        live: Whether the source is live (a recorded public video is read at its native rate).
    """

    args: list[str]
    secrets: tuple[str, ...] = ()
    title: str | None = None
    live: bool = True
    extra: dict[str, str] = field(default_factory=dict)


def input_args(
    kind: SourceKind, url: str, *, loop: bool = True, realtime: bool = False
) -> list[str]:
    """The ffmpeg input options for a source.

    Args:
        kind: The source kind.
        url: The URL ffmpeg opens (with credentials, for RTSP) or the file path.
        loop: For files: loop forever, as a virtual camera.
        realtime: Read at the native frame rate (files always are).

    Returns:
        Options to place before the output options.
    """
    if kind is SourceKind.RTSP:
        return ["-rtsp_transport", "tcp", "-timeout", str(RTSP_SOCKET_TIMEOUT_US), "-i", url]
    if kind is SourceKind.RTMP:
        return ["-rw_timeout", str(HTTP_RW_TIMEOUT_US), "-i", url]
    if kind is SourceKind.FILE:
        return ["-re", *(["-stream_loop", "-1"] if loop else []), "-i", url]
    return [
        *(["-re"] if realtime else []),
        # A live HLS playlist starts at its newest segment, not three back (no catch-up burst).
        *(["-live_start_index", "-1"] if kind is SourceKind.HLS else []),
        "-reconnect",
        "1",
        "-reconnect_streamed",
        "1",
        "-reconnect_on_network_error",
        "1",
        "-reconnect_delay_max",
        "5",
        "-rw_timeout",
        str(HTTP_RW_TIMEOUT_US),
        "-i",
        url,
    ]


def yt_dlp_command() -> list[str] | None:
    """How to run yt-dlp here: its executable, else the module in this environment, else `None`."""
    exe = shutil.which("yt-dlp")
    if exe:
        return [exe]
    if importlib.util.find_spec("yt_dlp") is None:
        return None
    return [sys.executable, "-m", "yt_dlp"]


def resolve_public(url: str, *, max_height: int = 720) -> ResolvedInput:
    """Turn a public video page (YouTube live, Twitch...) into ffmpeg input options with yt-dlp.

    Resolved URLs expire after a few hours, so this runs again on every (re)connect.

    Args:
        url: The page URL.
        max_height: The tallest format to pick.

    Returns:
        HTTP input options for the stream, with the resolved URL marked secret.

    Raises:
        SourceError: Without yt-dlp, or when it cannot find a stream.
    """
    command = yt_dlp_command()
    if command is None:
        msg = "public streams need yt-dlp: `uv sync --extra connectors` or install yt-dlp"
        raise SourceError(msg)
    cap = f"[height<={max_height}]"
    fmt = f"bv*{cap}[protocol^=m3u8]/bv*{cap}/b{cap}/bv*/b"  # video only: audio is dropped
    args = [*command, "--no-warnings", "--no-playlist", "-f", fmt, "-j", "--", url]
    try:
        result = subprocess.run(  # noqa: S603 - argument list, never a shell
            args, capture_output=True, text=True, timeout=RESOLVE_TIMEOUT_S, check=False
        )
    except subprocess.TimeoutExpired as exc:
        msg = f"yt-dlp took over {RESOLVE_TIMEOUT_S:.0f} s to find the stream"
        raise SourceError(msg) from exc
    if result.returncode != 0:
        tail = scrub(result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "", [])
        msg = f"yt-dlp could not open it: {tail[:300] or result.returncode}"
        raise SourceError(msg)
    try:
        info = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        msg = "yt-dlp gave no stream description"
        raise SourceError(msg) from exc
    media = info.get("url")
    if not isinstance(media, str) or not media:
        msg = "yt-dlp found no single stream URL for it"
        raise SourceError(msg)
    live = bool(info.get("is_live"))
    headers = info.get("http_headers") or {}
    agent = headers.get("User-Agent") if isinstance(headers, dict) else None
    options = ["-user_agent", str(agent)] if agent else []
    kind = SourceKind.HLS if "m3u8" in str(info.get("protocol", "")) else SourceKind.HTTP
    return ResolvedInput(
        args=[*options, *input_args(kind, media, realtime=not live)],
        secrets=(media,),
        title=str(info.get("title") or "") or None,
        live=live,
    )


def local_file(path_text: str, allowed_roots: Iterable[Path]) -> Path:
    """A local video file the owner named, inside one of the allowed folders.

    Args:
        path_text: A path or `file://` URL.
        allowed_roots: Folders clips may be read from (the data folder, the working folder).

    Returns:
        The resolved path.

    Raises:
        SourceError: When the file is missing or outside the allowed folders.
    """
    text = path_text.strip()
    if text.startswith("file://"):
        text = unquote(urlsplit(text).path)
    path = Path(text).expanduser().resolve()
    roots = [root.resolve() for root in allowed_roots]
    if not any(path.is_relative_to(root) for root in roots):
        msg = "clips must be uploaded, or be inside the Rulecam data or working folder"
        raise SourceError(msg)
    if not path.is_file():
        msg = f"no such clip: {path.name}"
        raise SourceError(msg)
    return path
