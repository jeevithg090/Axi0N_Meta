"use client";

// "Report a near miss by voice" on the public worker page: no login, no name, the audio is not
// kept. The worker hears back only what was understood and whether a camera measured it.

import { useState } from "react";

import { useRecorder } from "@/app/usecases/warehouse/useRecorder";
import { API_PROXY } from "@/lib/config";

import styles from "./worker.module.css";

interface Heard {
  heard: string;
  what_happened: string;
  matched: boolean;
  measured_m: number | null;
}

const TEXT = {
  en: {
    title: "Report a near miss by voice",
    hint: "Say what happened, in your language. No name is needed and the recording is not kept.",
    start: "Start recording",
    stop: "Stop and send",
    sending: "Sending…",
    heard: "We heard",
    matched: (m: number | null) =>
      m === null ? "A camera saw it too." : `A camera measured it: ${m.toFixed(1)} m.`,
    unmatched: "No camera measured it; the site owner will read your report.",
    failed: "The report could not be sent. Please try again later.",
    limit: "You have sent several reports today; thank you.",
  },
  es: {
    title: "Reportar un casi accidente con la voz",
    hint: "Diga qué pasó, en su idioma. No hace falta su nombre y la grabación no se guarda.",
    start: "Grabar",
    stop: "Parar y enviar",
    sending: "Enviando…",
    heard: "Entendimos",
    matched: (m: number | null) =>
      m === null ? "Una cámara también lo vio." : `Una cámara lo midió: ${m.toFixed(1)} m.`,
    unmatched: "Ninguna cámara lo midió; el responsable leerá su reporte.",
    failed: "No se pudo enviar el reporte. Inténtelo más tarde.",
    limit: "Ya envió varios reportes hoy; gracias.",
  },
};

export function VoiceReport({ lang }: { lang: string }) {
  const t = lang.startsWith("es") ? TEXT.es : TEXT.en;
  const recorder = useRecorder();
  const [busy, setBusy] = useState(false);
  const [heard, setHeard] = useState<Heard | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function toggle() {
    if (!recorder.recording) {
      setHeard(null);
      setError(null);
      await recorder.start();
      return;
    }
    const audio = await recorder.stop();
    if (audio === null) return;
    setBusy(true);
    try {
      const response = await fetch(
        `${API_PROXY}/warehouse/public/reports/voice?lang=${encodeURIComponent(lang)}`,
        {
          method: "POST",
          headers: { "Content-Type": audio.type || "application/octet-stream" },
          body: audio,
        },
      );
      if (response.status === 429) setError(t.limit);
      else if (!response.ok) setError(t.failed);
      else setHeard((await response.json()) as Heard);
    } catch {
      setError(t.failed);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="voice-report">
      <h2 id="voice-report">{t.title}</h2>
      <p className="muted">{t.hint}</p>
      <button type="button" className={`tap ${styles.tap}`} disabled={busy} onClick={toggle}>
        {busy ? t.sending : recorder.recording ? t.stop : t.start}
      </button>
      {(recorder.error || error) && <p role="alert">{recorder.error || error}</p>}
      {heard && (
        <div role="status">
          <p>
            {t.heard}: “{heard.what_happened || heard.heard}”
          </p>
          <p>{heard.matched ? t.matched(heard.measured_m) : t.unmatched}</p>
        </div>
      )}
    </section>
  );
}
