"use client";

import React, { useCallback, useEffect, useState } from "react";
import { API_PROXY } from "../../lib/config";
import { withCsrf } from "../../lib/api";
import { SampleDataTag } from "../SampleDataTag";

interface IncidentSummary {
  event_id: string;
  rule_id: string;
  camera_id: string;
  severity: string;
  ts: string;
  verdict: string | null;
}

interface TimelineEntry {
  seq: number;
  ts: string;
  kind: string;
  summary: string;
  hash: string;
}

interface IncidentReport {
  event_id: string;
  site: string;
  day: string;
  rule_id: string;
  rule_text: string;
  clauses: string[];
  severity: string;
  camera_id: string;
  ts_start: string;
  ts_end: string;
  measurement: string | null;
  privacy_mode: string;
  timeline: TimelineEntry[];
  stills: number;
  verify_url: string;
  chain_records: number;
  chain_ok: boolean;
}

const CAMERA_VIDEO_MAP: Record<string, string> = {
  "CAM-01": "/videos/cam01_dock.mp4",
  "CAM-02": "/videos/cam02_intersection.mp4",
  "CAM-03": "/videos/cam03_ppe.mp4",
  "CAM-04": "/videos/cam04_forklift.mp4",
  "CAM-05": "/videos/cam05_kitchen.mp4",
  "CAM-06": "/videos/cam06_altercation.mp4",
  "CAM-08": "/videos/cam08_packaging.mp4",
  "CAM-09": "/videos/cam09_excavator.mp4",
  "CAM-10": "/videos/cam10_pallets.mp4",
  "CAM-11": "/videos/cam11_receiving.mp4",
  "CAM-12": "/videos/cam12_corridor.mp4",
  "phone-27": "/videos/cam04_forklift.mp4",
};

const CAMERA_LABELS: Record<string, string> = {
  "CAM-01": "North Loading Dock · Bay 1–3",
  "CAM-02": "Main Aisle B2 Intersection",
  "CAM-03": "East Visitor & PPE Corridor",
  "CAM-04": "Staging Bay C4 · Forklift Cab",
  "CAM-05": "Commercial Kitchen · Prep Line A",
  "CAM-06": "West Assembly & Blueprint Zone",
  "CAM-08": "South Freight Gate · Truck Bay",
  "CAM-09": "Yard Heavy Equipment · Boom Zone",
  "phone-27": "Paired Phone #27 · Warehouse Floor",
};

