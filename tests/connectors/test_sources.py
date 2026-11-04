"""Camera source URLs: kinds, ffmpeg inputs, credentials kept out of everything shown."""

from __future__ import annotations

from pathlib import Path

import pytest

from rulecam.connectors import sources
from rulecam.connectors.errors import SourceError
from rulecam.connectors.sources import (
    SourceKind,
    classify,
    input_args,
    local_file,
    redact,
    resolve_public,
    scrub,
    split_credentials,
    with_credentials,
)


@pytest.mark.parametrize(
    ("url", "kind"),
    [
        ("rtsp://10.0.0.5:554/Streaming/Channels/101", SourceKind.RTSP),
        ("rtsps://nvr.local:7441/abc", SourceKind.RTSP),
        ("rtmp://relay/live/cam", SourceKind.RTMP),
        ("https://cdn.example.com/live/index.m3u8?token=x", SourceKind.HLS),
        ("http://10.0.0.9/mjpg/video.mjpg", SourceKind.HTTP),
        ("https://www.youtube.com/watch?v=abc", SourceKind.PUBLIC),
        ("https://youtu.be/abc", SourceKind.PUBLIC),
        ("https://m.twitch.tv/somecam", SourceKind.PUBLIC),
        ("data/clips/shop.mp4", SourceKind.FILE),
        ("file:///tmp/a.mp4", SourceKind.FILE),
    ],
)
def test_classify(url: str, kind: SourceKind) -> None:
    assert classify(url) is kind


@pytest.mark.parametrize("url", ["", "   ", "ftp://x/y", "javascript:alert(1)"])
def test_classify_rejects(url: str) -> None:
    with pytest.raises(SourceError):
        classify(url)


def test_split_and_rebuild_credentials() -> None:
    clean, user, password = split_credentials("rtsp://ad%40min:p%3Ass@10.0.0.5:554/s?ch=1")
    assert clean == "rtsp://10.0.0.5:554/s?ch=1"
    assert (user, password) == ("ad@min", "p:ss")
    full = with_credentials(clean, user, password)
    assert full == "rtsp://ad%40min:p%3Ass@10.0.0.5:554/s?ch=1"
    assert split_credentials(full) == (clean, user, password)


def test_split_without_credentials_and_ipv6() -> None:
    assert split_credentials("rtsp://cam/x") == ("rtsp://cam/x", None, None)
    clean, user, _ = split_credentials("rtsp://u:p@[fe80::1]:554/x")
    assert clean == "rtsp://[fe80::1]:554/x"
    assert user == "u"


def test_with_credentials_needs_a_user() -> None:
    assert with_credentials("rtsp://cam/x", None, "pw") == "rtsp://cam/x"
    assert with_credentials("rtsp://cam/x", "admin", None) == "rtsp://admin@cam/x"


def test_redact_hides_user_info_and_secret_query_values() -> None:
    shown = redact("http://admin:hunter22@cam/video.cgi?user=admin&pwd=hunter22&res=hd")
    assert "hunter22" not in shown
    assert "admin:" not in shown
    assert shown.startswith("http://***@cam/video.cgi?")
    assert "res=hd" in shown


def test_scrub_removes_passwords_and_user_info() -> None:
    text = (
        "[error] Error opening input file rtsp://admin:s3cr%21t@10.0.0.5/x. auth failed for s3cr!t"
    )
    out = scrub(text, ["s3cr!t", None, "ab"])
    assert "s3cr" not in out
    assert "rtsp://***@10.0.0.5/x" in out


def test_input_args_per_kind() -> None:
    rtsp = input_args(SourceKind.RTSP, "rtsp://cam/x")
    assert rtsp[:2] == ["-rtsp_transport", "tcp"]
    assert rtsp[-2:] == ["-i", "rtsp://cam/x"]
    clip = input_args(SourceKind.FILE, "a.mp4")
    assert clip == ["-re", "-stream_loop", "-1", "-i", "a.mp4"]
    assert "-stream_loop" not in input_args(SourceKind.FILE, "a.mp4", loop=False)
    hls = input_args(SourceKind.HLS, "https://x/i.m3u8")
    assert "-live_start_index" in hls
    assert "-reconnect" in hls
    http = input_args(SourceKind.HTTP, "http://x/v.mjpg", realtime=True)
    assert http[0] == "-re"
    assert "-live_start_index" not in http
    assert input_args(SourceKind.RTMP, "rtmp://r/l")[-1] == "rtmp://r/l"


def test_local_file_must_be_inside_allowed_roots(tmp_path: Path) -> None:
    clip = tmp_path / "data" / "a.mp4"
    clip.parent.mkdir()
    clip.write_bytes(b"x")
    assert local_file(f"file://{clip}", [tmp_path / "data"]) == clip.resolve()
    with pytest.raises(SourceError, match="inside"):
        local_file("/etc/passwd", [tmp_path / "data"])
    with pytest.raises(SourceError, match="no such clip"):
        local_file(str(tmp_path / "data" / "missing.mp4"), [tmp_path / "data"])


def test_resolve_public_without_yt_dlp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sources, "yt_dlp_command", lambda: None)
    with pytest.raises(SourceError, match="yt-dlp"):
        resolve_public("https://www.youtube.com/watch?v=x")


def test_resolve_public_reads_yt_dlp_json(monkeypatch: pytest.MonkeyPatch) -> None:
    class Done:
        returncode = 0
        stderr = ""
        stdout = (
            '{"url": "https://media.example/abc/index.m3u8", "is_live": true, '
            '"protocol": "m3u8_native", "title": "Town square", '
            '"http_headers": {"User-Agent": "UA/1"}}'
        )

    calls: list[list[str]] = []

    def fake_run(args: list[str], **_: object) -> Done:
        calls.append(args)
        return Done()

    monkeypatch.setattr(sources, "yt_dlp_command", lambda: ["yt-dlp"])
    monkeypatch.setattr("rulecam.connectors.sources.subprocess.run", fake_run)
    resolved = resolve_public("https://www.youtube.com/watch?v=x")
    assert resolved.title == "Town square"
    assert resolved.live is True
    assert resolved.secrets == ("https://media.example/abc/index.m3u8",)
    assert resolved.args[:2] == ["-user_agent", "UA/1"]
    assert "-live_start_index" in resolved.args
    assert "-re" not in resolved.args
    assert calls[0][-1] == "https://www.youtube.com/watch?v=x"


def test_resolve_public_reports_yt_dlp_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    class Failed:
        returncode = 1
        stdout = ""
        stderr = "ERROR: [youtube] x: Video unavailable"

    monkeypatch.setattr(sources, "yt_dlp_command", lambda: ["yt-dlp"])
    monkeypatch.setattr("rulecam.connectors.sources.subprocess.run", lambda *a, **k: Failed())
    with pytest.raises(SourceError, match="Video unavailable"):
        resolve_public("https://www.youtube.com/watch?v=x")
