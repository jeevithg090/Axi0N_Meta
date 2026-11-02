r"""Burst load test of the serverless SAM and Glimmer endpoints, cold and warm (add-on 1.6).

Replays real inputs: keyframes from recorded chunks (`--chunks`, MP4s, 6 frames each) to SAM's
`/segment_image`, then evidence strips (`--strips`, folders of JPEGs) to Glimmer's
`/chat/completions`. For each burst size (1, 5, 20 by default) it runs a **cold** burst (after
both endpoints have scaled to zero) and a **warm** one straight after, and reports p50 / p95 per
stage and dollars per verified alert (`rulecam.eval.loadtest`).

Endpoints and keys come from environment variables only:

    SAM_BASE_URL, SAM_API_KEY, SAM_EDGE_KEY (the RunPod key the load balancer checks)
    GLIMMER_BASE_URL (ends in /v1), GLIMMER_API_KEY, GLIMMER_EDGE_KEY, GLIMMER_MODEL
    RUNPOD_API_KEY (optional: worker counts for measured cost and for detecting "cold")
    SAM_ENDPOINT_ID, GLIMMER_ENDPOINT_ID (optional: default from https://<id>.api.runpod.ai)

Run from the API pod (same data center as the endpoints, as the deploy.md figures were), e.g.:

    set -a; . ./.env; set +a
    export SAM_EDGE_KEY=$(cat .sam_edge_key) GLIMMER_EDGE_KEY=$(cat .glimmer_edge_key)
    uv run python scripts/eval/serverless_load.py --strips data/strips --chunks data/chunks \
      --out reports/eval/$(date +%F)-serverless-load

A full run (three cold waits of about 5.5 minutes) takes 20 to 30 minutes and bills a few dollars.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import cv2
import httpx

from rulecam.eval.loadtest import (
    IDLE_TIMEOUT_S,
    Endpoint,
    JobResult,
    Payload,
    ScenarioReport,
    integrate_workers,
    live_workers,
    render_markdown,
    run_burst,
    run_job,
    scenario_report,
    to_json,
)

HEALTH = "https://api.runpod.ai/v2/{id}/health"
KEYFRAMES = 6
SAMPLE_EVERY_S = 5.0


def env(name: str, default: str | None = None) -> str:
    """A required environment variable (or its default)."""
    value = os.environ.get(name, default)
    if not value:
        raise SystemExit(f"set {name}")
    return value


def endpoint_id(url: str, override: str | None) -> str | None:
    """The RunPod endpoint id: the override, else the first label of `<id>.api.runpod.ai`."""
    if override:
        return override
    host = urlsplit(url).hostname or ""
    return host.split(".", 1)[0] if host.endswith(".api.runpod.ai") else None


def keyframes(video: Path, n: int = KEYFRAMES) -> tuple[tuple[bytes, ...], int, int]:
    """`n` evenly spread frames of a chunk as JPEG, and the frame size."""
    capture = cv2.VideoCapture(str(video))
    total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT)) or n
    out, size = [], (0, 0)
    for k in range(n):
        capture.set(cv2.CAP_PROP_POS_FRAMES, round(k * (total - 1) / max(1, n - 1)))
        ok, frame = capture.read()
        if not ok:
            continue
        size = (frame.shape[1], frame.shape[0])
        out.append(cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes())
    capture.release()
    return tuple(out), size[0], size[1]


def load_payloads(strips: Path, chunks: Path | None, limit: int) -> list[Payload]:
    """Pair recorded strips with recorded chunks' keyframes (strip frames when no chunks)."""
    folders = sorted({p.parent for p in strips.rglob("*.jpg") if ".part" not in p.parent.name})
    videos = sorted(chunks.rglob("*.mp4")) if chunks else []
    out = []
    for i, folder in enumerate(folders[:limit]):
        frame_files = sorted(folder.glob("*.jpg"), key=lambda p: (len(p.stem), p.stem))
        strip = tuple(p.read_bytes() for p in frame_files)
        if videos:
            frames, width, height = keyframes(videos[i % len(videos)])
        else:
            image = cv2.imread(str(frame_files[0]))
            if image is None:
                continue
            frames, width, height = strip[:KEYFRAMES], image.shape[1], image.shape[0]
        if frames and strip:
            out.append(Payload(keyframes=frames, strip=strip, width=width, height=height))
    if not out:
        raise SystemExit(f"no strips under {strips}")
    return out


async def workers(client: httpx.AsyncClient, ep: Endpoint, key: str | None) -> int | None:
    """Live workers of an endpoint from RunPod's health route (`None` without a key or id)."""
    if not key or not ep.endpoint_id:
        return None
    try:
        reply = await client.get(
            HEALTH.format(id=ep.endpoint_id), headers={"Authorization": f"Bearer {key}"}
        )
        reply.raise_for_status()
        return live_workers(reply.json())
    except (httpx.HTTPError, ValueError):
        return None


