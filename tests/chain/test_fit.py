"""Fitting a pack to stores: slots, knobs, severity, roles, privacy, lint and Rule CI."""

from __future__ import annotations

from pathlib import Path

import pytest

from rulecam.chain import (
    FittedRule,
    RulePack,
    StoreFit,
    StoreSetupError,
    fit_chain,
    fit_store,
    load_pack,
    load_store,
    load_stores,
)
from rulecam.chain.fit import parse_store
from rulecam.contracts.core import Severity
from tests.chain.helpers import CALIBRATION, DEMO, LINE, ROLES, handwash_only, pack_from, store


@pytest.fixture(scope="module")
def demo() -> dict[str, StoreFit]:
    fits = fit_chain(load_pack(DEMO / "pack.yaml"), load_stores(DEMO / "stores"))
    return {fit.store_id: fit for fit in fits}


@pytest.fixture(scope="module")
def handwash() -> RulePack:
    return pack_from(handwash_only())


def _only(fit: StoreFit) -> FittedRule:
    assert len(fit.rules) == 1
    return fit.rules[0]


# --- the demo chain --------------------------------------------------------------------------


def test_a_store_set_up_like_the_pack_runs_every_rule(demo: dict[str, StoreFit]) -> None:
    fit = demo["store-01"]
    assert fit.ready_count == 3
    wash = fit.rule("handwash-before-line")
    assert wash is not None
    assert wash.knobs == {"wash_seconds": 25.0, "wash_window": 300.0}  # stricter: allowed
    assert wash.cameras == ("kitchen-cam",)
    assert wash.rule is not None
    assert wash.rule.calibration == "ground-plane@kitchen-cam"
    assert wash.rule.source.plan_id == "pack:kitchen-basics@v3"
    assert wash.rule.version == 3
    assert wash.ci_summary.startswith("boundary ")


def test_looser_knobs_and_lower_severity_are_refused(demo: dict[str, StoreFit]) -> None:
    fit = demo["store-07"]
    wash = fit.rule("handwash-before-line")
    exit_rule = fit.rule("fire-exit-clear")
    assert wash is not None
    assert exit_rule is not None
    assert wash.status == "ready"
    assert wash.knobs["wash_seconds"] == 20.0
    assert "wash_seconds=10s refused" in wash.warnings[0]
    assert exit_rule.severity is Severity.HIGH
    assert "only raising is allowed" in exit_rule.warnings[0]


def test_explicit_bindings_map_slots_to_the_stores_own_names(demo: dict[str, StoreFit]) -> None:
    rule = demo["store-07"].rule("delivery-to-cold-storage")
    assert rule is not None
    assert rule.rule is not None
    assert rule.cameras == ("dock-cam",)
    assert rule.rule.zones["receiving"].polygon_px == (
        (100.0, 450.0),
        (800.0, 450.0),
        (800.0, 1000.0),
        (100.0, 1000.0),
    )


def test_an_alert_with_no_channel_needs_setup(demo: dict[str, StoreFit]) -> None:
    rule = demo["store-14"].rule("delivery-to-cold-storage")
    assert rule is not None
    assert rule.status == "needs_setup"
    assert "role 'kitchen_lead'" in rule.says


def test_a_buffer_needs_a_calibrated_camera(demo: dict[str, StoreFit]) -> None:
    rule = demo["store-22"].rule("fire-exit-clear")
    assert rule is not None
    assert rule.status == "needs_setup"
    assert "needs a ground calibration" in rule.problems[0]


def test_a_break_room_zone_is_blocked(demo: dict[str, StoreFit]) -> None:
    rule = demo["store-22"].rule("delivery-to-cold-storage")
    assert rule is not None
    assert rule.status == "blocked"
    assert "private space" in rule.says


def test_every_fit_names_the_exact_pack(demo: dict[str, StoreFit]) -> None:
    digest = load_pack(DEMO / "pack.yaml").digest()
    assert {fit.pack_digest for fit in demo.values()} == {digest}


# --- one rule at a time --------------------------------------------------------------------


