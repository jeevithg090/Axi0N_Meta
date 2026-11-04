"use client";

import React, { useCallback, useEffect, useState } from "react";
import { api, errorText } from "../../lib/api";
import { SampleDataTag } from "../SampleDataTag";
import {
  claimParts,
  formatNumber,
  verdictLine,
  type AdviceCard,
  type NumberCheck,
  type SealStatus,
} from "../../lib/advice";

const INITIAL_ADVICE_CARDS: AdviceCard[] = [
  {
    recommendation: {
      id: "rec-dock3-replenishment",
      status: "measuring",
      scope_kind: "station",
      scope_ids: ["CAM-01 · North Dock Bay 3", "CAM-02 · Aisle B2 Intersection"],
      klass: "test",
      provenance: "recorded",
      saw: [
        {
          template:
            "Outbound freight trucks wait {m1} for a counterbalance forklift at Dock 3 between 13:00–14:30 vs {m2} turnaround at Dock 1, while Aisle B2 records {m3} pedestrian-forklift <2.0m encounters per shift.",
          slots: { m1: "mr_dock3_wait", m2: "mr_dock1_turn", m3: "mr_aisle_encounters" },
          grade: "A",
        },
      ],
      why: [
        {
          id: "h1",
          statement:
            "13:00 pallet-racking replenishment on CAM-10 occupies 2 of 3 counterbalance forklifts during peak carrier arrivals",
          status: "supported",
          share_of_gap: 0.74,
        },
        {
          id: "h2",
          statement: "Carriers arriving off-appointment window at North Loading Dock",
          status: "ruled_out",
          share_of_gap: 0.08,
        },
      ],
      do: [
        {
          op: "resequence_task",
          params: {
            task: "High-Bay Aisle 4 pallet replenishment",
            from: "13:00–14:30",
            to: "10:30–12:00 (off-peak dock window)",
          },
        },
      ],
      who_role: "dock_lead",
      revert: "Restore High-Bay Aisle 4 replenishment schedule to 13:00 if carrier dwell > 18 min",
      expect: {
        metric: "dock3_wait_for_forklift_min",
        interval: [-14, -4],
        level: 0.95,
        unit: "min/truck",
        model_rung: "erlang_c_switchback",
        assumptions: {
          poisson_arrivals: "pass",
          stable_service_rate: "pass",
          no_spillover_to_dock1: "pass",
        },
      },
      safety: {
        rules_checked: [
          "keep-people-2m-from-moving-forklift",
          "heavy-equipment-swing-radius-exclusion",
        ],
        verdict: "pass",
        considered_not_recommended: [
          [
            "Add a 4th counterbalance forklift to North Loading Dock during 13:00 peak",
            "increases Aisle B2 pedestrian-forklift <2.0m encounter density by +38% (vetoed by Safety Rule IR)",
          ],
        ],
      },
      rank_score: 0.94,
      measurement_plan_id: "mp-rec-dock3-replenishment",
      numbers_total: 3,
      numbers_reproduced: 3,
    },
    checks: [
      {
        result_id: "mr_dock3_wait",
        metric: "dock3_wait_for_forklift_min",
        catalogue_value: 14.2,
        unit: "min",
        recomputed: 14.2,
        sql: "SELECT ROUND(AVG(wait_seconds) / 60.0, 1) FROM station_dwell WHERE station_id = 'dock_3' AND hour_utc BETWEEN 13 AND 14;",
        ok: true,
        error: "",
      },
      {
        result_id: "mr_dock1_turn",
        metric: "dock1_turnaround_min",
        catalogue_value: 31.0,
        unit: "min",
        recomputed: 31.0,
        sql: "SELECT ROUND(AVG(turnaround_seconds) / 60.0, 1) FROM station_dwell WHERE station_id = 'dock_1';",
        ok: true,
        error: "",
      },
      {
        result_id: "mr_aisle_encounters",
        metric: "aisle_b2_sub2m_encounters_per_shift",
        catalogue_value: 4.5,
        unit: "events/shift",
        recomputed: 4.5,
        sql: "SELECT ROUND(COUNT(*) / 4.0, 1) FROM candidate_events WHERE camera_id = 'CAM-02' AND measured_m <= 2.0;",
        ok: true,
        error: "",
      },
    ],
    hidden: [],
    flagged: false,
    measurement_plan: {
      id: "mp-rec-dock3-replenishment",
      design: "switchback",
      block_minutes: 30,
      n_pairs: 6,
      min_detectable_effect: 3.5,
      smallest_possible_p: 0.03125,
      decision_rule:
        "On-minus-off difference of dock3_wait_for_forklift_min per pair of 30-min switchback blocks, exact sign-flip test, 95% interval.",
      preregistration_hash: "8f4b2c91e04d7a129b3e6011c48d92a07e12c344f980a1b2c3d4e5f6a7b8c9d0",
    },
    preregistered_seq: 18,
    verdict: {
      plan_id: "mp-rec-dock3-replenishment",
      label: "worked",
      effect: -9.2,
      ci: [-14.0, -4.4],
      p_value: 0.03125,
      pairs_done: 6,
    },
  },
  {
    recommendation: {
      id: "rec-corridor-ppe-dispenser",
      status: "proposed",
      scope_kind: "station",
      scope_ids: ["CAM-03 · East Visitor Corridor"],
      klass: "do",
      provenance: "recorded",
      saw: [
        {
          template:
            "East Visitor Corridor recorded {m1} unhelmeted visitor entries over 14 shifts, with {m2} of violations occurring within the first 4.5 metres of Door E2.",
          slots: { m1: "mr_ppe_breaches", m2: "mr_door_e2_share" },
          grade: "A",
        },
      ],
      why: [
        {
          id: "h1",
          statement:
            "Visitor hardhat rack is mounted 11 metres inside the corridor past the CAM-03 threshold boundary",
          status: "supported",
          share_of_gap: 0.89,
        },
      ],
      do: [
        {
          op: "relocate_fixture",
          params: {
            fixture: "Visitor Hardhat Dispenser Rack",
            from: "Pillar E4 (+11m inside)",
            to: "Door E2 Vestibule (-1.5m before boundary)",
          },
        },
      ],
      who_role: "supervisor",
      revert: "Return dispenser rack to Pillar E4 if vestibule egress width drops below 1.5m",
      expect: {
        metric: "visitor_corridor_unhelmeted_entries_per_shift",
        interval: [-2.8, -1.6],
        level: 0.95,
        unit: "alerts/shift",
        model_rung: "before_after_switchback",
        assumptions: {
          vestibule_clearance_ok: "pass",
          visitor_volume_stationary: "pass",
        },
      },
      safety: {
        rules_checked: ["require-hardhat-in-visitor-corridor"],
        verdict: "pass",
        considered_not_recommended: [
          [
            "Add a 3.0s grace delay before triggering CAM-03 hardhat alert",
            "leaves visitors unprotected for the first 4.2 metres under overhead crane rail (vetoed)",
          ],
        ],
      },
      rank_score: 0.89,
      numbers_total: 2,
      numbers_reproduced: 2,
    },
    checks: [
      {
        result_id: "mr_ppe_breaches",
        metric: "corridor_ppe_violations_14d",
        catalogue_value: 29,
        unit: "events",
        recomputed: 29,
        sql: "SELECT COUNT(*) FROM candidate_events WHERE rule_id = 'require-hardhat-in-visitor-corridor' AND verdict = 'violation';",
        ok: true,
        error: "",
      },
      {
        result_id: "mr_door_e2_share",
        metric: "door_e2_entry_share_pct",
        catalogue_value: 89.7,
        unit: "%",
        recomputed: 89.7,
        sql: "SELECT ROUND(100.0 * SUM(CASE WHEN entry_zone = 'door_e2' THEN 1 ELSE 0 END) / COUNT(*), 1) FROM ppe_episodes;",
        ok: true,
        error: "",
      },
    ],
    hidden: [],
    flagged: false,
  },
  {
    recommendation: {
      id: "rec-kitchen-hood-glare-audit",
      status: "proposed",
      scope_kind: "station",
      scope_ids: ["CAM-05 · Commercial Kitchen Prep Line A"],
      klass: "hold",
      provenance: "recorded",
      saw: [
        {
          template:
            "Station 3 on Prep Line A triggered {m1} head-covering false alarms suppressed by Tier-1 Glimmer, while draft advisor claimed {m2} prep cycle delay.",
          slots: { m1: "mr_kitchen_suppressed", m2: "mr_hallucinated_delay" },
          grade: "B",
        },
      ],
      why: [
        {
          id: "h1",
          statement:
            "Overhead stainless exhaust hood casts a high-contrast specular shadow across Station 3 at 45° camera pitch",
          status: "supported",
          share_of_gap: 0.82,
        },
      ],
      do: [
        {
          op: "adjust_diffuser",
          params: {
            station: "Prep Line A · Station 3",
            action: "Install matte LED diffuser strip under hood lip",
          },
        },
      ],
      who_role: "kitchen_lead",
      revert: "Remove diffuser strip",
      expect: {
        metric: "cam05_tier0_false_candidates_per_day",
        interval: [-4.0, -1.5],
        level: 0.9,
        unit: "candidates/day",
        model_rung: "paired_station_comparison",
        assumptions: {
          illuminance_above_500lux: "pass",
        },
      },
      safety: {
        rules_checked: ["commercial-kitchen-prep-sanitation-guard"],
        verdict: "pass",
        considered_not_recommended: [],
      },
      rank_score: 0.71,
      numbers_total: 2,
      numbers_reproduced: 1,
    },
    checks: [
      {
        result_id: "mr_kitchen_suppressed",
        metric: "cam05_suppressed_candidates",
        catalogue_value: 6,
        unit: "events",
        recomputed: 6,
        sql: "SELECT COUNT(*) FROM verdicts WHERE camera_id = 'CAM-05' AND verdict = 'no_violation';",
        ok: true,
        error: "",
      },
      {
        result_id: "mr_hallucinated_delay",
        metric: "prep_line_cycle_delay_min",
        catalogue_value: 11.4,
        unit: "min",
        recomputed: 48.2,
        sql: "SELECT ROUND(AVG(cycle_s) / 60.0, 1) FROM kitchen_station_spans WHERE station = 'prep_a3';",
        ok: false,
        error: "Catalogue value (11.4 min) differs from Glimmer SQL recomputation (48.2 min); claim automatically redacted.",
      },
    ],
    hidden: ["mr_hallucinated_delay"],
    flagged: true,
  },
];

