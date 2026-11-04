"""Build one proof pack from a chain folder and write its JSON, HTML and PDF (eval and demos).

Usage (record Spark's replies into the replay cache while building):

    RULECAM_MODE=record uv run python scripts/compliance/proof_pack.py \
        --chain-root data/evidence/chain --site demo --plans data/pod-plans \
        --start 2026-11-01T00:00:00Z --end 2026-11-02T00:00:00Z --crew-lang pl \
        --out data/proof-eval

`--no-models` builds the numbers only (no summaries, no clause grounding).
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

from rulecam.bus import Bus
from rulecam.clients.meta_hosted.spark import SparkClient
from rulecam.clock import SystemClock
from rulecam.compliance.build import BuildInputs, build_pack
from rulecam.compliance.models import PackRequest
from rulecam.compliance.proofs import pack_hash
from rulecam.compliance.render import render_html
from rulecam.compliance.rules import ClauseFinder
from rulecam.config import get_settings
from rulecam.evidence.incident import report_document
from rulecam.planner.pipeline import PlanStore
from rulecam.planner.standards import StandardsLibrary
from rulecam.replay.cache import ReplayCache


def main() -> None:
    """Parse arguments, build the pack and write it."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chain-root", type=Path, required=True)
    parser.add_argument("--site", default="demo")
    parser.add_argument("--plans", type=Path, default=Path("plans"))
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--crew-lang", default="en")
    parser.add_argument("--ground-rules", type=int, default=4)
    parser.add_argument("--no-models", action="store_true")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    settings = get_settings()
    clock = SystemClock()
    bus = Bus()
    cache = ReplayCache.from_settings(settings, clock=clock, bus=bus)
    spark = (
        None
        if args.no_models
        else SparkClient.from_settings(settings, clock=clock, bus=bus, cache=cache)
    )
    root = settings.rulecam_data_dir / "compliance"
    clauses = ClauseFinder.live(
        spark, StandardsLibrary.from_settings(settings), root / "clauses.json"
    )
    request = PackRequest(
        start=datetime.fromisoformat(args.start),
        end=datetime.fromisoformat(args.end),
        crew_lang=args.crew_lang,
        models=not args.no_models,
        ground_rules=args.ground_rules,
    )
    started = time.monotonic()
    pack = build_pack(
        request,
        BuildInputs(
            chain_root=args.chain_root,
            site=args.site,
            plans=PlanStore(args.plans),
            roots_dir=Path("evidence/roots"),
            spark=spark,
            clauses=clauses,
            timezone=settings.rulecam_site_timezone,
            stills_root=settings.rulecam_data_dir / "strips",
        ),
    )
    seconds = time.monotonic() - started
    body = pack.model_dump(mode="json")
    digest = pack_hash(body)
    args.out.mkdir(parents=True, exist_ok=True)
    stem = args.out / pack.pack_id
    shared = {"pack": body, "pack_hash": digest, "expires_at": "2099-01-01T00:00:00Z"}
    stem.with_suffix(".json").write_text(json.dumps(shared), encoding="utf-8")
    html = render_html(pack, digest, None)
    stem.with_suffix(".html").write_text(html, encoding="utf-8")
    document, media = report_document(html)
    if media == "application/pdf":
        stem.with_suffix(".pdf").write_bytes(document)
    summary = {
        "pack_id": pack.pack_id,
        "pack_hash": digest,
        "build_s": round(seconds, 1),
        "records": len(pack.records),
        "metrics": len(pack.metrics),
        "rules": len(pack.rules),
        "pdf": media == "application/pdf",
    }
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