def test_slots_bind_themselves_when_the_answer_is_clear(handwash: RulePack) -> None:
    fitted = _only(fit_store(handwash, store()))
    assert fitted.status == "ready", fitted.problems
    assert fitted.rule is not None
    assert fitted.rule.zones["sink_zone"].camera_id == "kitchen-cam"


def test_an_unclear_slot_asks_to_be_mapped(handwash: RulePack) -> None:
    setup = store(
        site={
            "roles": ROLES,
            "cameras": {"a": {"calibration": CALIBRATION}, "b": {"calibration": CALIBRATION}},
        },
        frames={},
    )
    fitted = _only(fit_store(handwash, setup))
    assert fitted.status == "needs_setup"
    assert any("map slot 'line_entry'" in p for p in fitted.problems)
    assert any("map slot 'sink_zone'" in p for p in fitted.problems)


def test_unknown_zones_and_cameras_need_setup(handwash: RulePack) -> None:
    setup = store(bindings={"handwash-before-line": {"line_entry": "nope", "sink_zone": "cam9"}})
    fitted = _only(fit_store(handwash, setup))
    assert fitted.status == "needs_setup"
    assert "unknown 'nope'" in fitted.problems[0]
    assert "camera 'cam9'" in fitted.problems[1]


def test_a_zone_outside_the_frame_needs_setup(handwash: RulePack) -> None:
    fitted = _only(fit_store(handwash, store(frames={"kitchen-cam": [640, 360]})))
    assert fitted.status == "needs_setup"
    assert "[zone_out_of_view]" in fitted.problems[0]


def test_a_store_below_the_privacy_floor_runs_nothing() -> None:
    pack = pack_from(handwash_only(privacy_floor="anonymous"))
    fitted = _only(fit_store(pack, store()))
    assert fitted.status == "blocked"
    assert "needs anonymous or stricter" in fitted.says
    ok = store(
        site={
            "privacy_mode": "strict",
            "roles": ROLES,
            "cameras": {"kitchen-cam": {"calibration": CALIBRATION, "zones": {"line_entry": LINE}}},
        }
    )
    assert _only(fit_store(pack, ok)).status == "ready"


def test_raising_severity_is_allowed(handwash: RulePack) -> None:
    fitted = _only(fit_store(handwash, store(severity={"handwash-before-line": "high"})))
    assert fitted.severity is Severity.HIGH
    assert fitted.rule is not None
    assert fitted.rule.severity is Severity.HIGH
    assert fitted.warnings == ()


def test_unknown_knobs_and_rules_are_reported(handwash: RulePack) -> None:
    setup = store(knobs={"handwash-before-line": {"soap": 3}, "no-such-rule": {"x": 1}})
    fit = fit_store(handwash, setup)
    assert fit.rules[0].warnings == ("unknown knob 'soap' ignored",)
    assert fit.warnings == ("settings for 'no-such-rule' ignored: the pack has no such rule",)


def test_a_red_rule_ci_fails_the_fit() -> None:
    data = handwash_only()
    data["templates"][0]["knobs"] = []
    data["templates"][0]["rule"]["predicate"]["requirement"]["total_s_gte"] = 0
    fitted = _only(fit_store(pack_from(data), store()))
    assert fitted.status == "failed"


def test_the_fit_is_deterministic(handwash: RulePack) -> None:
    assert fit_store(handwash, store()) == fit_store(handwash, store())


def test_store_ids_must_be_unique(handwash: RulePack) -> None:
    with pytest.raises(StoreSetupError, match="unique"):
        fit_chain(handwash, [store("a"), store("a")])


# --- loading -------------------------------------------------------------------------------


def test_store_files_report_their_problems(tmp_path: Path) -> None:
    with pytest.raises(StoreSetupError, match="not valid YAML"):
        parse_store("a: [")
    with pytest.raises(StoreSetupError, match="mapping"):
        parse_store("- 1")
    with pytest.raises(StoreSetupError, match="invalid"):
        parse_store("store_id: 'bad id'\nname: x")
    with pytest.raises(StoreSetupError, match="cannot read"):
        load_store(tmp_path / "missing.yaml")
    with pytest.raises(StoreSetupError, match="no store setups"):
        load_stores(tmp_path)