async def wait_cold(
    client: httpx.AsyncClient, eps: list[Endpoint], key: str | None, wait_s: float
) -> None:
    """Wait until both endpoints report no worker (or `wait_s` without a RunPod key)."""
    if not key:
        print(f"waiting {wait_s:.0f} s for the endpoints to scale to zero")
        await asyncio.sleep(wait_s)
        return
    deadline = time.monotonic() + max(wait_s, 3 * IDLE_TIMEOUT_S)
    while time.monotonic() < deadline:
        counts = [await workers(client, ep, key) for ep in eps]
        if all(c == 0 for c in counts):
            return
        print(f"workers still up: {counts}; waiting")
        await asyncio.sleep(15)
    print("endpoints did not scale to zero in time; the burst is not truly cold", file=sys.stderr)


async def scenario(
    client: httpx.AsyncClient,
    *,
    burst: int,
    state: str,
    sam: Endpoint,
    glimmer: Endpoint,
    payloads: list[Payload],
    model: str,
    runpod_key: str | None,
) -> ScenarioReport:
    """Run one burst while sampling worker counts; summarise it."""
    samples: dict[str, list[tuple[float, int]]] = {"sam": [], "glimmer": []}
    done = asyncio.Event()

    async def sample() -> None:
        while True:
            for ep in (sam, glimmer):
                count = await workers(client, ep, runpod_key)
                if count is not None:
                    samples[ep.name].append((time.monotonic(), count))
            if done.is_set():
                return
            await asyncio.sleep(SAMPLE_EVERY_S)

    async def job(i: int) -> JobResult:
        payload = payloads[i % len(payloads)]
        return await run_job(client, sam=sam, glimmer=glimmer, payload=payload, model=model)

    sampler = asyncio.create_task(sample())
    started = time.monotonic()
    results = await run_burst(burst, job)
    wall = time.monotonic() - started
    done.set()
    await sampler
    measured = None
    if all(len(s) >= 2 for s in samples.values()):  # noqa: PLR2004 - two samples make a span
        measured = {name: integrate_workers(s) for name, s in samples.items()}
    report = scenario_report(
        burst=burst, state=state, results=results, wall_s=wall, worker_s=measured
    )
    print(render_markdown([report]).splitlines()[-1])
    return report


async def main_async(args: argparse.Namespace) -> int:
    """Run every scenario and write the report."""
    sam_url = env("SAM_BASE_URL")
    glimmer_url = env("GLIMMER_BASE_URL")
    sam = Endpoint(
        "sam",
        sam_url,
        Endpoint.auth(env("SAM_API_KEY"), os.environ.get("SAM_EDGE_KEY")),
        endpoint_id(sam_url, os.environ.get("SAM_ENDPOINT_ID")),
    )
    glimmer = Endpoint(
        "glimmer",
        glimmer_url,
        Endpoint.auth(env("GLIMMER_API_KEY"), os.environ.get("GLIMMER_EDGE_KEY")),
        endpoint_id(glimmer_url, os.environ.get("GLIMMER_ENDPOINT_ID")),
    )
    model = env("GLIMMER_MODEL", "muse-glimmer-30b")
    runpod_key = os.environ.get("RUNPOD_API_KEY")
    payloads = load_payloads(args.strips, args.chunks, args.max_payloads)
    reports = []
    timeout = httpx.Timeout(args.timeout_s, connect=30.0)
    limits = httpx.Limits(max_connections=200, max_keepalive_connections=50)
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        for burst in args.bursts:
            for state in args.states:
                if state == "cold":
                    await wait_cold(client, [sam, glimmer], runpod_key, args.cold_wait_s)
                reports.append(
                    await scenario(
                        client,
                        burst=burst,
                        state=state,
                        sam=sam,
                        glimmer=glimmer,
                        payloads=payloads,
                        model=model,
                        runpod_key=runpod_key,
                    )
                )
    meta = {
        "generated_at": datetime.now(UTC).isoformat(),
        "sam_endpoint": sam.endpoint_id,
        "glimmer_endpoint": glimmer.endpoint_id,
        "payloads": len(payloads),
        "strip_frames": sorted({len(p.strip) for p in payloads}),
        "usd_per_worker_hour": 1.34,
        "note": "worker time measured from RunPod health samples when RUNPOD_API_KEY is set, "
        "else a lower-bound estimate (request time union + one 300 s idle tail per endpoint)",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.with_suffix(".json").write_text(to_json(reports, meta), encoding="utf-8")
    args.out.with_suffix(".md").write_text(render_markdown(reports), encoding="utf-8")
    print(render_markdown(reports))
    print(f"wrote {args.out.with_suffix('.json')} and .md")
    return 0 if all(r.ok for r in reports) else 1


def main(argv: list[str]) -> int:
    """Parse arguments and run."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--strips", type=Path, default=Path("data/strips"))
    parser.add_argument("--chunks", type=Path, default=None)
    parser.add_argument("--bursts", type=int, nargs="+", default=[1, 5, 20])
    parser.add_argument("--states", nargs="+", choices=["cold", "warm"], default=["cold", "warm"])
    parser.add_argument("--cold-wait-s", type=float, default=IDLE_TIMEOUT_S + 30)
    parser.add_argument("--timeout-s", type=float, default=300.0)
    parser.add_argument("--max-payloads", type=int, default=40)
    parser.add_argument("--out", type=Path, default=Path("reports/eval/serverless-load"))
    args = parser.parse_args(argv)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
