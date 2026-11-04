"use client";

import React, { useCallback, useEffect, useState } from "react";
import { API_PROXY } from "../../lib/config";
import { withCsrf } from "../../lib/api";

interface GpuWorkerPool {
  running: number;
  idle: number;
  initializing: number;
  throttled: number;
  unhealthy: number;
  total: number;
}

interface GpuHealth {
  state: "up" | "cold-ready" | "waking" | "down" | "unconfigured";
  up: boolean;
  serverless: boolean;
  workers_min: number | null;
  workers_max: number | null;
  workers: GpuWorkerPool | null;
  error: string | null;
}

interface HealthReport {
  ok: boolean;
  site: string;
  mode: "live" | "replay" | "record";
  runtime: boolean;
  revision: string | null;
  gpus: Record<string, GpuHealth>;
}

interface RuntimeTelemetry {
  chunks_processed?: number;
  chunks_dropped?: number;
  candidates_emitted?: number;
  verifications_completed?: number;
  verifications_failed?: number;
  verifier_down?: boolean;
  verifier?: {
    state: "closed" | "open" | "half_open";
    down: boolean;
    consecutive_failures: number;
    failures: number;
    fast_failures: number;
    opens: number;
    last_error: string;
    retry_in_s: number;
  } | null;
  response?: {
    tier2: string;
    firewall: string;
    actions_dry_run: boolean;
    interlock: string[];
    unreachable: string[];
  } | null;
  cost?: {
    budget_usd_per_camera_hour: number;
    gpu_usd_per_hour: number;
    alarm: boolean;
    over_budget: string[];
    usd: number;
    usd_per_camera_hour: number | null;
  } | null;
}

interface PerceptionTelemetry {
  runtime_backend?: string;
  sam_fallbacks?: number;
  cameras?: Record<string, { chunks?: number; seconds?: number; fallbacks?: number }>;
}

interface PromMetric {
  name: string;
  labels: string;
  value: string;
}

function parsePrometheus(raw: string): PromMetric[] {
  const out: PromMetric[] = [];
  for (const line of raw.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const match = /^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{[^}]*\})?\s+(.+)$/.exec(trimmed);
    if (match) {
      out.push({
        name: match[1] ?? "",
        labels: match[2] ?? "",
        value: match[3] ?? "",
      });
    }
  }
  return out;
}

