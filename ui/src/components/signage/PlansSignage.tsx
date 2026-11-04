"use client";

import Link from "next/link";
import React, { useCallback, useEffect, useState } from "react";
import { api, errorText } from "../../lib/api";
import { planYaml, type PlanSummary, type SitePlan } from "../../lib/plan";

type ViewTab = "rules" | "feasibility" | "acceptance" | "yaml" | "json";

export function PlansSignage() {
  const [summaries, setSummaries] = useState<PlanSummary[]>([]);
  const [details, setDetails] = useState<Record<string, SitePlan>>({});
  const [openPlan, setOpenPlan] = useState<string>("");
  const [activeTab, setActiveTab] = useState<ViewTab>("rules");
  const [approvingId, setApprovingId] = useState<string | null>(null);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const loadPlans = useCallback(async () => {
    try {
      const list = await api.plans();
      // Sort approved / newest version first
      const sorted = [...list].sort((a, b) => {
        if (a.approved !== b.approved) return a.approved ? -1 : 1;
        return b.version - a.version;
      });
      setSummaries(sorted);
      const firstPlan = sorted[0];
      if (firstPlan && !openPlan) {
        setOpenPlan(firstPlan.plan_id);
      }
      const loaded: Record<string, SitePlan> = {};
      await Promise.all(
        sorted.map(async (s) => {
          try {
            loaded[s.plan_id] = await api.plan(s.plan_id);
          } catch {
            // ignore individual load error
          }
        }),
      );
      setDetails(loaded);
    } catch (err) {
      setErrorMsg(errorText(err, "Failed to load site plans from /plans"));
    }
  }, [openPlan]);

  useEffect(() => {
    void loadPlans();
  }, [loadPlans]);

  const handleApprove = async (planId: string) => {
    setApprovingId(planId);
    setStatusMsg(null);
    setErrorMsg(null);
    try {
      await api.approve(planId);
      setStatusMsg(
        `✓ Plan ${planId} approved, activated in RuntimeLoop, and anchored to the daily SHA-256 evidence chain.`,
      );
      await loadPlans();
    } catch (err) {
      setErrorMsg(errorText(err, `Failed to approve plan ${planId}`));
    } finally {
      setApprovingId(null);
    }
  };

  const handleDownloadYaml = (plan: SitePlan) => {
    const yamlText = planYaml(plan);
    const blob = new Blob([yamlText], { type: "text/yaml;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${plan.plan_id}.plan.yaml`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const approvedPlan = summaries.find((s) => s.approved);

  return (
    <div style={{ paddingBottom: "var(--s6)" }}>
      {/* Pagehead */}
      <section className="wrap pagehead">
        <div className="grid">
          <div className="s9">
            <div className="ctx">
              Site Plans Registry &nbsp;·&nbsp; <b>GET /plans ({summaries.length} registered)</b>{" "}
              &nbsp;·&nbsp; Active in Runtime: <b>{approvedPlan?.plan_id ?? "none"}</b>
            </div>
            <h1 className="h1">
              {summaries.length} site plans on file.{" "}
              <span className="hi">
                {approvedPlan ? `${approvedPlan.plan_id} is enforcing live.` : "Select a plan to activate."}
              </span>
            </h1>
          </div>
          <div className="s3 stack end">
            <Link href="/planner" className="btn block" style={{ textAlign: "center" }}>
              + Compile New Site Plan
            </Link>
          </div>
        </div>

        {statusMsg && (
          <div
            style={{
              marginTop: "var(--s2)",
              padding: "10px 14px",
              background: "var(--color-accent-soft)",
              color: "var(--color-ink)",
              border: "1px solid var(--color-rule)",
              borderRadius: "var(--radius-card)",
              fontWeight: 700,
            }}
          >
            {statusMsg}
          </div>
        )}
        {errorMsg && (
          <div
            style={{
              marginTop: "var(--s2)",
              padding: "10px 14px",
              background: "var(--color-bad-soft)",
              color: "var(--color-bad)",
              fontWeight: 700,
            }}
          >
            {errorMsg}
          </div>
        )}
      </section>

      <div className="tape" />

      {/* Plans List */}
      <section className="wrap sec sec-last">
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--s4)" }}>
          {summaries.map((s) => {
            const isOpen = openPlan === s.plan_id;
            const plan = details[s.plan_id];
            const passedChecks = plan?.acceptance.checks.filter((c) => c.passed).length ?? 0;
            const totalChecks = plan?.acceptance.checks.length ?? 0;

            return (
              <div
                key={s.plan_id}
                style={{
                  border: "1px solid var(--color-rule)",
                  borderRadius: "var(--radius-card)",
                  borderTop: s.approved ? "1px solid var(--color-rule-2)" : "1px solid var(--color-rule-2)",
                  background: isOpen ? "var(--paper-2)" : "var(--paper)",
                  padding: "var(--s3)",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "baseline",
                    cursor: "pointer",
                    flexWrap: "wrap",
                    gap: "var(--s2)",
                  }}
                  onClick={() => setOpenPlan(isOpen ? "" : s.plan_id)}
                >
                  <div>
                    <span className="dm small">
                      Site: <b>{s.site_name}</b> · Version v{s.version} ·{" "}
                      {plan ? `${plan.rules.length} compiled rules` : "Loading…"} ·{" "}
                      {totalChecks > 0 ? `Acceptance ${passedChecks}/${totalChecks} passed` : ""}
                    </span>
                    <h2 className="h2" style={{ fontSize: "34px", marginTop: "4px" }}>
                      {s.plan_id}{" "}
                      {plan?.setting_inferred ? `(${plan.setting_inferred.toUpperCase()})` : ""}
                    </h2>
                  </div>
                  <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
                    <span className={`state ${s.approved ? "ok" : ""}`}>
                      {s.approved ? "ACTIVE IN RUNTIME" : `STAGED v${s.version}`}
                    </span>
                    <button type="button" className="tab">
                      {isOpen ? "▲ Collapse" : "▼ Inspect Plan AST"}
                    </button>
                  </div>
                </div>

                {isOpen && plan && (
                  <div
                    style={{
                      marginTop: "var(--s3)",
                      borderTop: "1px solid var(--color-rule)",
                      paddingTop: "var(--s3)",
                    }}
                  >
                    {/* Sub-navigation tabs */}
                    <div
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                        flexWrap: "wrap",
                        gap: "8px",
                        marginBottom: "var(--s3)",
                      }}
                    >
                      <div style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
                        <button
                          type="button"
                          className="tab"
                          aria-pressed={activeTab === "rules"}
                          onClick={() => setActiveTab("rules")}
                        >
                          Rules &amp; Back-Translation ({plan.rules.length})
                        </button>
                        <button
                          type="button"
                          className="tab"
                          aria-pressed={activeTab === "feasibility"}
                          onClick={() => setActiveTab("feasibility")}
                        >
                          24-Frame Feasibility Probes ({plan.feasibility.length})
                        </button>
                        <button
                          type="button"
                          className="tab"
                          aria-pressed={activeTab === "acceptance"}
                          onClick={() => setActiveTab("acceptance")}
                        >
                          Acceptance Gate ({passedChecks}/{totalChecks})
                        </button>
                        <button
                          type="button"
                          className="tab"
                          aria-pressed={activeTab === "yaml"}
                          onClick={() => setActiveTab("yaml")}
                        >
                          Canonical YAML
                        </button>
                        <button
                          type="button"
                          className="tab"
                          aria-pressed={activeTab === "json"}
                          onClick={() => setActiveTab("json")}
                        >
                          Raw JSON AST
                        </button>
                      </div>

                      <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
                        <button
                          type="button"
                          className="btn"
                          disabled={approvingId === s.plan_id}
                          onClick={() => void handleApprove(s.plan_id)}
                          style={{ padding: "8px 14px", fontSize: "13px" }}
                        >
                          {approvingId === s.plan_id
                            ? "Activating…"
                            : s.approved
                              ? "✓ Re-Verify & Re-Broadcast Active Plan"
                              : "Approve & Activate in Runtime"}
                        </button>
                        <button
                          type="button"
                          className="btn o"
                          onClick={() => handleDownloadYaml(plan)}
                          style={{ padding: "8px 14px", fontSize: "13px" }}
                        >
                          ⬇ Export .plan.yaml
                        </button>
                        <Link
                          href="/ci"
                          className="btn o"
                          style={{ padding: "8px 14px", fontSize: "13px" }}
                        >
                          Inspect Rule CI Proofs →
                        </Link>
                      </div>
                    </div>

                    {activeTab === "rules" && (
                      <div>
                        <div className="scroll-x">
                          <table className="tbl">
                            <thead>
                              <tr>
                                <th>Rule ID</th>
                                <th>Natural Language Request &amp; Standards</th>
                                <th>Cameras &amp; Calibration</th>
                                <th>Severity &amp; Actions</th>
                              </tr>
                            </thead>
                            <tbody>
                              {plan.rules.map((r) => {
                                const zonesMap = (r.zones ?? {}) as Record<string, { camera_id?: string }>;
                                const camZones = Object.values(zonesMap)
                                  .map((z) => z.camera_id)
                                  .filter(Boolean);
                                return (
                                  <tr key={r.id}>
                                    <td>
                                      <b className="dm">{r.id}</b>
                                      <div className="small">v{r.version}</div>
                                    </td>
                                    <td>
                                      <div>{r.source.text}</div>
                                      {r.source.standard && r.source.standard.length > 0 && (
                                        <div className="dm small" style={{ marginTop: "4px" }}>
                                          Standards: {r.source.standard.join(", ")}
                                        </div>
                                      )}
                                    </td>
                                    <td className="dm small">
                                      <div>{String(r.calibration ?? "uncalibrated")}</div>
                                      {camZones.length > 0 && (
                                        <div>Zones: {camZones.join(", ")}</div>
                                      )}
                                    </td>
                                    <td>
                                      <span
                                        className={`state ${r.severity === "high" ? "bad" : "ok"}`}
                                      >
                                        {r.severity.toUpperCase()}
                                      </span>
                                      <div className="dm small" style={{ marginTop: "4px" }}>
                                        {r.actions.map((a) => `${a.kind}→${a.role ?? "all"}`).join(", ")}
                                      </div>
                                    </td>
                                  </tr>
                                );
                              })}
                            </tbody>
                          </table>
                        </div>

                        {plan.back_translation && (
                          <div style={{ marginTop: "var(--s3)" }}>
                            <h4 className="h3" style={{ fontSize: "18px", marginBottom: "8px" }}>
                              Independent Back-Translation by the Tier 1 model ([en] &amp; [es])
                            </h4>
                            <pre className="code" style={{ whiteSpace: "pre-wrap", fontSize: "13px" }}>
                              {plan.back_translation}
                            </pre>
                          </div>
                        )}
                      </div>
                    )}

                    {activeTab === "feasibility" && (
                      <div style={{ display: "flex", flexDirection: "column", gap: "var(--s2)" }}>
                        {plan.feasibility.map((f, idx) => (
                          <div
                            key={idx}
                            style={{
                              padding: "var(--s2)",
                              background: "var(--paper)",
                              border: "1px solid var(--color-rule)",
                              borderRadius: "var(--radius-card)",
                            }}
                          >
                            <div
                              style={{
                                display: "flex",
                                justifyContent: "space-between",
                                alignItems: "center",
                                marginBottom: "6px",
                              }}
                            >
                              <b>{f.concern}</b>
                              <span
                                className={`state ${f.verdict === "observable" ? "ok" : "bad"}`}
                              >
                                {f.verdict.toUpperCase()}
                              </span>
                            </div>
                            <p className="small" style={{ marginBottom: "8px" }}>
                              {f.says}
                            </p>
                            {f.alternative && (
                              <p className="dm small" style={{ color: "var(--alert)" }}>
                                Alternative: {f.alternative}
                              </p>
                            )}
                            {f.evidence.length > 0 && (
                              <div className="dm small" style={{ marginTop: "6px" }}>
                                Probes:{" "}
                                {f.evidence
                                  .map(
                                    (p) =>
                                      `${p.camera_id}:${p.concept} (rate ${(p.detect_rate * 100).toFixed(0)}%, Wilson LB ${p.detect_lb.toFixed(2)}, n=${p.n_frames})`,
                                  )
                                  .join(" · ")}
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    )}

                    {activeTab === "acceptance" && (
                      <div className="scroll-x">
                        <table className="tbl">
                          <thead>
                            <tr>
                              <th>Gate Check</th>
                              <th>Target Rule / Item</th>
                              <th>Detail</th>
                              <th>Result</th>
                            </tr>
                          </thead>
                          <tbody>
                            {plan.acceptance.checks.map((c, idx) => (
                              <tr key={idx}>
                                <td className="dm">
                                  <b>{c.check}</b>
                                </td>
                                <td className="dm small">{c.item}</td>
                                <td>{c.detail}</td>
                                <td>
                                  <span className={`state ${c.passed ? "ok" : "bad"}`}>
                                    {c.passed ? "PASS" : "FAIL"}
                                  </span>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}

                    {activeTab === "yaml" && (
                      <pre
                        className="code"
                        style={{ maxHeight: "460px", overflowY: "auto", fontSize: "12px" }}
                      >
                        {planYaml(plan)}
                      </pre>
                    )}

                    {activeTab === "json" && (
                      <pre
                        className="code"
                        style={{ maxHeight: "460px", overflowY: "auto", fontSize: "12px" }}
                      >
                        {JSON.stringify(plan, null, 2)}
                      </pre>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </section>
    </div>
  );
}
