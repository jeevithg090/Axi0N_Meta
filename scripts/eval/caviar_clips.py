"""Write CAVIAR clips into the eval set with their labelled events (add-on 1.3).

Each clip becomes `eval/clips/caviar_<name>/clip.yaml` (the video pinned by URL + sha256, so
`rulecam eval fetch` caches it) plus a MOT `gt.txt` for tracking metrics. Events come from
CAVIAR's own activity labels (`rulecam.eval.caviar_events`): falls, fights, left bags in the
INRIA lobby clips, shop entries in the Lisbon corridor clips. Negative controls (clips where the
event never happens) make false alerts countable.

    # the corridor clips already on disk (data/testclips/caviar: <Name>.xml + <Name>.mpg)
    uv run python scripts/eval/caviar_clips.py corridor --src data/testclips/caviar
    # the INRIA lobby clips: download each clip's XML and MPEG once, then write the entries
    uv run python scripts/eval/caviar_clips.py inria --src data/caviar/inria --fetch
    # (or only placeholders, listing the planned rules, with nothing downloaded)
    uv run python scripts/eval/caviar_clips.py inria --placeholders
    # what labels a file uses (to check an event type's needles against the data)
    uv run python scripts/eval/caviar_clips.py inventory data/caviar/inria/rffgt.xml

Then `uv run rulecam eval validate` and, on the API pod, `rulecam eval run --clip caviar_...`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from rulecam.eval.caviar import CaviarSequence, label_inventory, load_caviar, mot_rows
from rulecam.eval.caviar_events import RulePlan, clip_entry, placeholder_entry
from rulecam.eval.fetch import http_download, sha256_file

BASE = "https://groups.inf.ed.ac.uk/vision/DATASETS/CAVIAR"
EVAL_SET = Path("eval/clips")
FALL, FIGHT, BAG, SHOP = "fall", "fight", "left_bag", "shop_enter"


@dataclass(frozen=True)
class CaviarClip:
    """One CAVIAR clip: its folder name upstream, its ground-truth file, and what to score."""

    name: str
    gt_file: str
    rules: tuple[RulePlan, ...]
    description: str


def pos(*events: str) -> tuple[RulePlan, ...]:
    """Rules the clip is labelled positive for."""
    return tuple(RulePlan(e) for e in events)


def neg(*events: str) -> tuple[RulePlan, ...]:
    """Rules the clip is a negative control for."""
    return tuple(RulePlan(e, negative=True) for e in events)


INRIA = (
    CaviarClip("Rest_FallOnFloor", "rffgt.xml", pos(FALL), "A person falls to the lobby floor."),
    CaviarClip(
        "Fight_OneManDown", "fomdgt1.xml", pos(FIGHT, FALL), "A fight; one man is knocked down."
    ),
    CaviarClip("Fight_RunAway1", "fra1gt.xml", pos(FIGHT), "Two people fight, one runs away."),
    CaviarClip("Fight_RunAway2", "fra2gt.xml", pos(FIGHT), "Two people fight, one runs away."),
    CaviarClip("Fight_Chase", "fcgt.xml", pos(FIGHT), "A fight and a chase."),
    CaviarClip("LeftBag", "lb1gt.xml", pos(BAG), "A person leaves a bag and walks off."),
    CaviarClip("LeftBag_AtChair", "lb2gt.xml", pos(BAG), "A bag is left at a chair."),
    CaviarClip("LeftBag_BehindChair", "lbbcgt.xml", pos(BAG), "A bag is left behind a chair."),
    CaviarClip("LeftBox", "lbgt.xml", pos(BAG), "A box is left in the lobby."),
    CaviarClip("LeftBag_PickedUp", "lbpugt.xml", pos(BAG), "A bag is left, then picked up."),
    CaviarClip("Rest_InChair", "ricgt.xml", neg(FALL), "A person rests in a chair (no fall)."),
    CaviarClip(
        "Meet_WalkTogether1", "mwt1gt.xml", neg(FIGHT), "Two people meet and walk on (no fight)."
    ),
    CaviarClip(
        "Browse1", "br1gt.xml", neg(FALL, FIGHT, BAG), "A person browses (no fall/fight/bag)."
    ),
)
CORRIDOR = tuple(
    CaviarClip(name, gt, pos(SHOP), f"Shopping-centre corridor: {what}.")
    for name, gt, what in (
        ("WalkByShop1cor", "cwbs1gt.xml", "people walk past and into the shops"),
        ("TwoEnterShop1cor", "c2es1gt.xml", "two people enter a shop"),
        ("ShopAssistant2cor", "csa2gt.xml", "a shop assistant and passers-by"),
        ("ThreePastShop1cor", "c3ps1gt.xml", "three people walk past a shop"),
        ("OneStopEnter2cor", "cose2gt.xml", "one person stops, then enters"),
        ("OneShopOneWait1cor", "cosow1gt.xml", "one person shops, one waits"),
        ("EnterExitCrossingPaths1cor", "ceecp1gt.xml", "people enter, exit and cross"),
    )
)
SETS = {
    "inria": ("CAVIARDATA1", "caviar-inria-lobby", INRIA),
    "corridor": ("CAVIARDATA2", "caviar-lisbon-corridor", CORRIDOR),
}


def slug(name: str) -> str:
    """`Rest_FallOnFloor` -> `caviar_rest_fall_on_floor` (an eval-set clip name)."""
    snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).replace("__", "_").lower()
    return f"caviar_{snake}"


def local_files(clip: CaviarClip, src: Path, data_dir: str, *, fetch: bool) -> tuple[Path, Path]:
    """The clip's XML and MPEG under `src` (`<Name>.xml` or the upstream name), fetched if asked."""
    xml = next((p for p in (src / f"{clip.name}.xml", src / clip.gt_file) if p.is_file()), None)
    video = src / f"{clip.name}.mpg"
    if fetch:
        src.mkdir(parents=True, exist_ok=True)
        if xml is None:
            xml = src / f"{clip.name}.xml"
            http_download(f"{BASE}/{data_dir}/{clip.name}/{clip.gt_file}", xml)
        if not video.is_file():
            http_download(f"{BASE}/{data_dir}/{clip.name}/{clip.name}.mpg", video)
    if xml is None or not video.is_file():
        msg = f"{clip.name}: need {clip.name}.xml (or {clip.gt_file}) and {clip.name}.mpg in {src}"
        raise SystemExit(msg)
    return xml, video


