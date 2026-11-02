"""Add-on 1.5: CAVIAR XML -> MOT, scoring a stored camera's tracks, MOTChallenge folders."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from rulecam.cli import app
from rulecam.contracts import Track, TrackPoint
from rulecam.eval.caviar import (
    CaviarBox,
    chain_mot,
    label_inventory,
    load_caviar,
    mot_rows,
    parse_caviar,
)
from rulecam.eval.errors import EvalError
from rulecam.eval.motchallenge import SeqInfo, restrict_gt, tracker_rows, write_layout
from rulecam.eval.record import PredictedBox
from rulecam.eval.tracking import parse_mot
from rulecam.eval.trackstore import (
    load_identities,
    relabel,
    render_markdown,
    score_store,
    stored_boxes,
)
from rulecam.rules.store import ParquetTrackStore

cli = CliRunner()
START = datetime(2026, 11, 2, 9, 44, 11, tzinfo=UTC)


def obj(oid: str, xc: int, yc: int, *, situation: str = "moving", context: str = "walking") -> str:
    return (
        f'<object id="{oid}"><orientation>90</orientation>'
        f'<box h="40" w="20" xc="{xc}" yc="{yc}"/><appearance>visible</appearance>'
        '<hypothesislist><hypothesis evaluation="1.0" id="1" prev="1.0">'
        '<movement evaluation="1.0">walking</movement><role evaluation="1.0">walker</role>'
        f'<context evaluation="1.0">{context}</context>'
        f'<situation evaluation="1.0">{situation}</situation>'
        "</hypothesis></hypothesislist></object>"
    )


def caviar_xml(name: str, frames: list[list[str]], groups: dict[int, str] | None = None) -> str:
    body = []
    for n, objects in enumerate(frames):
        group = (groups or {}).get(n, "")
        body.append(
            f'<frame number="{n}"><objectlist>{"".join(objects)}</objectlist>'
            f"<grouplist>{group}</grouplist></frame>"
        )
    return f'<?xml version="1.0"?><dataset name="{name}">{"".join(body)}</dataset>'


GROUP = (
    '<group id="0"><box h="50" w="60" xc="50" yc="50"/><members>0,1</members>'
    '<hypothesislist><hypothesis evaluation="1.0" id="1" prev="1.0">'
    '<movement evaluation="1.0">active</movement><role evaluation="1.0">meeters</role>'
    '<context evaluation="1.0">meeting</context>'
    '<situation evaluation="1.0">interacting</situation>'
    "</hypothesis></hypothesislist></group>"
)
CLIP_A = caviar_xml(
    "A",
    [[obj("0", 30, 40), obj("1", 100, 40)], [obj("0", 32, 40), obj("1", 100, 40)]],
    {1: GROUP},
)
CLIP_B = caviar_xml("B", [[obj("5", 60, 60, situation="shop enter")]] * 3)


def test_parse_reads_boxes_labels_and_groups() -> None:
    seq = parse_caviar(CLIP_A)
    assert seq.name == "A"
    assert seq.frames == 2
    assert seq.duration_s == pytest.approx(0.08)
    assert seq.object_ids == ("0", "1")
    first = seq.objects[0]
    assert first.xywh == (20.0, 20.0, 20.0, 40.0)
    assert first.labels == {
        "movement": "walking",
        "role": "walker",
        "context": "walking",
        "situation": "moving",
    }
    assert seq.groups[0].members == ("0", "1")
    assert seq.groups[0].labels["context"] == "meeting"


def test_mot_rows_are_one_based_and_chained_streams_renumber() -> None:
    rows, used = mot_rows(parse_caviar(CLIP_A))
    assert used == 2
    assert rows[0] == "1,1,20.00,20.00,20.00,40.00,1,1,1"
    chained, segments = chain_mot([parse_caviar(CLIP_A), parse_caviar(CLIP_B)])
    assert [s.name for s in segments] == ["A", "B"]
    assert segments[1].first_frame == 3
    assert segments[1].start_s == pytest.approx(0.08)
    assert segments[1].end_s == pytest.approx(0.2)
    assert chained[-1].startswith("5,3,")  # B's frame 2 -> stream frame 5, id after A's two
    parsed = parse_mot(chained, {1: "person"})
    assert sorted(parsed["person"]) == [1, 2, 3, 4, 5]


def test_inventory_counts_object_and_group_labels() -> None:
    counts = label_inventory([parse_caviar(CLIP_A), parse_caviar(CLIP_B)])
    assert counts["situation"] == {"moving": 4, "shop enter": 3, "interacting": 1}


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("<dataset", "malformed"),
        ('<dataset name="x"/>', "no <frame>"),
        ('<dataset><frame number="a"/></dataset>', "not a number"),
        ("<dataset><frame/></dataset>", "no 'number'"),
        (
            '<dataset><frame number="0"><objectlist><object id="1"/></objectlist></frame>'
            "</dataset>",
            "no <box>",
        ),
    ],
)
def test_parse_rejects_bad_xml(text: str, error: str) -> None:
    with pytest.raises(EvalError, match=error):
        parse_caviar(text)


def test_load_names_the_clip_after_the_file(tmp_path: Path) -> None:
    path = tmp_path / "TwoEnterShop1cor.xml"
    path.write_text(CLIP_B, encoding="utf-8")
    assert load_caviar(path).name == "TwoEnterShop1cor"
    with pytest.raises(EvalError, match="cannot read"):
        load_caviar(tmp_path / "missing.xml")


def test_a_box_without_labels_has_none() -> None:
    box = CaviarBox(frame=0, object_id="1", xc=10, yc=10, w=4, h=4)
    assert box.labels == {}
    text = (
        '<dataset><frame number="0"><objectlist><object id="1">'
        '<box h="4" w="4" xc="10" yc="10"/></object></objectlist></frame></dataset>'
    )
    assert parse_caviar(text).objects[0].labels == {}


# --- scoring a track store ------------------------------------------------------------------


def stored_track(track_id: str, boxes: list[tuple[float, tuple[float, ...]]]) -> Track:
    points = tuple(
        TrackPoint(
            ts=START + timedelta(seconds=s),
            frame_idx=i,
            box_xyxy=(b[0], b[1], b[2], b[3]),
            foot_px=((b[0] + b[2]) / 2, b[3]),
            confidence=0.9,
        )
        for i, (s, b) in enumerate(boxes)
    )
    return Track(track_id=track_id, camera_id="phone-68", concept="person", points=points)


def doubled(x: float, y: float) -> tuple[float, ...]:
    """CLIP_A's 20x40 box centred at (x, y), on a 2x upscaled frame."""
    return (2 * (x - 10), 2 * (y - 20), 2 * (x + 10), 2 * (y + 20))


