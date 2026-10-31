"""Cross-language fixture for the browser verify page (P3-07b).

`tests/fixtures/evidence/chain.jsonl` and `expected.json` are written by Python here and read by
`src/rulecam/evidence/verify_page/chain.test.js` (vitest). If Node is installed, this test also
runs the JS directly, so the browser code is checked against Python in one pytest run.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from rulecam.evidence.chain import EventChain
from rulecam.evidence.merkle import chain_root
from tests.fakes import FakeClock

FIXTURES = Path(__file__).parent.parent / "fixtures" / "evidence"
PAGE = Path(__file__).parent.parent.parent / "src" / "rulecam" / "evidence" / "verify_page"


def build_fixture(directory: Path) -> tuple[str, dict[str, object]]:
    clock = FakeClock()
    chain = EventChain(directory, "kitchen", clock=clock)
    payloads: list[tuple[str, dict[str, object]]] = [
        ("plan", {"rules": ["wash-before-line"], "lang": "es", "note": "señal «lavar» ✓"}),
        ("candidate", {"i": 1, "score": 0.1, "big": 1e21, "tiny": 1e-7, "whole": 2.0}),
        ("verdict", {"i": 2, "p": 0.87, "nested": {"z": [1, 2.5, None], "a": True}}),
        ("coverage", {"camera_id": "cam1", "watched_fraction": 0.92}),
        ("action", {"kind": "speak", "quote": 'he said "stop"\n', "neg": -0.000001}),
    ]
    for kind, payload in payloads:
        chain.append(kind, payload)  # type: ignore[arg-type]
        clock.advance(1.5)
    path = chain.path_for(clock.now().date())
    root, n = chain_root(path)
    hashes = [json.loads(line)["hash"] for line in path.read_text(encoding="utf-8").splitlines()]
    expected = {"n": n, "root": root, "hashes": hashes, "watched": 0.92}
    return path.read_text(encoding="utf-8"), expected


def test_committed_fixture_matches_python(tmp_path: Path) -> None:
    text, expected = build_fixture(tmp_path)
    assert (FIXTURES / "chain.jsonl").read_text(encoding="utf-8") == text
    assert json.loads((FIXTURES / "expected.json").read_text()) == expected


@pytest.mark.skipif(shutil.which("node") is None, reason="Node not installed")
def test_browser_code_reproduces_python_hashes() -> None:
    script = (
        "const m = await import(process.argv[1]);"
        "const fs = await import('node:fs');"
        "const text = fs.readFileSync(process.argv[2], 'utf8');"
        "const r = await m.verifyChainText(text);"
        "console.log(JSON.stringify({ok: r.ok, n: r.n, hashes: r.hashes, watched: r.watched,"
        " root: await m.merkleRoot(r.hashes)}));"
    )
    node = shutil.which("node")
    assert node is not None
    result = subprocess.run(  # noqa: S603 - fixed argv
        [
            node,
            "--input-type=module",
            "-e",
            script,
            (PAGE / "chain.js").as_uri(),
            str(FIXTURES / "chain.jsonl"),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    out = json.loads(result.stdout)
    expected = json.loads((FIXTURES / "expected.json").read_text())
    assert out == {"ok": True, **expected}