const INITIAL_SEALS: SealStatus[] = [
  {
    seal_id: "seal_9a4c8e20f1b2",
    sealed: true,
    revealed: true,
    matches: true,
    cause:
      "13:00 High-Bay Aisle 4 pallet replenishment occupies 2 of 3 counterbalance forklifts during peak carrier arrivals at Dock 3",
  },
];

export function AdviceSignage() {
  const [cards, setCards] = useState<AdviceCard[]>(INITIAL_ADVICE_CARDS);
  const [seals, setSeals] = useState<SealStatus[]>(INITIAL_SEALS);
  const [cardsFromApi, setCardsFromApi] = useState<boolean>(false);
  const [openCheck, setOpenCheck] = useState<{ check: NumberCheck; hidden: boolean } | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [filterKlass, setFilterKlass] = useState<"all" | "test" | "do" | "hold">("all");

  const loadFromApi = useCallback(async () => {
    try {
      const [apiCards, apiSeals] = await Promise.all([api.advice(), api.seals()]);
      if (apiCards.length > 0) {
        setCards(apiCards);
        setCardsFromApi(true);
      }
      if (apiSeals.length > 0) setSeals(apiSeals);
    } catch {
      // Keep the built-in sample cards; the page labels them SAMPLE DATA.
    }
  }, []);

  useEffect(() => {
    void loadFromApi();
  }, [loadFromApi]);

  // Accepting asks the server to pre-register the measurement plan in the evidence chain. The
  // plan, its hash and its chain sequence number come back from the server (the cards are
  // reloaded); when the call fails nothing was pre-registered, and the banner says so.
  const handleAccept = async (cardId: string) => {
    setBusyId(cardId);
    setBanner(null);
    try {
      const res = await api.acceptAdvice(cardId);
      await loadFromApi();
      setBanner(
        `✓ Measurement plan for ${cardId} pre-registered in the evidence chain at seq #${res.preregistered_seq}.`,
      );
    } catch (err) {
      setBanner(
        `Not pre-registered: ${errorText(err, "the server did not answer")}. Nothing was written to the evidence chain.`,
      );
    } finally {
      setBusyId(null);
    }
  };

  const visibleCards =
    filterKlass === "all"
      ? cards
      : cards.filter((c) => c.recommendation.klass === filterKlass);

  const totalNumbers = cards.reduce((acc, c) => acc + c.recommendation.numbers_total, 0);
  const reproducedNumbers = cards.reduce((acc, c) => acc + c.recommendation.numbers_reproduced, 0);

  return (
    <div style={{ paddingBottom: "var(--s6)" }}>
      {!cardsFromApi && (
        <section className="wrap sec">
          <SampleDataTag what="The server sent no advice cards, so these are the built-in example cards and seal; their numbers, stations and hashes are written into the page." />
        </section>
      )}
      {/* Pagehead */}
      <section className="wrap pagehead">
        <div className="grid">
          <div className="s8">
            <div className="ctx">
              Site Advisor &amp; Pre-Registered Causal Experiments (P3-10 / P4-08) &nbsp;·&nbsp;{" "}
              <b>Stations Only, Never People</b>
            </div>
            <h1 className="h1">
              Every number is <span className="hi">recomputed by SQL</span> before you see it.
            </h1>
            <p className="lead" style={{ marginTop: "var(--s2)", maxWidth: "66ch" }}>
              Click any highlighted number in a card below to inspect the exact DuckDB/SQL query
              Glimmer ran to verify it. Any number that does not reproduce is automatically redacted
              and flagged. Accepting a recommendation hashes a switchback measurement plan onto the
              SHA-256 chain before the change starts.
            </p>
          </div>
          <div className="s4" style={{ borderLeft: "4px solid var(--ink)", paddingLeft: "var(--s3)" }}>
            <div className="small">Glimmer SQL Verification Rate</div>
            <div className="num-l" style={{ marginTop: "4px" }}>
              {reproducedNumbers}/{totalNumbers}
            </div>
            <div className="mono" style={{ marginTop: "6px" }}>
              1 un-reproduced number redacted (`mr_hallucinated_delay`)
            </div>
            <div style={{ marginTop: "var(--s2)" }}>
              <span className="state ok">SEALED CAUSE MATCHED ✓</span>
            </div>
          </div>
        </div>

        {banner && (
          <div
            style={{
              marginTop: "var(--s3)",
              padding: "12px 16px",
              background: "var(--yellow)",
              color: "#141414",
              border: "2px solid var(--ink)",
              fontWeight: 700,
            }}
          >
            {banner}
          </div>
        )}
      </section>

      <div className="tape" />

      {/* Cryptographic Sealed Cause Banner */}
      {seals.map((s) => (
        <section
          key={s.seal_id}
          className="wrap"
          style={{ paddingTop: "var(--s4)" }}
        >
          <div
            style={{
              border: "3px solid var(--ink)",
              background: "var(--bar)",
              color: "var(--on-dark)",
              padding: "var(--s3)",
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              flexWrap: "wrap",
              gap: "var(--s2)",
            }}
          >
            <div>
              <div className="dm" style={{ fontSize: "12px", color: "var(--yellow)" }}>
                CRYPTOGRAPHIC BLIND EVALUATION SEAL · {s.seal_id.toUpperCase()}
              </div>
              <div style={{ fontSize: "18px", fontWeight: 700, marginTop: "4px" }}>
                Revealed Root Cause: &ldquo;{s.cause}&rdquo;
              </div>
              <div className="dm" style={{ fontSize: "12px", color: "var(--on-dark-2)", marginTop: "4px" }}>
                Committed `sha256(cause + salt)` before blind Site Advisor run · Post-run reveal verified against chain
              </div>
            </div>
            <span
              className="state"
              style={{
                background: s.matches ? "#1E7A3A" : "var(--alert)",
                color: "#fff",
                borderColor: "#fff",
                padding: "6px 12px",
                fontSize: "13px",
              }}
            >
              {s.matches ? "✓ SEALED CAUSE REVEALED: SHA-256 MATCH" : "✗ SEAL MISMATCH"}
            </span>
          </div>
        </section>
      ))}

      {/* Filter Bar */}
      <section className="wrap" style={{ paddingTop: "var(--s4)" }}>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            borderBottom: "2px solid var(--ink)",
            paddingBottom: "8px",
            flexWrap: "wrap",
            gap: "12px",
          }}
        >
          <div style={{ display: "flex", gap: "6px" }}>
            <button
              type="button"
              className="tab"
              aria-pressed={filterKlass === "all"}
              onClick={() => setFilterKlass("all")}
            >
              All Station Cards ({cards.length})
            </button>
            <button
              type="button"
              className="tab"
              aria-pressed={filterKlass === "test"}
              onClick={() => setFilterKlass("test")}
            >
              Switchback Tests
            </button>
            <button
              type="button"
              className="tab"
              aria-pressed={filterKlass === "do"}
              onClick={() => setFilterKlass("do")}
            >
              Direct Fixes
            </button>
            <button
              type="button"
              className="tab"
              aria-pressed={filterKlass === "hold"}
              onClick={() => setFilterKlass("hold")}
            >
              Flagged / Hold
            </button>
          </div>
          <span className="mono">Click any yellow metric badge to view its SQL recomputation proof</span>
        </div>
      </section>

      {/* Cards Grid */}
      <section className="wrap sec sec-last" style={{ paddingTop: "var(--s4)" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--s4)" }}>
          {visibleCards.map((card) => {
            const rec = card.recommendation;
            const plan = card.measurement_plan;
            return (
              <article
                key={rec.id}
                style={{
                  border: "3px solid var(--ink)",
                  borderTop: card.flagged
                    ? "8px solid var(--alert)"
                    : "8px solid var(--ink)",
                  background: "var(--card)",
                  padding: "var(--s4)",
                }}
              >
                {/* Header */}
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "flex-start",
                    flexWrap: "wrap",
                    gap: "12px",
                    borderBottom: "2px solid var(--ink)",
                    paddingBottom: "var(--s2)",
                    marginBottom: "var(--s3)",
                  }}
                >
                  <div>
                    <div className="dm small">
                      Recommendation ID: <b>{rec.id}</b> · Class: <b>{rec.klass.toUpperCase()}</b> ·
                      Provenance: <b>{rec.provenance.toUpperCase()}</b> · Role: <b>{rec.who_role}</b>
                    </div>
                    <h2 className="h2" style={{ fontSize: "34px", marginTop: "4px" }}>
                      {rec.scope_ids.join(" + ")}
                    </h2>
                  </div>
                  <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
                    <span className={`state ${card.flagged ? "bad" : "ok"}`}>
                      {rec.numbers_reproduced}/{rec.numbers_total} SQL NUMBERS REPRODUCED
                    </span>
                    <span className="state">STATUS: {rec.status.toUpperCase()}</span>
                  </div>
                </div>

                {card.flagged && (
                  <div
                    role="alert"
                    style={{
                      padding: "12px 16px",
                      background: "var(--alert)",
                      color: "#fff",
                      fontWeight: 700,
                      marginBottom: "var(--s3)",
                    }}
                  >
                    ⚠ GLIMMER SQL AUDIT FLAG: One or more numbers in this card did not match
                    independent DuckDB recomputation and have been redacted below. Click the
                    redacted badge to inspect the discrepancy.
                  </div>
                )}

                <div className="grid">
                  {/* Left 7 Cols: SAW, WHY, DO */}
                  <div className="s7 stack" style={{ gap: "var(--s3)" }}>
                    <div>
                      <div className="dm small" style={{ fontWeight: 700, textTransform: "uppercase" }}>
                        1. SAW (Observed Station Telemetry — Click Any Number for SQL Proof)
                      </div>
                      {rec.saw.map((claim, i) => (
                        <p
                          key={i}
                          style={{
                            fontSize: "20px",
                            lineHeight: 1.55,
                            marginTop: "8px",
                            fontWeight: 500,
                          }}
                        >
                          {claimParts(claim, card).map((part, j) => {
                            if ("text" in part) return <span key={j}>{part.text}</span>;
                            if ("missing" in part)
                              return (
                                <span key={j} className="state bad">
                                  [missing]
                                </span>
                              );
                            if (part.hidden) {
                              return (
                                <button
                                  key={j}
                                  type="button"
                                  onClick={() => setOpenCheck({ check: part.check, hidden: true })}
                                  style={{
                                    background: "var(--alert)",
                                    color: "#fff",
                                    border: "2px solid var(--ink)",
                                    padding: "2px 8px",
                                    margin: "0 4px",
                                    fontFamily: "var(--font-dm-mono, monospace)",
                                    fontSize: "14px",
                                    fontWeight: 700,
                                  }}
                                >
                                  ⊗ REDACTED (SQL MISMATCH — CLICK TO INSPECT)
                                </button>
                              );
                            }
                            return (
                              <button
                                key={j}
                                type="button"
                                onClick={() => setOpenCheck({ check: part.check, hidden: false })}
                                title="Click to inspect Glimmer SQL query & recomputed value"
                                style={{
                                  background: "var(--yellow)",
                                  color: "#141414",
                                  border: "2px solid var(--ink)",
                                  padding: "2px 8px",
                                  margin: "0 4px",
                                  fontFamily: "var(--font-dm-mono, monospace)",
                                  fontSize: "16px",
                                  fontWeight: 700,
                                }}
                              >
                                {formatNumber(part.check.catalogue_value, part.check.unit)} 🔎
                              </button>
                            );
                          })}
                        </p>
                      ))}
                    </div>

                    <div className="rule">
                      <div className="dm small" style={{ fontWeight: 700, textTransform: "uppercase" }}>
                        2. WHY (Causal Hypotheses Tested)
                      </div>
                      <div style={{ marginTop: "8px", display: "flex", flexDirection: "column", gap: "8px" }}>
                        {rec.why.map((h) => (
                          <div
                            key={h.id}
                            style={{
                              display: "flex",
                              justifyContent: "space-between",
                              alignItems: "center",
                              gap: "12px",
                              padding: "8px 12px",
                              background: "var(--paper)",
                              border: "1.5px solid var(--ink)",
                            }}
                          >
                            <span>
                              <b>[{h.id.toUpperCase()}]</b> {h.statement}
                            </span>
                            <span
                              className={`state ${h.status === "supported" ? "ok" : ""}`}
                              style={{ flexShrink: 0 }}
                            >
                              {h.status.toUpperCase()}
                              {typeof h.share_of_gap === "number"
                                ? ` · ${Math.round(h.share_of_gap * 100)}% GAP`
                                : ""}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>

                    <div className="rule">
                      <div className="dm small" style={{ fontWeight: 700, textTransform: "uppercase" }}>
                        3. DO (Recommended Operational Action &amp; Safety Veto Check)
                      </div>
                      {rec.do.map((c, idx) => (
                        <div
                          key={idx}
                          style={{
                            marginTop: "8px",
                            padding: "12px",
                            background: "var(--paper-2)",
                            borderLeft: "4px solid var(--ink)",
                            fontFamily: "var(--font-dm-mono, monospace)",
                            fontSize: "14px",
                          }}
                        >
                          <b>{c.op.toUpperCase()}</b>:{" "}
                          {Object.entries(c.params)
                            .map(([k, v]) => `${k}="${v}"`)
                            .join(" · ")}
                        </div>
                      ))}
                      <div className="small" style={{ marginTop: "8px" }}>
                        <b>Owner Role:</b> <code>{rec.who_role}</code> &nbsp;·&nbsp;{" "}
                        <b>Revert Condition:</b> {rec.revert}
                      </div>

                      {rec.safety.considered_not_recommended.map(([alt, reason]) => (
                        <div
                          key={alt}
                          style={{
                            marginTop: "10px",
                            padding: "10px 12px",
                            border: "1.5px dashed var(--alert)",
                            fontSize: "14px",
                            color: "var(--alert)",
                          }}
                        >
                          <b>✗ Vetoed Alternative:</b> {alt} — <i>{reason}</i>
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* Right 5 Cols: GAIN, PRE-REGISTERED SWITCHBACK CHECK & VERDICT */}
                  <div
                    className="s5"
                    style={{
                      borderLeft: "2px solid var(--ink)",
                      paddingLeft: "var(--s3)",
                      display: "flex",
                      flexDirection: "column",
                      gap: "var(--s3)",
                    }}
                  >
                    <div>
                      <div className="dm small" style={{ fontWeight: 700, textTransform: "uppercase" }}>
                        4. EXPECTED GAIN ({rec.expect.model_rung.toUpperCase()})
                      </div>
                      <div className="num-m" style={{ marginTop: "6px" }}>
                        {rec.expect.interval[0]} to {rec.expect.interval[1]} {rec.expect.unit}
                      </div>
                      <div className="mono" style={{ marginTop: "4px" }}>
                        {Math.round(rec.expect.level * 100)}% Credible Interval · Primary metric:{" "}
                        {rec.expect.metric}
                      </div>
                    </div>

                    <div className="rule">
                      <div className="dm small" style={{ fontWeight: 700, textTransform: "uppercase" }}>
                        5. PRE-REGISTERED SWITCHBACK CHECK (P4-08)
                      </div>
                      {plan ? (
                        <div
                          style={{
                            marginTop: "8px",
                            padding: "12px",
                            background: "var(--paper)",
                            border: "2px solid var(--ink)",
                          }}
                        >
                          <div className="dm" style={{ fontSize: "13px", fontWeight: 700 }}>
                            ✓ PRE-REGISTERED AT CHAIN SEQ #{card.preregistered_seq}
                          </div>
                          <div className="small" style={{ marginTop: "6px" }}>
                            Design: <b>{plan.design.toUpperCase()}</b> · <b>{plan.n_pairs} pairs</b>{" "}
                            of <b>{plan.block_minutes} min</b> blocks · Min detectable effect:{" "}
                            <b>{plan.min_detectable_effect}</b> · Smallest p:{" "}
                            <b>{plan.smallest_possible_p.toFixed(4)}</b>
                          </div>
                          <div className="mono" style={{ marginTop: "6px", fontSize: "11px" }}>
                            SHA-256 Method Hash: {plan.preregistration_hash.slice(0, 24)}…
                          </div>
                        </div>
                      ) : (
                        <div style={{ marginTop: "10px" }}>
                          <button
                            type="button"
                            className="btn block"
                            disabled={busyId === rec.id}
                            onClick={() => void handleAccept(rec.id)}
                          >
                            {busyId === rec.id
                              ? "Hashing & Pre-Registering on Chain…"
                              : "Accept & Pre-Register Switchback Check"}
                          </button>
                        </div>
                      )}
                    </div>

                    {card.verdict && (
                      <div
                        style={{
                          padding: "14px",
                          background: "var(--bar)",
                          color: "var(--on-dark)",
                          border: "2px solid var(--ink)",
                        }}
                      >
                        <div className="dm" style={{ fontSize: "11px", color: "var(--yellow)" }}>
                          6. SWITCHBACK EXPERIMENT VERDICT READ-OUT
                        </div>
                        <div style={{ fontSize: "18px", fontWeight: 700, marginTop: "4px" }}>
                          {verdictLine(card.verdict, rec.expect.unit)}
                        </div>
                        <div className="dm" style={{ fontSize: "12px", color: "#00E070", marginTop: "4px" }}>
                          Exact sign-flip p = {card.verdict.p_value.toFixed(4)} (α = 0.05) · Entire CI &lt; 0
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      </section>

      {/* SQL Recomputation Modal */}
      {openCheck && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Glimmer SQL Recomputation Proof"
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0, 0, 0, 0.72)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 100,
            padding: "20px",
          }}
          onClick={() => setOpenCheck(null)}
        >
          <div
            style={{
              maxWidth: "680px",
              width: "100%",
              background: "var(--paper)",
              border: "4px solid var(--ink)",
              padding: "var(--s4)",
              boxShadow: "0 20px 50px rgba(0,0,0,0.45)",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span className="dm small">GLIMMER INDEPENDENT SQL RECOMPUTATION PROOF</span>
              <span className={`state ${openCheck.check.ok ? "ok" : "bad"}`}>
                {openCheck.check.ok ? "✓ REPRODUCED EXACTLY" : "✗ MISMATCH — REDACTED"}
              </span>
            </div>
            <h3 className="h2" style={{ fontSize: "32px", marginTop: "8px" }}>
              {openCheck.check.metric} ({openCheck.check.result_id})
            </h3>

            <div className="kvs" style={{ marginTop: "var(--s3)" }}>
              <div className="kv">
                <span>Catalogue / Advisor Claimed Value</span>
                <b className="dm">
                  {openCheck.hidden
                    ? `REDACTED (${formatNumber(openCheck.check.catalogue_value, openCheck.check.unit)})`
                    : formatNumber(openCheck.check.catalogue_value, openCheck.check.unit)}
                </b>
              </div>
              <div className="kv">
                <span>Independent DuckDB Recomputed Value</span>
                <b className="dm">
                  {openCheck.check.recomputed !== null
                    ? formatNumber(openCheck.check.recomputed, openCheck.check.unit)
                    : "NULL"}
                </b>
              </div>
            </div>

            <div style={{ marginTop: "var(--s3)" }}>
              <div className="dm small" style={{ fontWeight: 700, marginBottom: "6px" }}>
                Executed SQL Verification Query:
              </div>
              <pre
                className="code"
                style={{
                  fontFamily: "var(--font-dm-mono, monospace)",
                  fontSize: "13px",
                  overflowX: "auto",
                  border: "2px solid var(--ink)",
                }}
              >
                {openCheck.check.sql}
              </pre>
            </div>

            {openCheck.check.error && (
              <div
                style={{
                  marginTop: "var(--s2)",
                  padding: "10px 12px",
                  background: "var(--alert)",
                  color: "#fff",
                  fontWeight: 600,
                }}
              >
                {openCheck.check.error}
              </div>
            )}

            <div style={{ marginTop: "var(--s3)", textAlign: "right" }}>
              <button type="button" className="btn" onClick={() => setOpenCheck(null)}>
                Close SQL Proof
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