@pytest.fixture
def store(tmp_path: Path) -> Path:
    """Person 0 is split into two raw tracks that one identity joins; person 1 is one track."""
    root = tmp_path / "tracks"
    with ParquetTrackStore(root) as s:
        s.append(
            [
                stored_track("t1", [(0.0, doubled(30, 40))]),
                stored_track("t2", [(0.04, doubled(32, 40))]),
                stored_track("t3", [(0.0, doubled(100, 40)), (0.04, doubled(100, 40))]),
            ]
        )
    return root


def test_store_scores_raw_and_reid_apart(store: Path) -> None:
    gt = parse_mot(mot_rows(parse_caviar(CLIP_A))[0], {1: "person"})
    with ParquetTrackStore(store) as tracks:
        assert len(stored_boxes(tracks, "phone-68", START, START + timedelta(seconds=1))) == 4
        report = score_store(
            tracks,
            gt,
            camera_id="phone-68",
            start=START,
            end=START + timedelta(seconds=1),
            fps=25.0,
            scale=(0.5, 0.5),
            identities={"t1": "id-a", "t2": "id-a"},
        )
    raw, reid = report.scores
    assert (raw.ids, reid.ids) == ("raw", "reid")
    assert raw.predicted_ids == 3
    assert reid.predicted_ids == 2
    assert raw.id_switches == 1
    assert reid.id_switches == 0
    assert reid.idf1 == pytest.approx(1.0)
    assert reid.hota == pytest.approx(1.0)
    assert raw.hota < reid.hota
    assert report.identities == 1
    assert report.unlinked_tracks == 1
    assert "| reid | person | 2 |" in render_markdown(report)
    assert json.loads(report.to_json())["tracks"] == 3


def test_store_without_points_is_an_error(store: Path) -> None:
    with ParquetTrackStore(store) as tracks, pytest.raises(EvalError, match="no track points"):
        score_store(
            tracks,
            {},
            camera_id="other",
            start=START,
            end=START + timedelta(seconds=1),
            fps=25.0,
        )


def test_identities_file(tmp_path: Path) -> None:
    path = tmp_path / "track_identity.jsonl"
    path.write_text('{"track_id": "t1", "identity_id": "i1"}\n\n', encoding="utf-8")
    assert load_identities(path) == {"t1": "i1"}
    path.write_text('{"track_id": "t1"}\n', encoding="utf-8")
    with pytest.raises(EvalError, match="track_id/identity_id"):
        load_identities(path)
    with pytest.raises(EvalError, match="cannot read"):
        load_identities(tmp_path / "missing.jsonl")


