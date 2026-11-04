"use client";

// The incident the story follows: what was measured, what the verifier saw, the phone alert and
// its buttons, the spoken warning in the worker's language, the training duty, the toolbox talk,
// the evidence and the worker's dispute page.

import { useCallback, useEffect, useState } from "react";

import { api, errorText } from "@/lib/api";
import type { Alert } from "@/lib/ops";

import {
  metres,
  reportUrl,
  verifyUrl,
  fresh,
  warehouse,
  withRecovery,
  type NearMiss,
  type RefresherDuty,
  type SpokenWarning,
  type ToolboxTalk,
} from "./warehouse";
import styles from "./warehouse.module.css";

const LANGS: [string, string][] = [
  ["es", "Español"],
  ["en", "English"],
  ["pt", "Português"],
  ["hi", "हिन्दी"],
];

function useAction<T>(run: () => Promise<T>, done: (value: T) => void) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const go = async () => {
    setBusy(true);
    setError(null);
    try {
      done(await run());
    } catch (failure) {
      setError(errorText(failure, "The call failed."));
    } finally {
      setBusy(false);
    }
  };
  return { busy, error, go };
}

export function IncidentPanel({ near, planId }: { near: NearMiss; planId: string | null }) {
  const [lang, setLang] = useState(near.lang === "en" ? "es" : near.lang);
  // A second language spoken right after the crew's, e.g. "¡Montacargas acercándose! Forklift coming".
  const [also, setAlso] = useState<string | null>(null);
  const [warning, setWarning] = useState<SpokenWarning | null>(null);
  const [talk, setTalk] = useState<ToolboxTalk | null>(null);
  const [duty, setDuty] = useState<RefresherDuty | null>(null);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const event = near.event_id;

  const loadSaved = useCallback(async () => {
    const [w, t, d, a] = await Promise.allSettled([
      warehouse.savedWarning(event, lang, also),
      warehouse.savedTalk(event, lang),
      warehouse.duty(event),
      api.alerts(),
    ]);
    setWarning(w.status === "fulfilled" ? w.value : null);
    setTalk(t.status === "fulfilled" ? t.value : null);
    setDuty(d.status === "fulfilled" ? d.value : null);
    setAlerts(a.status === "fulfilled" ? a.value.filter((x) => x.event_id === event) : []);
  }, [event, lang, also]);

  useEffect(() => {
    void loadSaved();
  }, [loadSaved]);

  const speak = useAction(
    () =>
      withRecovery(
        () => warehouse.warning(event, lang, also),
        fresh(() => warehouse.savedWarning(event, lang, also)),
      ),
    setWarning,
  );
  const write = useAction(
    () =>
      withRecovery(
        () => warehouse.talk(event, lang),
        fresh(() => warehouse.savedTalk(event, lang)),
      ),
    setTalk,
  );
  const raise = useAction(
    () =>
      withRecovery(
        () => warehouse.raiseDuty(event),
        fresh(() => warehouse.duty(event)),
      ),
    setDuty,
  );
  const close = useAction(() => warehouse.dutyDone(event, "owner"), setDuty);
  const [tap, setTap] = useState<string | null>(null);

  async function respond(alert: Alert, button: string) {
    try {
      const result = await api.respond(alert.alert_id, button, alert.role);
      setTap(`${button}: ${result.action}`);
      await loadSaved();
    } catch (failure) {
      setTap(errorText(failure, "The tap failed."));
    }
  }

  const day = near.ts.slice(0, 10);
  return (
    <section className={styles.card} aria-labelledby="wh-incident">
      <h2 id="wh-incident">3. The near miss, and what happens next</h2>
      <p>
        <strong>{metres(near.separation_m)}</strong> from a moving vehicle (limit{" "}
        {metres(near.limit_m)}, closest {metres(near.closest_m)} within a second
        {near.vehicle_speed_mps !== null
          ? `, vehicle at ${near.vehicle_speed_mps.toFixed(2)} m/s`
          : ""}
        {near.ttc_s !== null ? `, contact in ${near.ttc_s.toFixed(1)} s` : ""}) on{" "}
        <span className={styles.mono}>{near.camera_id}</span> at{" "}
        {new Date(near.ts).toLocaleTimeString()}. Calibrated from {near.calibration ?? "–"}
        {near.calibration_err_m !== null ? ` (±${near.calibration_err_m.toFixed(2)} m)` : ""}.
      </p>
      <blockquote className={styles.quote}>
        <strong>
          {near.verifier ?? "Verifier"}: {near.verdict ?? "waiting"}
          {near.verdict_score !== null ? ` (${near.verdict_score.toFixed(2)})` : ""}
        </strong>{" "}
        {near.verdict_reason}
        <br />
        Decision: {near.alert === null ? "pending" : near.alert ? "alert" : "suppressed"}
        {near.route === "human_queue" ? ", and queued for a person" : ""}. Actions:{" "}
        {near.actions.join("; ") || "none yet"}.
      </blockquote>

      <h3>Phone alert</h3>
      {alerts.length === 0 && (
        <p className={styles.muted}>
          No phone alert for this event (actions in dry-run are recorded as “would …”).
        </p>
      )}
      {alerts.map((alert) => (
        <div key={alert.alert_id} className={styles.row}>
          <span className={styles.chip}>{alert.status}</span>
          <span>
            {alert.title} → {alert.role}
          </span>
          {alert.buttons.map((button) => (
            <button
              key={button}
              type="button"
              className={`${styles.button} ${button === "False alarm" ? styles.danger : ""}`}
              onClick={() => void respond(alert, button)}
            >
              {button}
            </button>
          ))}
        </div>
      ))}
      {tap && <p className={styles.muted}>{tap}</p>}

      <div className={styles.row}>
        <h3>Worker&apos;s language</h3>
        <select
          className={styles.input}
          style={{ width: "auto" }}
          value={lang}
          onChange={(e) => setLang(e.target.value)}
          aria-label="Worker's language"
        >
          {LANGS.map(([code, name]) => (
            <option key={code} value={code}>
              {name}
            </option>
          ))}
        </select>
        <label>
          <input
            type="checkbox"
            checked={also !== null}
            onChange={(e) => setAlso(e.target.checked ? (lang === "en" ? "es" : "en") : null)}
          />{" "}
          and {lang === "en" ? "Español" : "English"} (no tags, no app: the loudspeaker says both)
        </label>
      </div>

      <div className={styles.row}>
        <button type="button" className={styles.button} disabled={speak.busy} onClick={speak.go}>
          {speak.busy ? "Writing and speaking…" : "Spoken warning"}
        </button>
        <button type="button" className={styles.button} disabled={write.busy} onClick={write.go}>
          {write.busy ? "Writing the talk…" : "Toolbox talk"}
        </button>
        <button type="button" className={styles.button} disabled={raise.busy} onClick={raise.go}>
          {raise.busy ? "Reading the rule…" : "Training duty (OSHA)"}
        </button>
      </div>
      {[speak.error, write.error, raise.error, close.error].filter(Boolean).map((e) => (
        <p key={e} className={styles.error}>
          {e}
        </p>
      ))}

      {warning && (
        <div>
          <h3>
            Spoken warning ({(warning.parts ?? []).map((p) => p.lang).join(" + ") || warning.lang})
          </h3>
          <blockquote className={styles.quote}>
            “{warning.text}” <span className={styles.muted}>({warning.english})</span>
          </blockquote>
          {(warning.parts ?? []).slice(1).map((part) => (
            <blockquote key={part.lang} className={styles.quote}>
              then ({part.lang}) “{part.text}”
              {part.heard !== null ? ` · heard “${part.heard}” (WER ${part.wer?.toFixed(2)})` : ""}
            </blockquote>
          ))}
          {warning.audio && (
            <audio controls src={warehouse.fileUrl(warning.audio)}>
              <track kind="captions" />
            </audio>
          )}
          <p className={styles.muted}>
            Written by {warning.model}; spoken by {warning.tts ?? "no engine"}
            {warning.heard !== null
              ? `; Muse Voice Transcribe heard “${warning.heard}” (WER ${warning.wer?.toFixed(2)}, ${warning.roundtrip_ok ? "passed" : "failed"})`
              : ""}
            {warning.error ? `; ${warning.error}` : ""}.
          </p>
        </div>
      )}

      {duty && (
        <div>
          <h3>
            Refresher training: <span className={styles.chip}>{duty.status}</span>
          </h3>
          <p>
            <strong>{duty.clause.clause}</strong> {duty.clause.title}: “{duty.clause.text}”{" "}
            <a href={duty.clause.source_url} target="_blank" rel="noreferrer">
              source
            </a>{" "}
            <span className={styles.muted}>
              (
              {duty.clause.source_retrieved
                ? "page returned by the search"
                : "cited, not among the pages the search returned"}
              )
            </span>
          </p>
          <p>
            {duty.why} Forklift unit: {duty.vehicle_unit ?? "not legible in the frames"}. Due:{" "}
            {duty.due}.
          </p>
          {duty.training_focus.length > 0 && (
            <ul>
              {duty.training_focus.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          )}
          {duty.status === "due" && (
            <button
              type="button"
              className={`${styles.button} ${styles.primary}`}
              disabled={close.busy}
              onClick={close.go}
            >
              Mark training done
            </button>
          )}
          {duty.done_hash && (
            <p className={styles.muted}>
              Chained by {duty.done_by}: <span className={styles.mono}>{duty.done_hash}</span>
            </p>
          )}
        </div>
      )}

      {talk && (
        <div>
          <h3>
            {talk.title} <span className={styles.chip}>{talk.lang}</span>{" "}
            <span className={styles.muted}>about {talk.minutes} min</span>
          </h3>
          {talk.illustration && (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              className={styles.illustration}
              src={warehouse.fileUrl(talk.illustration)}
              alt={talk.illustration_prompt}
            />
          )}
          {talk.sections.map((section) => (
            <div key={section.heading}>
              <strong>{section.heading}</strong>
              <p>{section.text}</p>
            </div>
          ))}
          <ul>
            {talk.key_points.map((point) => (
              <li key={point}>{point}</li>
            ))}
          </ul>
          {talk.questions.length > 0 && (
            <ol>
              {talk.questions.map((question) => (
                <li key={question}>{question}</li>
              ))}
            </ol>
          )}
          <p className={styles.muted}>
            Written by {talk.model} from the chained records and evidence frames; drawn by{" "}
            {talk.image_model ?? "nobody"}
            {talk.illustration_error ? ` (${talk.illustration_error})` : ""}.
          </p>
        </div>
      )}

      <h3>Evidence and the worker&apos;s side</h3>
      <div className={styles.row}>
        <a className={styles.button} href={reportUrl(event)} target="_blank" rel="noreferrer">
          Incident report
        </a>
        <a className={styles.button} href={verifyUrl(day)} target="_blank" rel="noreferrer">
          Verify the chain in your browser
        </a>
        {planId && (
          <a
            className={styles.button}
            href={`/worker/${encodeURIComponent(planId)}/${encodeURIComponent(near.camera_id)}`}
            target="_blank"
            rel="noreferrer"
          >
            Worker page (QR): rules and disputes
          </a>
        )}
      </div>
      <p className={styles.muted}>
        Disputes: {near.disputes.join(", ") || "none"} · Regression:{" "}
        {near.regression ? "yes" : "no"} · Training done: {near.training_done ? "yes" : "no"}
      </p>
    </section>
  );
}
