"use client";

// Step 1: the owner asks, by typing or speaking in any language. The answer streams in step by
// step (SQL, SAM 3.1 counts, Glimmer looks, Spark watching video), then comes with a receipt the
// browser re-hashes itself and a QR code for the same check on a phone.

import { useRef, useState } from "react";

import { errorText } from "@/lib/api";
import {
  askReducer,
  type AskAnswer,
  type Exchange,
  frameUrl,
  newExchange,
  stepSummary,
  streamAsk,
  toolLabel,
} from "@/lib/ask";
import { recordingToWav } from "@/lib/wav";

import { receiptHolds, retail, type Receipt, speechLang } from "./api";
import styles from "./supermarket.module.css";

export const SAMPLE_QUESTIONS = [
  "When was the checkout queue longest today, how many tills were open, and do we need another till?",
  "How long did customers wait at the checkout on average, and how many arrived per minute?",
  "¿Cuántos clientes esperaban en las cajas y cuántas cajas estaban abiertas?",
  "Were any shelf gaps or floor hazards found today, and how fast were they fixed?",
];

export function speak(text: string, lang: string): void {
  if (typeof window === "undefined" || !("speechSynthesis" in window)) return;
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = speechLang(lang);
  window.speechSynthesis.speak(utterance);
}

function ReceiptCard({ answer }: { answer: AskAnswer }) {
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [holds, setHolds] = useState<boolean | null>(null);
  const [error, setError] = useState("");
  const sha = answer.receipt ?? "";
  const check = async (): Promise<void> => {
    setError("");
    try {
      const link = `${window.location.origin}/usecases/supermarket?receipt=${sha}`;
      const got = await retail.receipt(sha, link);
      setReceipt(got);
      setHolds(await receiptHolds(got));
    } catch (e) {
      setError(errorText(e, "Could not fetch the receipt."));
    }
  };
  if (!sha) return null;
  return (
    <div className={styles.card} data-testid="receipt">
      <div className={styles.row}>
        <strong>Receipt</strong>
        <span className={styles.mono}>{sha.slice(0, 16)}…</span>
        <button type="button" className={styles.ghost} onClick={() => void check()}>
          Verify in this browser
        </button>
      </div>
      {holds !== null && (
        <p className={holds ? styles.ok : styles.alert} role="status">
          {holds
            ? `✓ Your browser re-hashed the logged answer: it matches the receipt. Run log chain: ${
                receipt?.rows_verified ?? "?"
              } rows intact.`
            : "✗ The logged text does not hash to this receipt."}
        </p>
      )}
      {receipt?.qr_png_base64 && (
        <figure className={styles.row}>
          {/* eslint-disable-next-line @next/next/no-img-element -- a data URI */}
          <img
            src={`data:image/png;base64,${receipt.qr_png_base64}`}
            alt="QR code of this answer's receipt check"
            width={140}
            height={140}
          />
          <figcaption className={styles.muted}>
            Scan to check the same receipt on a phone.
          </figcaption>
        </figure>
      )}
      {error && <p className={styles.error}>{error}</p>}
    </div>
  );
}