def test_relabel_counts_unlinked_tracks() -> None:
    boxes = [
        PredictedBox(clip_s=0, track="a", concept="person", box_xyxy=(0, 0, 1, 1)),
        PredictedBox(clip_s=1, track="b", concept="person", box_xyxy=(0, 0, 1, 1)),
    ]
    out, unlinked = relabel(boxes, {"a": "x"})
    assert [b.track for b in out] == ["identity:x", "track:b"]
    assert unlinked == 1


# --- CLI ------------------------------------------------------------------------------------


def test_cli_converts_and_scores(store: Path, tmp_path: Path) -> None:
    xml = tmp_path / "A.xml"
    xml.write_text(CLIP_A, encoding="utf-8")
    gt = tmp_path / "gt.txt"
    result = cli.invoke(app, ["eval", "caviar-mot", str(xml), "--out", str(gt)])
    assert result.exit_code == 0, result.output
    assert "A: frames 1-2, 0.0-0.1 s" in result.output
    identities = tmp_path / "ids.jsonl"
    identities.write_text(
        '{"track_id": "t1", "identity_id": "a"}\n{"track_id": "t2", "identity_id": "a"}\n',
        encoding="utf-8",
    )
    out = tmp_path / "report.json"
    common = ["eval", "track", "--store", str(store), "--camera", "phone-68"]
    common += ["--start", "2026-11-02T09:44:11Z", "--scale", "0.5"]
    result = cli.invoke(
        app, [*common, "--gt", str(gt), "--identities", str(identities), "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert "| raw | person | 3 |" in result.output
    assert json.loads(out.read_text(encoding="utf-8"))["identities"] == 1
    result = cli.invoke(app, [*common, "--caviar", str(xml), "--end", "2026-11-02T09:44:12"])
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize(
    "extra",
    [
        [],  # neither --gt nor --caviar
        ["--gt", "x.txt", "--caviar", "y.xml"],
        ["--caviar", "missing.xml"],
    ],
)
def test_cli_track_usage_errors(store: Path, extra: list[str]) -> None:
    args = ["eval", "track", "--store", str(store), "--camera", "phone-68", "--start", "2026-11-02"]
    assert cli.invoke(app, [*args, *extra]).exit_code == 2


def test_cli_track_rejects_a_bad_time(store: Path, tmp_path: Path) -> None:
    gt = tmp_path / "gt.txt"
    gt.write_text("1,1,0,0,1,1,1,1,1\n", encoding="utf-8")
    args = ["eval", "track", "--store", str(store), "--camera", "phone-68", "--gt", str(gt)]
    assert cli.invoke(app, [*args, "--start", "yesterday"]).exit_code == 2


def test_cli_caviar_mot_rejects_bad_xml(tmp_path: Path) -> None:
    bad = tmp_path / "bad.xml"
    bad.write_text("<dataset", encoding="utf-8")
    assert cli.invoke(app, ["eval", "caviar-mot", str(bad)]).exit_code == 2


# --- MOTChallenge folders -------------------------------------------------------------------


def test_tracker_rows_dedupe_and_restrict(tmp_path: Path) -> None:
    boxes = [
        PredictedBox(clip_s=0.0, track="a", concept="person", box_xyxy=(0, 0, 10, 20)),
        PredictedBox(clip_s=0.01, track="a", concept="person", box_xyxy=(0, 0, 10, 20)),
        PredictedBox(clip_s=0.04, track="b", concept="person", box_xyxy=(2, 2, 4, 4)),
        PredictedBox(clip_s=0.08, track="c", concept="bag", box_xyxy=(2, 2, 4, 4)),
    ]
    rows, frames = tracker_rows(boxes, fps=25.0, scale=(0.5, 0.5), concept="person")
    assert rows == ["1,1,0.00,0.00,5.00,10.00,1,-1,-1,-1", "2,2,1.00,1.00,1.00,1.00,1,-1,-1,-1"]
    assert frames == {1, 2, 3}
    assert restrict_gt(["1,1,0,0,1,1,1,1,1", "4,1,0,0,1,1,1,1,1", ""], frames) == [
        "1,1,0,0,1,1,1,1,1"
    ]
    info = SeqInfo("S", 25.0, 3, 384, 288)
    out = write_layout(tmp_path, info, ["1,1,0,0,1,1,1,1,1"], rows)
    assert out == tmp_path / "trackers" / "rulecam" / "data" / "S.txt"
    assert "seqLength=3" in (tmp_path / "gt" / "S" / "seqinfo.ini").read_text(encoding="utf-8")
    assert (tmp_path / "gt" / "S" / "gt" / "gt.txt").read_text(encoding="utf-8").count("\n") == 1
