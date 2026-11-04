"use client";

// Push-to-talk Ask: hold the button, speak, release. Only that recording is sent (Muse Voice
// Transcribe), never ambient audio. The answer comes back from the Ask agent and is spoken.
// Where the API serves the warehouse's spoken near-miss reports, a second hold button sends
// the recording there instead (`POST /warehouse/reports/voice`).

import { useRef, useState } from "react";

import { errorText } from "@/lib/api";
import { recordingToWav } from "@/lib/wav";

import { sayLine, unlockAudio } from "../_lib/alerting";
import { reportNearMiss, staffApi } from "../_lib/client";
import type { Words } from "../_lib/i18n";
import type { AskReply, Lang, StaffConfig, StaffItem } from "../_lib/types";
import styles from "../staff.module.css";
import { icons } from "@/components/icons";

type Phase = "idle" | "listening" | "thinking";
type Purpose = "ask" | "report";

export function AskDock({
  role,
  lang,
  words,
  config,
  onTasks,
}: {
  role: string;
  lang: Lang;
  words: Words;
  config: StaffConfig | null;
  onTasks: (items: StaffItem[]) => void;
}) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [reply, setReply] = useState<AskReply | null>(null);
  const [note, setNote] = useState("");
  const [typing, setTyping] = useState(false);
  const [question, setQuestion] = useState("");
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const shelfInput = useRef<HTMLInputElement>(null);
  const purpose = useRef<Purpose>("ask");

  const report = async (audio: Blob) => {
    setPhase("thinking");
    setNote("");
    try {
      const filed = await reportNearMiss(audio, lang);
      const matched = filed.matches_event_id
        ? ` (${filed.matches_event_id}${filed.measured_m ? `, ${filed.measured_m.toFixed(1)} m` : ""})`
        : "";
      setReply({
        heard: filed.transcript,
        answer: `${filed.what_happened}${matched}`,
        lang,
        audio: null,
        engine: null,
        seconds: 0,
        models: filed.model ? [filed.model] : [],
      });
      void sayLine({ text: filed.what_happened, audio: null }, lang);
    } catch (error) {
      setNote(errorText(error, "Near-miss report not sent"));
    } finally {
      setPhase("idle");
    }
  };

  const send = async (audio: Blob | null, text = "") => {
    setPhase("thinking");
    setNote("");
    try {
      const answer = await staffApi.ask(role, lang, audio, text);
      setReply(answer);
      void sayLine({ text: answer.answer, audio: answer.audio }, lang);
    } catch (error) {
      setNote(errorText(error, "Ask is not available"));
    } finally {
      setPhase("idle");
    }
  };

  const start = async (why: Purpose = "ask") => {
    unlockAudio();
    purpose.current = why;
    if (phase !== "idle" || !config?.listen) {
      setTyping(true);
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const rec = new MediaRecorder(stream);
      chunks.current = [];
      rec.ondataavailable = (e) => chunks.current.push(e.data);
      rec.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(chunks.current, { type: rec.mimeType });
        if (blob.size < 2000) {
          setPhase("idle");
          return;
        }
        try {
          const wav = await recordingToWav(blob);
          await (purpose.current === "report" ? report(wav) : send(wav));
        } catch {
          setNote("Could not read the recording");
          setPhase("idle");
        }
      };
      recorder.current = rec;
      rec.start();
      setPhase("listening");
    } catch {
      setTyping(true);
      setNote("Microphone not available: type the question");
    }
  };

  const stop = () => {
    if (recorder.current && recorder.current.state === "recording") recorder.current.stop();
    recorder.current = null;
  };

  const scanShelf = async (file: File) => {
    setPhase("thinking");
    setNote(words.scanning);
    try {
      const made = await staffApi.shelf(role, file);
      onTasks(made);
      setNote(made.length ? `${made.length} restock task(s)` : "No empty gaps confirmed");
    } catch (error) {
      setNote(errorText(error, "Shelf scan failed"));
    } finally {
      setPhase("idle");
    }
  };

  if (!config?.ask && !config?.shelf && !config?.near_miss_voice) return null;
  return (
    <div className={styles.dock}>
      {reply || note || typing ? (
        <div className={styles.answer} role="status" aria-live="polite">
          <button
            type="button"
            className={styles.answerClose}
            aria-label={words.cancel}
            onClick={() => {
              setReply(null);
              setNote("");
              setTyping(false);
            }}
          >
            ×
          </button>
          {reply ? (
            <>
              <div className={styles.answerHeard}>
                {words.heard}: “{reply.heard}”
              </div>
              <div>{reply.answer}</div>
            </>
          ) : null}
          {note ? <div className={styles.answerHeard}>{note}</div> : null}
          {typing ? (
            <form
              className={styles.askForm}
              onSubmit={(e) => {
                e.preventDefault();
                if (question.trim()) void send(null, question.trim());
              }}
            >
              <input
                className={styles.input}
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                placeholder={words.typeQuestion}
                aria-label={words.typeQuestion}
              />
              <button type="submit" className={styles.secondary} disabled={phase !== "idle"}>
                {words.send}
              </button>
            </form>
          ) : null}
        </div>
      ) : null}
      <div className={styles.dockInner}>
        {config?.ask ? (
          <button
            type="button"
            className={`${styles.ask} ${phase === "listening" ? styles.askOn : ""}`}
            disabled={phase === "thinking"}
            onPointerDown={() => void start()}
            onPointerUp={stop}
            onPointerLeave={stop}
            onContextMenu={(e) => e.preventDefault()}
          >
            {phase === "listening"
              ? words.listening
              : phase === "thinking"
                ? words.thinking
                : `🎙 ${words.holdToAsk}`}
          </button>
        ) : null}
        {config?.near_miss_voice ? (
          <button
            type="button"
            className={styles.dockSide}
            disabled={phase === "thinking"}
            onPointerDown={() => void start("report")}
            onPointerUp={stop}
            onPointerLeave={stop}
            onContextMenu={(e) => e.preventDefault()}
            aria-label={words.reportNearMiss}
          >
            ⚠
            <br />
            {words.reportNearMiss}
          </button>
        ) : null}
        {config?.shelf ? (
          <>
            <input
              ref={shelfInput}
              className={styles.hidden}
              type="file"
              accept="image/*"
              capture="environment"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) void scanShelf(file);
                e.target.value = "";
              }}
            />
            <button
              type="button"
              className={styles.dockSide}
              disabled={phase !== "idle"}
              onClick={() => shelfInput.current?.click()}
              aria-label={words.scanShelf}
            >
              {icons.camera}
              <br />
              {words.scanShelf}
            </button>
          </>
        ) : null}
      </div>
    </div>
  );
}