export function AskPanel({ onAnswer }: { onAnswer: (answer: AskAnswer) => void }) {
  const [question, setQuestion] = useState<string>(SAMPLE_QUESTIONS[0] ?? "");
  const [exchange, setExchange] = useState<Exchange | null>(null);
  const [lang, setLang] = useState("en");
  const [busy, setBusy] = useState(false);
  const [recording, setRecording] = useState(false);
  const [heard, setHeard] = useState("");
  const [error, setError] = useState("");
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const conversation = exchange?.answer?.conversation_id ?? null;

  const finish = (answer: AskAnswer): void => {
    onAnswer(answer);
    speak(answer.answer, lang);
  };

  const ask = async (followUp: boolean): Promise<void> => {
    setBusy(true);
    setError("");
    setHeard("");
    let current = newExchange(question, followUp ? conversation : null);
    setExchange(current);
    try {
      await streamAsk({ question, conversation_id: followUp ? conversation : null }, (event) => {
        current = askReducer(current, event);
        setExchange(current);
        if (event.type === "answer" && event.answer) finish(event.answer);
      });
    } catch (e) {
      setError(errorText(e, "Asking failed."));
    } finally {
      setBusy(false);
    }
  };

  const toggleVoice = async (): Promise<void> => {
    if (recording) {
      recorder.current?.stop();
      return;
    }
    setError("");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const rec = new MediaRecorder(stream);
      chunks.current = [];
      rec.ondataavailable = (e) => chunks.current.push(e.data);
      rec.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        setRecording(false);
        void sendVoice(new Blob(chunks.current, { type: rec.mimeType }));
      };
      recorder.current = rec;
      rec.start();
      setRecording(true);
    } catch (e) {
      setError(errorText(e, "No microphone."));
    }
  };

  const sendVoice = async (blob: Blob): Promise<void> => {
    setBusy(true);
    try {
      const wav = await recordingToWav(blob);
      const reply = await retail.askVoice(wav, lang === "auto" ? null : lang, conversation);
      const answer = reply.answer as AskAnswer;
      setHeard(
        `Heard (${reply.lang || "?"}, ${reply.transcribe_seconds} s): “${reply.transcript}”`,
      );
      setQuestion(reply.transcript);
      setExchange({ ...newExchange(reply.transcript, conversation), answer });
      finish(answer);
    } catch (e) {
      setError(errorText(e, "The spoken question failed."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={styles.steps}>
      <div className={styles.row}>
        <input
          className={styles.input}
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          aria-label="Your question"
        />
        <button
          type="button"
          className={styles.button}
          disabled={busy}
          onClick={() => void ask(false)}
        >
          Ask
        </button>
        <button
          type="button"
          className={styles.ghost}
          disabled={busy || !conversation}
          onClick={() => void ask(true)}
        >
          Follow-up
        </button>
        <button
          type="button"
          className={styles.ghost}
          disabled={busy && !recording}
          onClick={() => void toggleVoice()}
        >
          {recording ? "■ Stop and ask" : "🎤 Speak"}
        </button>
        <select value={lang} onChange={(e) => setLang(e.target.value)} aria-label="Language">
          <option value="en">English</option>
          <option value="es">Español</option>
          <option value="hi">हिन्दी</option>
          <option value="auto">Any language</option>
        </select>
      </div>
      <div className={styles.row}>
        {SAMPLE_QUESTIONS.map((q) => (
          <button key={q} type="button" className={styles.ghost} onClick={() => setQuestion(q)}>
            {q.length > 48 ? `${q.slice(0, 48)}…` : q}
          </button>
        ))}
      </div>
      {heard && <p className={styles.muted}>{heard}</p>}
      {error && <p className={styles.error}>{error}</p>}
      {exchange && (
        <div className={styles.timeline} data-testid="ask-timeline">
          {exchange.turns.map((turn) => (
            <div key={turn.turn}>
              <strong>Turn {turn.turn + 1}</strong>{" "}
              <span className={styles.muted}>
                {turn.status === "thinking" ? "Muse Spark is choosing tools…" : turn.status}
                {turn.seconds !== null ? ` · ${turn.seconds.toFixed(1)} s` : ""}
              </span>
              {turn.reasoning && <div className={styles.reasoning}><span>Spark’s reasoning</span> {turn.reasoning}</div>}
              {turn.actions.map((view, i) => (
                <div key={i} className={styles.muted}>
                  · {toolLabel(view.action.action)}: {view.action.thought}
                  {view.step
                    ? ` → ${stepSummary(view.step)} (${view.step.seconds.toFixed(1)} s${
                        view.step.model ? `, ${view.step.model}` : ""
                      })`
                    : " …"}
                  {view.step && view.step.frames.length > 0 && (
                    <span className={styles.thumbs}>
                      {view.step.frames.slice(0, 4).map((f) => (
                        // eslint-disable-next-line @next/next/no-img-element -- API frames
                        <img key={f} src={frameUrl(f)} alt="frame the model saw" />
                      ))}
                    </span>
                  )}
                </div>
              ))}
            </div>
          ))}
          {exchange.answer && (
            <div className={styles.card}>
              <p className={styles.answer}>{exchange.answer.answer}</p>
              <p className={styles.muted}>
                {exchange.answer.seconds.toFixed(1)} s · {exchange.answer.steps.length} steps ·{" "}
                {exchange.answer.models.join(", ")}
              </p>
              <div className={styles.row}>
                <button
                  type="button"
                  className={styles.ghost}
                  onClick={() => speak(exchange.answer?.answer ?? "", lang)}
                >
                  Read aloud
                </button>
              </div>
              <ReceiptCard answer={exchange.answer} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