def write_clip(
    clip: CaviarClip,
    sequence: CaviarSequence,
    video: Path,
    *,
    data_dir: str,
    camera: str,
    out: Path,
) -> dict[str, object]:
    """Write one clip's `clip.yaml` and `gt.txt`; return the entry."""
    name = slug(clip.name)
    folder = out / name
    folder.mkdir(parents=True, exist_ok=True)
    rows, _ = mot_rows(sequence)
    (folder / "gt.txt").write_text("".join(r + "\n" for r in rows), encoding="utf-8")
    entry = clip_entry(
        sequence,
        name=name,
        camera_id=camera,
        url=f"{BASE}/{data_dir}/{clip.name}/{clip.name}.mpg",
        sha256=sha256_file(video),
        rules=clip.rules,
        description=clip.description,
        tracks_file="gt.txt",
    )
    header = (
        f"# CAVIAR {clip.name} ({clip.gt_file}). Generated by scripts/eval/caviar_clips.py from\n"
        "# CAVIAR's own activity labels: edit the script, not this file.\n"
    )
    text = header + yaml.safe_dump(entry, sort_keys=False, allow_unicode=True, width=100)
    (folder / "clip.yaml").write_text(text, encoding="utf-8")
    return entry


def write_placeholder(
    clip: CaviarClip, *, which: str, data_dir: str, camera: str, out: Path
) -> None:
    """Write a placeholder `clip.yaml` (rules and requests, no labels or video yet)."""
    folder = out / slug(clip.name)
    folder.mkdir(parents=True, exist_ok=True)
    entry = placeholder_entry(
        name=slug(clip.name),
        camera_id=camera,
        rules=clip.rules,
        description=clip.description,
        url=f"{BASE}/{data_dir}/{clip.name}/{clip.name}.mpg",
    )
    header = (
        f"# PLACEHOLDER for CAVIAR {clip.name}: `scripts/eval/caviar_clips.py {which} --src DIR\n"
        "# --fetch` downloads its labels and video and replaces this with the labelled entry.\n"
    )
    text = header + yaml.safe_dump(entry, sort_keys=False, allow_unicode=True, width=100)
    (folder / "clip.yaml").write_text(text, encoding="utf-8")
    print(f"{slug(clip.name)}: placeholder")


def build(which: str, src: Path | None, out: Path, *, fetch: bool, only: list[str] | None) -> int:
    """Write every clip of one CAVIAR set (placeholders when `src` is None); returns exit code."""
    data_dir, camera, clips = SETS[which]
    for clip in clips:
        if only and clip.name not in only:
            continue
        if src is None:
            write_placeholder(clip, which=which, data_dir=data_dir, camera=camera, out=out)
            continue
        xml, video = local_files(clip, src, data_dir, fetch=fetch)
        entry = write_clip(clip, load_caviar(xml), video, data_dir=data_dir, camera=camera, out=out)
        rules = entry["rules"]
        assert isinstance(rules, list)  # noqa: S101 - built above
        summary = ", ".join(f"{r['id']}={r['status']}:{len(r.get('events', []))}" for r in rules)
        print(f"{entry['name']}: {summary}")
    return 0


def main(argv: list[str]) -> int:
    """Parse the command line and run one sub-command; returns an exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for which in SETS:
        cmd = sub.add_parser(which, help=f"write the {which} clips")
        cmd.add_argument("--src", type=Path, help="folder with the XML and MPEG")
        cmd.add_argument("--placeholders", action="store_true", help="write placeholders only")
        cmd.add_argument("--out", type=Path, default=EVAL_SET)
        cmd.add_argument("--fetch", action="store_true", help="download missing files")
        cmd.add_argument("--only", nargs="*", help="only these CAVIAR clip names")
    inventory = sub.add_parser("inventory", help="count the activity labels in XML files")
    inventory.add_argument("xml", type=Path, nargs="+")
    args = parser.parse_args(argv)
    if args.command == "inventory":
        print(json.dumps(label_inventory(load_caviar(p) for p in args.xml), indent=2))
        return 0
    if args.src is None and not args.placeholders:
        parser.error("give --src (or --placeholders)")
    src = None if args.placeholders else args.src
    return build(args.command, src, args.out, fetch=args.fetch, only=args.only)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
