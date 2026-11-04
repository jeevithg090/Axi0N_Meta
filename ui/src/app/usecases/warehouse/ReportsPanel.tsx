"use client";

// A worker reports a near miss by voice (any language) and the shift safety report.

import { useEffect, useState } from "react";

import { errorText } from "@/lib/api";

import { useRecorder } from "./useRecorder";
import { metres, warehouse, type ShiftReport, type VoiceReport } from "./warehouse";
import styles from "./warehouse.module.css";

export function VoiceReportPanel({ onPlan }: { onPlan: (planId: string) => void }) {
  const recorder = useRecorder();
  const [lang, setLang] = useState("es");
  const [reports, setReports] = useState<VoiceReport[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    warehouse
      .reports()
      .then(setReports)
      .catch(() => setReports([]));
  }, []);

  async function toggle() {
    if (!recorder.recording) {
      await recorder.start();
      return;
    }
    const audio = await recorder.stop();
    if (audio === null) return;
    setBusy(true);
    setError(null);
    try {
      const report = await warehouse.reportVoice(audio, lang);
      setReports((current) => [report, ...current]);
    } catch (failure) {
      setError(errorText(failure, "The report failed."));
    } finally {
      setBusy(false);
    }
  }

  async function propose(report: VoiceReport) {
    try {
      const updated = await warehouse.propose(report.report_id);
      setReports((current) =>
        current.map((r) => (r.report_id === updated.report_id ? updated : r)),
      );
      if (updated.plan_id) onPlan(updated.plan_id);
    } catch (failure) {
      setError(errorText(failure, "The proposal failed."));
    }
  }

  return (
    <section className={styles.card} aria-labelledby="wh-voice">
      <h2 id="wh-voice">A worker reports a near miss by voice</h2>
      <p className={styles.muted}>
        Muse Voice Transcribe writes it down; Muse Spark matches it to what the cameras measured and
        proposes a rule, which the planner and Rule CI check before anyone approves it. The audio is
        not kept.
      </p>
      <div className={styles.row}>
        <select
          className={styles.input}
          style={{ width: "auto" }}
          value={lang}
          onChange={(e) => setLang(e.target.value)}
          aria-label="Spoken language"
        >
          <option value="es">Español</option>
          <option value="en">English</option>
          <option value="pt">Português</option>
          <option value="hi">हिन्दी</option>
        </select>
        <button type="button" className={styles.button} disabled={busy} onClick={toggle}>
          {recorder.recording ? "Stop and report" : busy ? "Reading…" : "Report by voice"}
        </button>
      </div>
      {(recorder.error || error) && <p className={styles.error}>{recorder.error || error}</p>}
      {reports.map((report) => (
        <div key={report.report_id} className={styles.card}>
          <blockquote className={styles.quote}>“{report.transcript}”</blockquote>
          <p>
            {report.summary_en}{" "}
            {report.matches_event_id ? (
              <strong>
                Cameras confirm: {report.matches_event_id} measured {metres(report.measured_m)}.
              </strong>
            ) : (
              <strong>No camera near miss matched.</strong>
            )}{" "}
            <span className={styles.muted}>{report.match_reason}</span>
          </p>
          <p>
            Proposed rule: “{report.proposed_rule}”{" "}
            <span className={styles.muted}>({report.proposed_rule_en})</span>
          </p>
          {report.plan_id ? (
            <p className={styles.muted}>
              Sent to the planner as <span className={styles.mono}>{report.plan_id}</span>.
            </p>
          ) : (
            <button type="button" className={styles.button} onClick={() => void propose(report)}>
              Plan and prove this rule
            </button>
          )}
        </div>
      ))}
    </section>
  );
}

export function ShiftPanel() {
  const [report, setReport] = useState<ShiftReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function write() {
    setBusy(true);
    setError(null);
    try {
      setReport(await warehouse.shift("en"));
    } catch (failure) {
      setError(errorText(failure, "The report failed."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className={styles.card} aria-labelledby="wh-shift">
      <h2 id="wh-shift">Shift safety report</h2>
      <button type="button" className={styles.button} disabled={busy} onClick={write}>
        {busy ? "Writing…" : "Write today's report"}
      </button>
      {error && <p className={styles.error}>{error}</p>}
      {report && (
        <div>
          <h3>{report.headline}</h3>
          <p>{report.summary}</p>
          <p className={styles.muted}>
            Counted from the chain: {report.counts.candidates} near misses, {report.counts.alerts}{" "}
            alerts, {report.counts.suppressed} suppressed, {report.counts.human_queue} to a person,{" "}
            {report.counts.acked} acknowledged, {report.counts.disputes} disputes (
            {report.counts.upheld} upheld), closest {metres(report.counts.closest_m)}.
          </p>
          {report.patterns.length > 0 && (
            <ul>
              {report.patterns.map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
          )}
          {report.actions.length > 0 && (
            <ol>
              {report.actions.map((a) => (
                <li key={a}>{a}</li>
              ))}
            </ol>
          )}
          <p className={styles.muted}>
            Written by {report.model}. Chain root{" "}
            <span className={styles.mono}>{report.chain_root ?? "–"}</span>
          </p>
        </div>
      )}
    </section>
  );
}