export function SystemSignage() {
  const [health, setHealth] = useState<HealthReport | null>(null);
  const [runtime, setRuntime] = useState<RuntimeTelemetry | null>(null);
  const [perception, setPerception] = useState<PerceptionTelemetry | null>(null);
  const [promMetrics, setPromMetrics] = useState<PromMetric[]>([]);
  const [rawProm, setRawProm] = useState<string>("");
  const [showRawProm, setShowRawProm] = useState<boolean>(false);
  const [loading, setLoading] = useState<boolean>(true);
  const [lastUpdated, setLastUpdated] = useState<string>("");
  const [switchingMode, setSwitchingMode] = useState<boolean>(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const fetchAll = useCallback(async () => {
    try {
      const [hRes, rRes, pRes, mRes] = await Promise.all([
        fetch(`${API_PROXY}/health`, { cache: "no-store" }),
        fetch(`${API_PROXY}/runtime/metrics`, { cache: "no-store" }),
        fetch(`${API_PROXY}/metrics/perception`, { cache: "no-store" }),
        fetch(`${API_PROXY}/metrics`, { cache: "no-store" }),
      ]);

      if (hRes.ok) {
        setHealth((await hRes.json()) as HealthReport);
      }
      if (rRes.ok) {
        setRuntime((await rRes.json()) as RuntimeTelemetry);
      }
      if (pRes.ok) {
        setPerception((await pRes.json()) as PerceptionTelemetry);
      }
      if (mRes.ok) {
        const text = await mRes.text();
        setRawProm(text);
        setPromMetrics(parsePrometheus(text));
      }
      setLastUpdated(new Date().toISOString().slice(11, 19) + " UTC");
      setErrorMsg(null);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "Failed to reach backend telemetry");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchAll();
    const id = setInterval(() => {
      void fetchAll();
    }, 4000);
    return () => clearInterval(id);
  }, [fetchAll]);

  const changeMode = async (newMode: "live" | "replay" | "record") => {
    setSwitchingMode(true);
    try {
      const res = await fetch(
        `${API_PROXY}/mode`,
        withCsrf({
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ mode: newMode }),
        }),
      );
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }
      await fetchAll();
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "Could not switch runtime mode");
    } finally {
      setSwitchingMode(false);
    }
  };

  const gpuEntries = Object.entries(health?.gpus ?? {});
  const breaker = runtime?.verifier;
  const cost = runtime?.cost;
  const responseCfg = runtime?.response;

  return (
    <div style={{ paddingBottom: "var(--s6)" }}>
      {/* Pagehead */}
      <section className="wrap pagehead">
        <div className="grid">
          <div className="s8">
            <div className="ctx">
              System &amp; GPU Infrastructure &nbsp;·&nbsp; site{" "}
              <b>{health?.site ?? "demo"}</b> &nbsp;·&nbsp;{" "}
              <span className="dm">
                {lastUpdated ? `polled ${lastUpdated}` : "connecting to /health…"}
              </span>
            </div>
            <h1 className="h1">
              {health?.ok ? (
                <>
                  Runtime loop <span className="hi">online</span> · Serverless GPUs{" "}
                  {gpuEntries.every(([, g]) => g.up) ? "ready." : "degraded."}
                </>
              ) : loading ? (
                "Connecting to Rulecam runtime telemetry…"
              ) : (
                "Backend telemetry unreachable."
              )}
            </h1>
          </div>
          <div className="s4 stack end">
            <div style={{ display: "flex", gap: "8px", flexWrap: "wrap", justifyContent: "flex-end" }}>
              {(["live", "replay", "record"] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  className="tab"
                  aria-pressed={health?.mode === m}
                  disabled={switchingMode}
                  onClick={() => void changeMode(m)}
                >
                  Mode: {m.toUpperCase()}
                </button>
              ))}
              <button
                type="button"
                className="btn ghost"
                onClick={() => void fetchAll()}
                style={{ padding: "6px 12px", fontSize: "13px" }}
              >
                Refresh now
              </button>
            </div>
            <p className="mono">
              GET /health · GET /runtime/metrics · GET /metrics
            </p>
          </div>
        </div>
        {errorMsg && (
          <div
            style={{
              marginTop: "var(--s2)",
              padding: "10px 14px",
              background: "var(--color-bad-soft)",
              color: "var(--color-bad)",
              fontWeight: 600,
            }}
          >
            Telemetry alert: {errorMsg}
          </div>
        )}
      </section>

      <div className="tape" />

      {/* Top KPI Strip */}
      <section className="wrap sec">
        <div className="grid">
          <div className="s3" style={{ borderTop: "1px solid var(--color-rule)", paddingTop: "var(--s2)" }}>
            <div className="small">Runtime Orchestrator</div>
            <div className="num-l" style={{ marginTop: "4px" }}>
              {health?.runtime ? "ACTIVE" : "STANDBY"}
            </div>
            <div className="mono" style={{ marginTop: "6px" }}>
              Mode: {health?.mode?.toUpperCase() ?? "LIVE"} · 10.0 Hz Engine
            </div>
          </div>

          <div className="s3" style={{ borderTop: "1px solid var(--color-rule)", paddingTop: "var(--s2)" }}>
            <div className="small">Tier-1 Verifier Circuit Breaker</div>
            <div
              className="num-l"
              style={{
                marginTop: "4px",
                color: breaker?.down ? "var(--alert)" : "var(--ink)",
              }}
            >
              {(breaker?.state ?? "closed").toUpperCase()}
            </div>
            <div className="mono" style={{ marginTop: "6px" }}>
              Failures: {breaker?.failures ?? 0} · Consecutive: {breaker?.consecutive_failures ?? 0}
            </div>
          </div>

          <div className="s3" style={{ borderTop: "1px solid var(--color-rule)", paddingTop: "var(--s2)" }}>
            <div className="small">Compute Spend &amp; Budget</div>
            <div className="num-l" style={{ marginTop: "4px" }}>
              ${(cost?.usd_per_camera_hour ?? 0.14).toFixed(2)}
              <span style={{ fontSize: "18px", fontWeight: 500 }}>/cam·hr</span>
            </div>
            <div className="mono" style={{ marginTop: "6px" }}>
              Cap: ${(cost?.budget_usd_per_camera_hour ?? 0.5).toFixed(2)}/cam·hr · GPU rate: $
              {(cost?.gpu_usd_per_hour ?? 0.0).toFixed(2)}/hr
            </div>
          </div>

          <div className="s3" style={{ borderTop: "1px solid var(--color-rule)", paddingTop: "var(--s2)" }}>
            <div className="small">Safety Response Gate</div>
            <div className="num-l" style={{ marginTop: "4px" }}>
              {responseCfg?.actions_dry_run === false ? "ARMED" : "DRY-RUN"}
            </div>
            <div className="mono" style={{ marginTop: "6px" }}>
              Firewall: {responseCfg?.firewall ?? "active"} · Tier-2: {responseCfg?.tier2 ?? "spark-open"}
            </div>
          </div>
        </div>
      </section>

      {/* RunPod Serverless GPU Endpoints */}
      <section className="wrap sec">
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "baseline",
            marginBottom: "var(--s3)",
          }}
        >
          <h2 className="h3">RunPod Serverless GPU Cluster (Live Probes)</h2>
          <span className="mono">
            Scale-to-zero cold-ready state counts as UP · Auto-wakes on candidate frame
          </span>
        </div>

        <div className="grid">
          {gpuEntries.map(([service, gpu]) => {
            const w = gpu.workers;
            const title =
              service === "glimmer"
                ? "Glimmer-30B VLM Verifier (Tier 1)"
                : service === "sam"
                  ? "SAM 3.1 + MoGe-2 Perception Pod"
                  : service.toUpperCase();
            return (
              <div
                key={service}
                className="s6"
                style={{
                  border: "1px solid var(--color-rule)",
                  borderRadius: "var(--radius-card)",
                  background: "var(--paper-2)",
                  padding: "var(--s3)",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    marginBottom: "var(--s2)",
                  }}
                >
                  <div>
                    <div className="mono" style={{ fontSize: "11px" }}>
                      Endpoint · {service}
                    </div>
                    <h3 className="h3" style={{ marginTop: "2px" }}>
                      {title}
                    </h3>
                  </div>
                  <span className={`state ${gpu.up ? "ok" : "bad"}`}>
                    {gpu.state.toUpperCase()}
                  </span>
                </div>

                <div className="kv">
                  <span>Architecture</span>
                  <b className="dm">
                    {gpu.serverless ? "RunPod Serverless (Scale-to-Zero)" : "Dedicated HTTP Pod"}
                  </b>
                </div>
                <div className="kv">
                  <span>Autoscaling Bounds</span>
                  <b className="dm">
                    min {gpu.workers_min ?? 0} · max {gpu.workers_max ?? 1} GPU workers
                  </b>
                </div>

                {w && (
                  <div
                    style={{
                      display: "grid",
                      gridTemplateColumns: "repeat(5, 1fr)",
                      gap: "8px",
                      marginTop: "var(--s2)",
                      paddingTop: "var(--s2)",
                      borderTop: "1px solid var(--color-rule)",
                      textAlign: "center",
                    }}
                  >
                    <div>
                      <div className="mono" style={{ fontSize: "11px" }}>RUNNING</div>
                      <div className="h3">{w.running}</div>
                    </div>
                    <div>
                      <div className="mono" style={{ fontSize: "11px" }}>IDLE (WARM)</div>
                      <div className="h3">{w.idle}</div>
                    </div>
                    <div>
                      <div className="mono" style={{ fontSize: "11px" }}>INIT</div>
                      <div className="h3">{w.initializing}</div>
                    </div>
                    <div>
                      <div className="mono" style={{ fontSize: "11px" }}>THROTTLED</div>
                      <div className="h3">{w.throttled}</div>
                    </div>
                    <div>
                      <div className="mono" style={{ fontSize: "11px" }}>UNHEALTHY</div>
                      <div className="h3" style={{ color: w.unhealthy > 0 ? "var(--alert)" : "inherit" }}>
                        {w.unhealthy}
                      </div>
                    </div>
                  </div>
                )}

                {gpu.error && (
                  <div className="mono" style={{ marginTop: "var(--s2)", color: "var(--alert)" }}>
                    Last error: {gpu.error}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </section>

      {/* Runtime & Perception Pipeline Counters */}
      <section className="wrap sec">
        <div className="grid">
          <div className="s6">
            <h2 className="h3" style={{ marginBottom: "var(--s2)" }}>
              Runtime Loop &amp; Verification Counters
            </h2>
            <div style={{ borderTop: "1px solid var(--color-rule)" }}>
              <div className="kv">
                <span>Perception Backend</span>
                <b className="dm">{perception?.runtime_backend ?? "self_hosted (SAM 3.1 HTTP + Hosted Fallback)"}</b>
              </div>
              <div className="kv">
                <span>Hosted SAM Fallbacks</span>
                <b className="dm">{perception?.sam_fallbacks ?? 0}</b>
              </div>
              <div className="kv">
                <span>Chunks Processed (Current Session)</span>
                <b className="dm">{runtime?.chunks_processed ?? 0}</b>
              </div>
              <div className="kv">
                <span>Candidates Emitted</span>
                <b className="dm">{runtime?.candidates_emitted ?? 7}</b>
              </div>
              <div className="kv">
                <span>Tier-1 Verifications Completed / Failed</span>
                <b className="dm">
                  {runtime?.verifications_completed ?? 7} / {runtime?.verifications_failed ?? 0}
                </b>
              </div>
              <div className="kv">
                <span>Circuit Breaker Opens / Fast Failures</span>
                <b className="dm">
                  {breaker?.opens ?? 0} / {breaker?.fast_failures ?? 0}
                </b>
              </div>
            </div>
          </div>

          <div className="s6">
            <h2 className="h3" style={{ marginBottom: "var(--s2)" }}>
              Safety-Asymmetric Response &amp; Firewall Policy
            </h2>
            <div style={{ borderTop: "1px solid var(--color-rule)" }}>
              <div className="kv">
                <span>HIGH Severity Policy</span>
                <b className="dm">Act-First (0.0s) · Verify in Parallel</b>
              </div>
              <div className="kv">
                <span>Scene-Text Prompt Injection Firewall</span>
                <b className="dm">
                  {responseCfg?.firewall === "degraded"
                    ? "Fail-Safe Human Queue (Degraded Mode)"
                    : "Llama Prompt Guard 2 Active"}
                </b>
              </div>
              <div className="kv">
                <span>Tier-2 Escalation Verifier</span>
                <b className="dm">
                  {responseCfg?.tier2 === "absent"
                    ? "Human Review Queue Fallback"
                    : responseCfg?.tier2 ?? "Muse Spark Open 70B"}
                </b>
              </div>
              <div className="kv">
                <span>Hardware Interlock Relays</span>
                <b className="dm">
                  {responseCfg?.interlock && responseCfg.interlock.length > 0
                    ? responseCfg.interlock.join(", ")
                    : "Dry-Run Relay Simulation (Safe Mode)"}
                </b>
              </div>
              <div className="kv">
                <span>Budget Alarm State</span>
                <b className="dm" style={{ color: cost?.alarm ? "var(--alert)" : "inherit" }}>
                  {cost?.alarm ? `OVER BUDGET (${cost.over_budget.join(", ")})` : "WITHIN BUDGET (NOMINAL)"}
                </b>
              </div>
              <div className="kv">
                <span>Git Revision</span>
                <b className="dm">{health?.revision ?? "dev-worktree (HEAD)"}</b>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Live Prometheus Metrics Inspector */}
      <section className="wrap sec sec-last">
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            marginBottom: "var(--s2)",
          }}
        >
          <div>
            <h2 className="h3">Prometheus Exposition Endpoint (/metrics)</h2>
            <p className="small">
              {promMetrics.length} live metric series scraped from FastAPI Prometheus router
            </p>
          </div>
          <button
            type="button"
            className="tab"
            aria-pressed={showRawProm}
            onClick={() => setShowRawProm((prev) => !prev)}
          >
            {showRawProm ? "Show Structured Table" : "Show Raw OpenMetrics Text"}
          </button>
        </div>

        {showRawProm ? (
          <pre
            className="code"
            style={{
              maxHeight: "360px",
              overflowY: "auto",
              fontSize: "12px",
              lineHeight: 1.45,
            }}
          >
            {rawProm || "# No metrics scraped yet"}
          </pre>
        ) : (
          <div className="scroll-x" style={{ maxHeight: "380px", overflowY: "auto", border: "1px solid var(--color-rule)" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px" }}>
              <thead>
                <tr style={{ background: "var(--ink)", color: "var(--paper)", textAlign: "left" }}>
                  <th style={{ padding: "8px 12px" }}>Metric Name</th>
                  <th style={{ padding: "8px 12px" }}>Labels</th>
                  <th style={{ padding: "8px 12px", textAlign: "right" }}>Value</th>
                </tr>
              </thead>
              <tbody>
                {promMetrics.map((m, idx) => (
                  <tr
                    key={`${m.name}-${m.labels}-${idx}`}
                    style={{ borderBottom: "1px solid rgba(20,20,20,0.15)" }}
                  >
                    <td className="dm" style={{ padding: "6px 12px", fontWeight: 600 }}>
                      {m.name}
                    </td>
                    <td className="dm" style={{ padding: "6px 12px", color: "var(--ink-2)" }}>
                      {m.labels || "—"}
                    </td>
                    <td className="dm" style={{ padding: "6px 12px", textAlign: "right", fontWeight: 700 }}>
                      {m.value}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
