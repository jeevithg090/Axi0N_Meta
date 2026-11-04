"use client";

// Step 1: the owner types or says the rule (any language); the Site Planner takes it from there.

import { useState } from "react";

import { api, errorText } from "@/lib/api";
import { recordingToWav } from "@/lib/wav";

import { useRecorder } from "./useRecorder";
import { PRESETS } from "./warehouse";
import styles from "./warehouse.module.css";

export function RequestPanel({ onPlan }: { onPlan: (planId: string) => void }) {
  const [text, setText] = useState(PRESETS[0]!.text);
  const [lang, setLang] = useState(PRESETS[0]!.lang);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const recorder = useRecorder();

  async function submitText() {
    setBusy(true);
    setStatus(null);
    try {
      const { plan_id } = await api.requestPlan([{ kind: "text", text, lang }]);
      setStatus(`Planning ${plan_id}: survey, probes, calibration, compile, Rule CI…`);
      onPlan(plan_id);
    } catch (error) {
      setStatus(errorText(error, "The planner did not accept the request."));
    } finally {
      setBusy(false);
    }
  }

  async function toggleVoice() {
    if (!recorder.recording) {
      await recorder.start();
      return;
    }
    const audio = await recorder.stop();
    if (audio === null) return;
    setBusy(true);
    setStatus("Sending the spoken request to Muse Voice Transcribe…");
    try {
      const wav = await recordingToWav(audio);
      const uploaded = await api.upload(wav, "request.wav");
      const { plan_id } = await api.requestPlan([{ kind: "voice", file: uploaded.file, lang }]);
      setStatus(`Planning ${plan_id} from your voice note…`);
      onPlan(plan_id);
    } catch (error) {
      setStatus(errorText(error, "The voice request failed."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className={styles.card} aria-labelledby="wh-request">
      <h2 id="wh-request">1. Say or type the rule</h2>
      <div className={styles.row}>
        {PRESETS.map((preset) => (
          <button
            key={preset.label}
            type="button"
            className={styles.button}
            onClick={() => {
              setText(preset.text);
              setLang(preset.lang);
            }}
          >
            {preset.label}
          </button>
        ))}
      </div>
      <textarea
        className={styles.input}
        rows={2}
        value={text}
        onChange={(event) => setText(event.target.value)}
        aria-label="Rule in plain words"
      />
      <div className={styles.row}>
        <label>
          Language{" "}
          <select
            className={styles.input}
            style={{ width: "auto" }}
            value={lang}
            onChange={(event) => setLang(event.target.value)}
          >
            <option value="es">Español</option>
            <option value="en">English</option>
            <option value="pt">Português</option>
            <option value="hi">हिन्दी</option>
          </select>
        </label>
        <button
          type="button"
          className={`${styles.button} ${styles.primary}`}
          disabled={busy || !text.trim()}
          onClick={submitText}
        >
          Plan this rule
        </button>
        <button type="button" className={styles.button} disabled={busy} onClick={toggleVoice}>
          {recorder.recording ? "Stop and send" : "Say it instead"}
        </button>
      </div>
      {recorder.error && <p className={styles.error}>{recorder.error}</p>}
      {status && <p className={styles.muted}>{status}</p>}
    </section>
  );
}