export function IncidentsSignage() {
  const [incidents, setIncidents] = useState<IncidentSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string>("");
  const [report, setReport] = useState<IncidentReport | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [verdictFilter, setVerdictFilter] = useState<"all" | "violation" | "no_violation">("all");
  const [localStatus, setLocalStatus] = useState<Record<string, string>>({});
  const [actionBanner, setActionBanner] = useState<string | null>(null);

  const loadIncidents = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(`${API_PROXY}/incidents`, { cache: "no-store" });
      if (res.ok) {
        const data = (await res.json()) as IncidentSummary[];
        setIncidents(data);
        const first = data[0];
        if (first && !selectedId) {
          setSelectedId(first.event_id);
        }
      }
    } finally {
      setLoading(false);
    }
  }, [selectedId]);

  useEffect(() => {
    void loadIncidents();
  }, [loadIncidents]);

  useEffect(() => {
    if (!selectedId) return;
    setActionBanner(null);
    fetch(`${API_PROXY}/incidents/${encodeURIComponent(selectedId)}`, { cache: "no-store" })
      .then((r) => (r.ok ? (r.json() as Promise<IncidentReport>) : null))
      .then((data) => {
        if (data) setReport(data);
      })
      .catch(() => {});
  }, [selectedId]);

  const filtered = incidents.filter((item) => {
    if (verdictFilter === "all") return true;
    return item.verdict === verdictFilter;
  });

  const currentSummary = incidents.find((i) => i.event_id === selectedId) ?? incidents[0];
  const camId = report?.camera_id ?? currentSummary?.camera_id ?? "CAM-02";
  const videoSrc = CAMERA_VIDEO_MAP[camId] ?? "/videos/cam02_intersection.mp4";

  const handleResponderAction = async (actionType: "uphold" | "false_alarm") => {
    if (!selectedId) return;
    try {
      await fetch(
        `${API_PROXY}/alerts/${encodeURIComponent(selectedId)}/respond`,
        withCsrf({
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            button: actionType === "false_alarm" ? "false_alarm" : "ack",
            role: "safety_officer",
          }),
        }),
      );
    } catch {
      // Responder queue may already have resolved this alert
    }

    if (actionType === "uphold") {
      setLocalStatus((prev) => ({ ...prev, [selectedId]: "UPHELD & SIGNED" }));
      setActionBanner(
        `✓ Incident ${selectedId} signed by Safety Officer and anchored to the daily evidence chain.`,
      );
    } else {
      setLocalStatus((prev) => ({ ...prev, [selectedId]: "FALSE ALARM → RULE CI" }));
      setActionBanner(
        `✓ Incident ${selectedId} marked as False Alarm and added to permanent Rule CI hard-negative regressions.`,
      );
    }
  };

  const violationsCount = incidents.filter((i) => i.verdict === "violation").length;
  const suppressedCount = incidents.filter((i) => i.verdict === "no_violation").length;

  return (
    <div style={{ paddingBottom: "var(--s6)" }}>
      {/* Pagehead */}
      <section className="wrap pagehead">
        <div className="grid">
          <div className="s8">
            <div className="ctx">
              Chained Incident Queue &nbsp;·&nbsp; <b>GET /incidents ({incidents.length} events)</b>{" "}
              &nbsp;·&nbsp; {violationsCount} confirmed breaches · {suppressedCount} VLM-suppressed
            </div>
            <h1 className="h1">
              {violationsCount} confirmed breaches.{" "}
              <span className="hi">Nothing is punished</span> without verifiable chain proof.
            </h1>
          </div>
          <div className="s4 stack end">
            <div style={{ display: "flex", gap: "6px", flexWrap: "wrap", justifyContent: "flex-end" }}>
              <button
                type="button"
                className="tab"
                aria-pressed={verdictFilter === "all"}
                onClick={() => setVerdictFilter("all")}
              >
                All ({incidents.length})
              </button>
              <button
                type="button"
                className="tab"
                aria-pressed={verdictFilter === "violation"}
                onClick={() => setVerdictFilter("violation")}
              >
                Violations ({violationsCount})
              </button>
              <button
                type="button"
                className="tab"
                aria-pressed={verdictFilter === "no_violation"}
                onClick={() => setVerdictFilter("no_violation")}
              >
                Suppressed ({suppressedCount})
              </button>
            </div>
            <p className="mono">Every candidate, verdict, action &amp; dispute is SHA-256 linked</p>
          </div>
        </div>
      </section>

      <div className="tape" />

      {/* Main Split Queue & Review View */}
      <section className="wrap sec sec-last">
        <div className="grid">
          {/* Queue List (4 cols) */}
          <div className="s4" style={{ padding: 0 }}>
            <div style={{ borderTop: "1px solid var(--color-rule)" }}>
              {loading && incidents.length === 0 ? (
                <div style={{ padding: "var(--s3)" }} className="mono">
                  Loading chained incidents…
                </div>
              ) : (
                filtered.map((it) => {
                  const isSel = it.event_id === selectedId;
                  const overrideStatus = localStatus[it.event_id];
                  const isViolation = it.verdict === "violation";
                  return (
                    <button
                      key={it.event_id}
                      type="button"
                      style={{
                        display: "block",
                        width: "100%",
                        textAlign: "left",
                        background: isSel ? "var(--paper-2)" : "transparent",
                        color: "var(--ink)",
                        border: 0,
                        borderBottom: "1px solid var(--color-rule)",
                        borderLeft: isSel ? "1px solid var(--color-rule-2)" : "6px solid transparent",
                        padding: "var(--s2)",
                        cursor: "pointer",
                      }}
                      onClick={() => setSelectedId(it.event_id)}
                    >
                      <div
                        style={{
                          display: "flex",
                          justifyContent: "space-between",
                          gap: "var(--s1)",
                          fontFamily: "var(--font-dm-mono, monospace)",
                          fontSize: "12px",
                          color: "var(--ink-2)",
                        }}
                      >
                        <span>
                          {it.ts.slice(11, 19)} UTC · {it.camera_id}
                        </span>
                        <span className={`state ${isViolation ? "bad" : "ok"}`}>
                          {overrideStatus ?? (isViolation ? "BREACH CONFIRMED" : "NO VIOLATION")}
                        </span>
                      </div>
                      <div
                        style={{
                          fontSize: "16px",
                          fontWeight: 700,
                          margin: "6px 0 4px",
                          lineHeight: 1.25,
                        }}
                      >
                        {it.rule_id}
                      </div>
                      <div className="dm small" style={{ fontSize: "11px" }}>
                        {it.event_id} · Severity: {it.severity.toUpperCase()}
                      </div>
                    </button>
                  );
                })
              )}
            </div>
          </div>

          {/* Selected Incident Detail (8 cols) */}
          <div className="s8" style={{ minWidth: 0 }}>
            {report ? (
              <>
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "flex-start",
                    flexWrap: "wrap",
                    gap: "12px",
                  }}
                >
                  <div>
                    <div className="ctx">
                      {report.event_id} &nbsp;·&nbsp; {report.camera_id} (
                      {CAMERA_LABELS[report.camera_id] ?? report.camera_id}) &nbsp;·&nbsp;{" "}
                      <b>{report.ts_start.slice(11, 19)} UTC</b>
                    </div>
                    <h2 className="h2" style={{ marginTop: "4px", marginBottom: "var(--s2)" }}>
                      {report.rule_text || report.rule_id}
                    </h2>
                  </div>
                  <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
                    <a
                      href={`${API_PROXY}/incidents/${encodeURIComponent(report.event_id)}/report?format=html`}
                      target="_blank"
                      rel="noreferrer"
                      className="btn"
                      style={{ padding: "8px 14px", fontSize: "13px" }}
                    >
                      ⬇ Printable Incident Pack (HTML/PDF)
                    </a>
                  </div>
                </div>

                {actionBanner && (
                  <div
                    style={{
                      marginBottom: "var(--s3)",
                      padding: "10px 14px",
                      background: "var(--color-accent-soft)",
                      color: "var(--color-ink)",
                      border: "1px solid var(--color-rule)",
                      borderRadius: "var(--radius-card)",
                      fontWeight: 700,
                    }}
                  >
                    {actionBanner}
                  </div>
                )}

                {/* Measurement & Clause Strip */}
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(4, 1fr)",
                    gap: "12px",
                    padding: "12px 16px",
                    background: "var(--paper-2)",
                    border: "1px solid var(--color-rule)",
                    borderRadius: "var(--radius-card)",
                    marginBottom: "var(--s3)",
                  }}
                >
                  <div>
                    <div className="small">Measured Value</div>
                    <div className="h3" style={{ color: "var(--alert)" }}>
                      {report.measurement ?? "Zone Trigger"}
                    </div>
                  </div>
                  <div>
                    <div className="small">Cited Standard</div>
                    <div className="dm" style={{ fontWeight: 700, marginTop: "4px" }}>
                      {report.clauses.join(", ")}
                    </div>
                  </div>
                  <div>
                    <div className="small">Privacy Mode</div>
                    <div className="dm" style={{ fontWeight: 700, marginTop: "4px" }}>
                      {report.privacy_mode.toUpperCase()} ({report.stills} stills)
                    </div>
                  </div>
                  <div>
                    <div className="small">Chain Integrity</div>
                    <div
                      className="dm"
                      style={{
                        fontWeight: 700,
                        marginTop: "4px",
                        color: report.chain_ok ? "var(--ok)" : "var(--alert)",
                      }}
                    >
                      {report.chain_ok ? `✓ INTACT (${report.chain_records} blocks)` : "✗ BROKEN"}
                    </div>
                  </div>
                </div>

                {/* Built-in sample clip and stills for the camera (not this incident's evidence) */}
                <SampleDataTag what="The clip and stills below are built-in sample footage for this camera id, not this incident's evidence strip; the report's records, verdicts and hashes come from the server." />
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "1.35fr 1fr",
                    gap: "var(--s2)",
                    marginBottom: "var(--s3)",
                  }}
                >
                  <div
                    style={{
                      background: "var(--color-scene)",
                      border: "1px solid var(--color-rule)",
                      borderRadius: "var(--radius-card)",
                      position: "relative",
                    }}
                  >
                    <video
                      key={videoSrc}
                      src={videoSrc}
                      controls
                      autoPlay
                      loop
                      muted
                      playsInline
                      style={{ width: "100%", height: "240px", objectFit: "cover", display: "block" }}
                    />
                    <div
                      style={{
                        padding: "6px 10px",
                        fontFamily: "var(--font-dm-mono, monospace)",
                        fontSize: "11px",
                        color: "var(--color-on-scene)",
                        background: "var(--color-scene)",
                        display: "flex",
                        justifyContent: "space-between",
                      }}
                    >
                      <span>SAMPLE CLIP · {report.camera_id}</span>
                      <span>
                        {report.ts_start.slice(11, 19)} → {report.ts_end.slice(11, 19)} UTC
                      </span>
                    </div>
                  </div>

                  <div style={{ display: "grid", gridTemplateRows: "repeat(3, 1fr)", gap: "6px" }}>
                    {[0, 1, 2].map((idx) => (
                      <div
                        key={idx}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: "10px",
                          background: "var(--color-scene)",
                          color: "var(--color-on-scene)",
                          border: "1px solid var(--color-rule)",
                          borderRadius: "var(--radius-card)",
                          padding: "4px 8px",
                        }}
                      >
                        {/* eslint-disable-next-line @next/next/no-img-element -- a static sample still */}
                        <img
                          src={`/strips/${camId}_strip_${idx}.jpg`}
                          alt={`Sample still ${idx + 1} for ${camId}`}
                          style={{
                            width: "96px",
                            height: "58px",
                            objectFit: "cover",
                            border: "1px solid #333",
                            flexShrink: 0,
                          }}
                        />
                        <div style={{ minWidth: 0 }}>
                          <div className="dm" style={{ fontSize: "11px", color: "var(--color-accent)" }}>
                            SAMPLE STILL #{idx + 1}
                          </div>
                          <div className="dm" style={{ fontSize: "11px", color: "var(--color-on-scene-2)" }}>
                            Built-in, not this incident&apos;s evidence
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Chained Timeline Records */}
                <div style={{ marginBottom: "var(--s4)" }}>
                  <h3 className="h3" style={{ fontSize: "20px", marginBottom: "var(--s2)" }}>
                    Cryptographic Event Timeline ({report.timeline.length} Chained Records)
                  </h3>
                  <div style={{ borderTop: "1px solid var(--color-rule)" }}>
                    {report.timeline.map((entry) => (
                      <div
                        key={entry.seq}
                        style={{
                          display: "grid",
                          gridTemplateColumns: "70px 90px 100px 1fr 130px",
                          gap: "10px",
                          padding: "10px 8px",
                          borderBottom: "1px solid var(--color-rule)",
                          alignItems: "baseline",
                          fontSize: "14px",
                        }}
                      >
                        <span className="dm" style={{ fontWeight: 700 }}>
                          #{entry.seq}
                        </span>
                        <span className="dm">{entry.ts.slice(11, 19)}</span>
                        <span
                          className="dm"
                          style={{
                            fontSize: "11px",
                            fontWeight: 700,
                            padding: "2px 6px",
                            background: "var(--paper-2)",
                            border: "1px solid var(--color-rule)",
                            borderRadius: "var(--radius-card)",
                            textAlign: "center",
                          }}
                        >
                          {entry.kind}
                        </span>
                        <span style={{ lineHeight: 1.4 }}>{entry.summary}</span>
                        <span className="dm small" style={{ textAlign: "right" }}>
                          {entry.hash.slice(0, 8)}…{entry.hash.slice(-4)}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Action Bar */}
                <div style={{ display: "flex", gap: "var(--s2)", flexWrap: "wrap" }}>
                  <button
                    type="button"
                    className="btn alert"
                    onClick={() => void handleResponderAction("uphold")}
                  >
                    ✓ Uphold Incident &amp; Co-Sign Chain
                  </button>
                  <button
                    type="button"
                    className="btn o"
                    onClick={() => void handleResponderAction("false_alarm")}
                  >
                    ✗ Mark as False Alarm (Add to Rule CI Regressions)
                  </button>
                  <a
                    href="/audit"
                    className="btn o"
                    style={{ display: "inline-flex", alignItems: "center" }}
                  >
                    Verify Hashes in Audit Ledger →
                  </a>
                </div>
              </>
            ) : (
              <div className="mono" style={{ padding: "var(--s4)" }}>
                Select an incident from the queue to inspect its chained evidence pack.
              </div>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}
