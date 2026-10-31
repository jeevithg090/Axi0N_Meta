"""`/plans/{id}/questions`, `/frames/{camera}` and `/answers` (WS4): owner-only, mapped errors."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rulecam.api.app import AppServices, create_app
from rulecam.bus import Bus
from rulecam.planner.answers import (
    Answer,
    AnswerError,
    PlanBusyError,
    PlanQuestion,
    PlanQuestions,
)
from rulecam.planner.errors import PipelineError
from rulecam.replay.cache import ReplayCache
from tests.fakes import FakeClock

OWNER: dict[str, object] = {"base_url": "http://localhost", "client": ("127.0.0.1", 1)}
TUNNEL: dict[str, object] = {"base_url": "https://demo.tunnel.test", "client": ("203.0.113.7", 1)}
QUESTION = PlanQuestion(
    step="ask", question_id="q2", text="Opening hours?", target_field="schedule"
)


class FakeQuestions:
    def __init__(self, frame: Path) -> None:
        self.frame_path = frame
        self.answered: list[tuple[str, list[Answer]]] = []
        self.error: Exception | None = None

    def questions(self, plan_id: str) -> PlanQuestions:
        if plan_id != "p1":
            raise PipelineError(f"no saved run input for plan {plan_id!r}")
        return PlanQuestions(plan_id=plan_id, questions=(QUESTION,), running=False, answered=0)

    def frame(self, plan_id: str, camera_id: str) -> Path | None:
        self.questions(plan_id)
        return self.frame_path if camera_id == "cam1" else None

    def answer(self, plan_id: str, answers: Sequence[Answer]) -> None:
        if self.error is not None:
            raise self.error
        self.questions(plan_id)
        self.answered.append((plan_id, list(answers)))


@pytest.fixture
def fake(tmp_path: Path) -> FakeQuestions:
    frame = tmp_path / "cam1.jpg"
    frame.write_bytes(b"\xff\xd8\xff\xe0jpeg")
    return FakeQuestions(frame)


def client(tmp_path: Path, questions: FakeQuestions | None, where: dict[str, object]) -> TestClient:
    bus = Bus()
    services = AppServices(
        bus=bus,
        cache=ReplayCache(tmp_path / "replay", "replay", clock=FakeClock(), bus=bus),
        chain_root=tmp_path / "chain",
        site="site",
        questions=questions,
    )
    return TestClient(create_app(services), **where)  # type: ignore[arg-type]


def test_owner_reads_questions_and_frames(tmp_path: Path, fake: FakeQuestions) -> None:
    with client(tmp_path, fake, OWNER) as owner:
        body = owner.get("/plans/p1/questions").json()
        assert body["questions"][0]["question_id"] == "q2"
        assert body["questions"][0]["step"] == "ask"
        assert owner.get("/plans/p9/questions").status_code == 404
        image = owner.get("/plans/p1/frames/cam1")
        assert image.status_code == 200
        assert image.headers["content-type"] == "image/jpeg"
        assert image.headers["cache-control"] == "no-store"
        assert owner.get("/plans/p1/frames/cam2").status_code == 404
        assert owner.get("/plans/p9/frames/cam1").status_code == 404
        assert owner.get("/plans/p1/frames/..%2Fetc").status_code in {404, 422}


def test_owner_answers_and_errors_map(tmp_path: Path, fake: FakeQuestions) -> None:
    answers = {"answers": [{"question_id": "q2", "text": "6am to 10pm"}]}
    with client(tmp_path, fake, OWNER) as owner:
        reply = owner.post("/plans/p1/answers", json=answers)
        assert reply.status_code == 202
        assert reply.json() == {"plan_id": "p1", "answers": 1}
        assert fake.answered[0][1][0].text == "6am to 10pm"
        assert owner.post("/plans/p9/answers", json=answers).status_code == 404
        assert owner.post("/plans/p1/answers", json={"answers": []}).status_code == 422
        many = {"answers": [{"question_id": f"q{i}", "text": "x"} for i in range(11)]}
        assert owner.post("/plans/p1/answers", json=many).status_code == 422
        extra = {"answers": [{"question_id": "q2", "text": "x", "path": "/etc"}]}
        assert owner.post("/plans/p1/answers", json=extra).status_code == 422
        fake.error = PlanBusyError("still planning")
        assert owner.post("/plans/p1/answers", json=answers).status_code == 409
        fake.error = AnswerError("the answer to q2 is empty")
        bad = owner.post("/plans/p1/answers", json=answers)
        assert bad.status_code == 422
        assert bad.json()["detail"] == "the answer to q2 is empty"


def test_questions_are_owner_only(tmp_path: Path, fake: FakeQuestions) -> None:
    with client(tmp_path, fake, TUNNEL) as remote:
        assert remote.get("/plans/p1/questions").status_code == 403
        assert remote.get("/plans/p1/frames/cam1").status_code == 403
        answers = {"answers": [{"question_id": "q2", "text": "x"}]}
        assert remote.post("/plans/p1/answers", json=answers).status_code == 403
    assert fake.answered == []


def test_without_a_planner_the_routes_answer_503(tmp_path: Path) -> None:
    with client(tmp_path, None, OWNER) as owner:
        assert owner.get("/plans/p1/questions").status_code == 503
        assert owner.get("/plans/p1/frames/cam1").status_code == 503
        answers = {"answers": [{"question_id": "q2", "text": "x"}]}
        assert owner.post("/plans/p1/answers", json=answers).status_code == 503
