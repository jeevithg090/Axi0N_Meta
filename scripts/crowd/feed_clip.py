"""Feed a crowd clip into a running API in real time, as a paired phone, with its watch and truth.

The replay half of the crowd demo: the clip is cut into 3 s chunks the way a phone sends them,
paired as a new camera, and uploaded at the pace it was filmed. As soon as the camera exists, the
sample folder's files are applied to it (`CAMERA` in them becomes the new camera id):

- `venue.json` -> `PUT /crowd/venue` (a SAMPLE layout),
- `truth.json` -> `PUT /crowd/truth/{camera}` (ground truth, for the report's score),
- `watch.json` -> `POST /crowd/watch` (the operator's instruction, floor points and zones).

With `--setup`, Muse Spark's proposal for the camera (`POST /crowd/setup`) is fetched after the
first chunks and saved next to the feed log, before the sample watch starts.

Usage:
    uv run python scripts/crowd/feed_clip.py --clip small-030_c_56_h0.mp4
        --sample samples/crowd/juelich-030 --out /tmp/feed --label "Gate replay"
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

from rulecam.eval.client import HttpApiClient, Pairing
from rulecam.eval.errors import EvalRunError
from rulecam.eval.runner import split_clip

TOO_MANY = "429"


def upload(api: HttpApiClient, pairing: Pairing, path: Path) -> str:
    """Upload one chunk, waiting out the API's one-request-a-second pairing limit."""
    for _ in range(5):
        try:
            return api.upload(pairing, path).chunk_id
        except EvalRunError as exc:
            if TOO_MANY not in str(exc):
                raise
            time.sleep(1.1)
    return api.upload(pairing, path).chunk_id


def sample(folder: Path, name: str, camera_id: str) -> object | None:
    """A sample file with `CAMERA` replaced by the camera id (`None`: no such file)."""
    path = folder / name
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8").replace('"CAMERA"', json.dumps(camera_id)))


def configure(http: httpx.Client, folder: Path, camera_id: str, log: dict[str, object]) -> None:
    """Apply the sample folder's venue, truth and watch to the camera."""
    for name, method, path in (
        ("venue.json", "PUT", "/crowd/venue"),
        ("truth.json", "PUT", f"/crowd/truth/{camera_id}"),
        ("watch.json", "POST", "/crowd/watch"),
    ):
        body = sample(folder, name, camera_id)
        if body is None:
            continue
        reply = http.request(method, path, json=body)
        log[name] = {"status": reply.status_code, "at": datetime.now(UTC).isoformat()}
        reply.raise_for_status()


def main() -> None:
    """Feed the clip."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--clip", type=Path, required=True)
    parser.add_argument("--sample", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--label", default="Crowd replay")
    parser.add_argument("--setup", action="store_true", help="Ask Spark for a proposal first.")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    api = HttpApiClient(args.api)
    http = httpx.Client(base_url=args.api, timeout=180.0)
    chunks = split_clip(args.clip, args.out / "chunks")
    pairing = api.pair(args.label)
    time.sleep(1.2)  # the API takes one request per second per pairing
    log: dict[str, object] = {"camera_id": pairing.camera_id, "chunks": []}
    sent: list[dict[str, object]] = []
    start = time.monotonic()
    for chunk in chunks:
        wait = start + chunk.clip_start_s - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        at = datetime.now(UTC).isoformat()
        sent.append(
            {
                "index": chunk.index,
                "clip_start_s": chunk.clip_start_s,
                "sent_utc": at,
                "chunk_id": upload(api, pairing, chunk.path),
            }
        )
        log["chunks"] = sent
        if chunk.index == 0:
            if args.setup:
                proposal = http.post(
                    "/crowd/setup",
                    json={"camera_id": pairing.camera_id, "instruction": _instruction(args)},
                )
                (args.out / "setup.json").write_text(proposal.text, encoding="utf-8")
            configure(http, args.sample, pairing.camera_id, log)
        (args.out / "feed_log.json").write_text(json.dumps(log, indent=1), encoding="utf-8")


def _instruction(args: argparse.Namespace) -> str:
    watch = sample(args.sample, "watch.json", "CAMERA")
    return str(watch["instruction"]) if isinstance(watch, dict) else "Warn us before it is unsafe."


if __name__ == "__main__":
    main()
