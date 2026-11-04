"""Guardrail (rule R13): Chain Mode compares stores, never people.

- No Chain Mode model has a field for a track, a person or a worker, so nothing per person can
  reach the HQ view.
- No Chain Mode source mentions `track_id` at all.
- A store can never bind a pack slot to a zone named after a private space, and a pack can
  never name one, prototype mode or not.

Anyone may add checks here; nobody may weaken them.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import BaseModel

import rulecam.chain.models as chain_models
from rulecam.chain import fit_store, lint_pack
from rulecam.config import get_settings
from tests.chain.helpers import CALIBRATION, ROLES, demo_pack_data, handwash_only, pack_from, store

CHAIN_SRC = Path(__file__).resolve().parents[2] / "src" / "rulecam" / "chain"
PERSON_FIELD = re.compile(
    r"track|person|people|worker|employee|staff|cook|face|identity|name_of", re.IGNORECASE
)
# Fields that name stores or HQ, never people.
ALLOWED = frozenset({"store_name"})


def _models() -> list[type[BaseModel]]:
    return [
        obj
        for _, obj in inspect.getmembers(chain_models, inspect.isclass)
        if issubclass(obj, BaseModel) and obj.__module__ == chain_models.__name__
    ]


@pytest.fixture
def prototype(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("RULECAM_PROTOTYPE_MODE", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.parametrize("model", _models(), ids=lambda m: m.__name__)
def test_no_chain_model_has_a_per_person_field(model: type[BaseModel]) -> None:
    bad = [f for f in model.model_fields if PERSON_FIELD.search(f) and f not in ALLOWED]
    assert bad == []


@pytest.mark.parametrize("path", sorted(CHAIN_SRC.glob("*.py")), ids=lambda p: p.name)
def test_no_chain_source_touches_track_ids(path: Path) -> None:
    assert "track_id" not in path.read_text(encoding="utf-8")


@pytest.mark.usefixtures("prototype")
def test_a_break_room_binding_is_blocked_even_in_prototype_mode() -> None:
    setup = store(
        site={
            "roles": ROLES,
            "cameras": {
                "kitchen-cam": {
                    "calibration": CALIBRATION,
                    "zones": {"staff_break_room": [[0, 0], [400, 0], [400, 300], [0, 300]]},
                }
            },
        },
        bindings={"handwash-before-line": {"line_entry": "staff_break_room"}},
    )
    fitted = fit_store(pack_from(handwash_only()), setup).rules[0]
    assert fitted.status == "blocked"
    assert fitted.rule is not None
    assert "private space" in fitted.says


@pytest.mark.usefixtures("prototype")
def test_a_pack_slot_in_a_bathroom_is_refused_even_in_prototype_mode() -> None:
    data = demo_pack_data()
    template = data["templates"][0]
    template["slots"]["bathroom_sink"] = template["slots"].pop("sink_zone")
    template["rule"]["predicate"]["requirement"]["zone"] = "bathroom_sink"
    assert "private_space_slot" in [f.code for f in lint_pack(pack_from(data))]
