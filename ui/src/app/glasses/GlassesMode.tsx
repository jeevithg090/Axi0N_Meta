"use client";

// Glasses mode: the phone's rear camera stands in for the point-of-view camera of Meta AI glasses.
// Hold to talk asks Rulecam about what is in front of you; patrol sends a frame every few seconds
// and speaks only alerts two models verified; a safety walk collects the frames into a report
// chained as evidence. Answers play through the phone's audio route, so with Ray-Ban Meta glasses
// paired over Bluetooth they are heard in the glasses.

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";

import { OwnerOnly } from "@/components/OwnerOnly";
import { errorText } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useStream } from "@/lib/stream";
import { encodeWav, recordingToWav } from "@/lib/wav";

import {
  alertLine,
  audioUrl,
  frameUrl,
  glimmerLabel,
  MODES,
  modelName,
  progressLabel,
  scaledSize,
  linesToSay,
  LOOKING_TEXT,
  SOURCE_LABELS,
  stageLine,
  unspokenAlerts,
  verdictLabel,
  wearables,
  type Mode,
  type PovAnswer,
  type Source,
  type Walk,
  type WalkReport,
  type WearablesStatus,
} from "./glasses";
import styles from "./glasses.module.css";

const PATROL_EVERY_MS = 5000;
const KEEP_ANSWERS = 12;
const STATUS_EVERY_MS = 20_000;

type Camera = "off" | "starting" | "on" | "blocked";

/** A blob URL of a short silent WAV, played on a tap so later answers may play without one. */
function silentWavUrl(): string {
  const wav = encodeWav(new Float32Array(1600), 16_000);
  return URL.createObjectURL(new Blob([wav], { type: "audio/wav" }));
}

/** Speak with the browser's voice; `onEnd` runs when it is done (or at once without one). */
function browserSay(text: string, onEnd: () => void = () => undefined): boolean {
  if (typeof window === "undefined" || !("speechSynthesis" in window)) {
    onEnd();
    return false;
  }
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.onend = onEnd;
  utterance.onerror = onEnd;
  window.speechSynthesis.speak(utterance);
  return true;
}

async function jpegOf(
  source: CanvasImageSource,
  width: number,
  height: number,
): Promise<Blob | null> {
  const [w, h] = scaledSize(width, height);
  if (w === 0) return null;
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const context = canvas.getContext("2d");
  if (!context) return null;
  context.drawImage(source, 0, 0, w, h);
  return new Promise((resolve) => canvas.toBlob((b) => resolve(b), "image/jpeg", 0.82));
}

export function GlassesMode() {
  const auth = useAuth();
  const { alerts } = useStream();
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const busyRef = useRef(false);
  // Lines wait their turn: "Looking.", then the first answer, then the checked line.
  const queueRef = useRef<{ text: string; audio: string | null }[]>([]);
  const playingRef = useRef(false);
  const spokenAlertsRef = useRef<Set<string> | null>(null);
  const wakeRef = useRef<{ release: () => Promise<void> } | null>(null);

  const [status, setStatus] = useState<WearablesStatus | null>(null);
  const [camera, setCamera] = useState<Camera>("off");
  const [still, setStill] = useState<{ url: string; blob: Blob } | null>(null);
  const [mode, setMode] = useState<Mode>("warehouse");
  const [busy, setBusy] = useState<string | null>(null);
  const [recording, setRecording] = useState(false);
  const [patrol, setPatrol] = useState(false);
  const [patrolFrames, setPatrolFrames] = useState(0);
  const [walk, setWalk] = useState<Walk | null>(null);
  const [report, setReport] = useState<WalkReport | null>(null);
  const [answers, setAnswers] = useState<PovAnswer[]>([]);
  const [typed, setTyped] = useState("");
  const [speakSite, setSpeakSite] = useState(true);
  const [lastVoice, setLastVoice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const owner = auth.phase === "owner";
  const source: Source = still ? "upload" : "phone-camera";
  const latest = answers[0] ?? null;

  // Asking for the status also wakes Glimmer up, so a cold worker starts before the first question.
  useEffect(() => {
    if (!owner) return;
    const load = (): void => {
      wearables
        .status()
        .then(setStatus)
        .catch((e: unknown) => setError(errorText(e, "Could not reach the Rulecam API.")));
    };
    load();
    const timer = setInterval(load, STATUS_EVERY_MS);
    return () => clearInterval(timer);
  }, [owner]);

  // Stop the camera and microphone when the page goes away.
  useEffect(
    () => () => {
      streamRef.current?.getTracks().forEach((t) => t.stop());
      void wakeRef.current?.release();
    },
    [],
  );

  const playNext = useCallback((): void => {
    const next = queueRef.current.shift();
    if (!next) {
      playingRef.current = false;
      return;
    }
    playingRef.current = true;
    const player = audioRef.current;
    const fallback = (): void => {
      if (browserSay(next.text, playNext)) setLastVoice("browser voice (not a Meta model)");
    };
    if (next.audio && player) {
      player.onended = playNext;
      player.src = audioUrl(next.audio);
      setLastVoice(status?.voice ?? "server voice");
      player.play().catch(fallback);
      return;
    }
    fallback();
  }, [status]);

  /** Queue a line for the ear (`interrupt`: drop what is queued or playing, for a new question). */
  const say = useCallback(
    (text: string, audio: string | null, interrupt = false): void => {
      if (interrupt) {
        queueRef.current = [];
        audioRef.current?.pause();
        if (typeof window !== "undefined" && "speechSynthesis" in window) {
          window.speechSynthesis.cancel();
        }
        playingRef.current = false;
      }
      if (!text.trim()) return;
      queueRef.current.push({ text, audio });
      if (!playingRef.current) playNext();
    },
    [playNext],
  );

  const unlockAudio = (): void => {
    const player = audioRef.current;
    if (!player || player.dataset.unlocked) return;
    player.src = silentWavUrl();
    player.dataset.unlocked = "1";
    player.play().catch(() => undefined);
  };

  const startCamera = async (): Promise<void> => {
    unlockAudio();
    setError(null);
    setCamera("starting");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 } },
        audio: { echoCancellation: true, noiseSuppression: true },
      });
      // The microphone is muted except while the talk button is held: nothing ambient is heard.
      stream.getAudioTracks().forEach((t) => (t.enabled = false));
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play().catch(() => undefined);
      }
      setStill(null);
      setCamera("on");
      const lock = (
        navigator as Navigator & {
          wakeLock?: { request: (t: "screen") => Promise<{ release: () => Promise<void> }> };
        }
      ).wakeLock;
      if (lock) wakeRef.current = await lock.request("screen").catch(() => null);
    } catch (e) {
      setCamera("blocked");
      setError(
        e instanceof Error
          ? `Camera or microphone unavailable (${e.name}). Use HTTPS or localhost, allow access, or upload a photo.`
          : "Camera unavailable.",
      );
    }
  };

  const onPhoto = async (file: File | undefined): Promise<void> => {
    if (!file) return;
    unlockAudio();
    const bitmap = await createImageBitmap(file);
    const blob = await jpegOf(bitmap, bitmap.width, bitmap.height);
    bitmap.close();
    if (!blob) return;
    if (still) URL.revokeObjectURL(still.url);
    setStill({ url: URL.createObjectURL(blob), blob });
  };

  const grabFrame = useCallback(async (): Promise<Blob | null> => {
    if (still) return still.blob;
    const video = videoRef.current;
    if (!video || video.videoWidth === 0) return null;
    return jpegOf(video, video.videoWidth, video.videoHeight);
  }, [still]);

  const send = useCallback(
    async (opts: {
      question?: string;
      audio?: Blob | null;
      label: string;
    }): Promise<PovAnswer | null> => {
      const frame = await grabFrame();
      if (!frame) {
        setError("No frame yet: start the camera or upload a photo.");
        return null;
      }
      busyRef.current = true;
      setBusy(opts.label);
      setError(null);
      const asking = Boolean(opts.question || opts.audio);
      // Say "Looking." at once (spoken once on the server at startup), then each line as it comes.
      if (asking) say(LOOKING_TEXT, status?.phrases.looking ?? null, true);
      let firstSaid = false;
      try {
        const answer = await wearables.look(
          {
            frame,
            mode,
            source,
            question: opts.question,
            audio: opts.audio,
            walkId: walk?.walk_id ?? null,
            lang: (navigator.language || "en").split("-")[0],
          },
          (job) => {
            const done = progressLabel(job);
            if (done) setBusy(`${opts.label} ${done}`);
            if (job.first && !firstSaid) {
              firstSaid = true;
              say(job.first.text, job.first.audio);
            }
          },
        );
        setAnswers((prev) => [answer, ...prev].slice(0, KEEP_ANSWERS));
        for (const line of linesToSay(answer, firstSaid)) say(line.text, line.audio);
        // The walk may have been finished while this frame was in flight: leave it finished.
        setWalk((w) => (w && w.walk_id === answer.walk_id ? { ...w, frames: w.frames + 1 } : w));
        return answer;
      } catch (e) {
        setError(errorText(e, "The glasses assistant did not answer."));
        return null;
      } finally {
        busyRef.current = false;
        setBusy(null);
      }
    },
    [grabFrame, mode, source, walk, say, status],
  );

  // Patrol: one frame every few seconds while nothing else is in flight.
  useEffect(() => {
    if (!patrol) return;
    const timer = setInterval(() => {
      if (busyRef.current) return;
      void send({ label: "Patrol look…" }).then((a) => a && setPatrolFrames((n) => n + 1));
    }, PATROL_EVERY_MS);
    return () => clearInterval(timer);
  }, [patrol, send]);

  // Speak verified alerts from the site's fixed cameras (not the backlog that was there on load).
  useEffect(() => {
    if (spokenAlertsRef.current === null) {
      spokenAlertsRef.current = new Set(alerts.map((a) => a.alert_id));
      return;
    }
    const fresh = unspokenAlerts(alerts, spokenAlertsRef.current);
    fresh.forEach((a) => spokenAlertsRef.current?.add(a.alert_id));
    if (speakSite && fresh.length > 0) say(fresh.map(alertLine).join(" "), null);
  }, [alerts, speakSite, say]);

  const startTalking = (): void => {
    unlockAudio();
    const stream = streamRef.current;
    const track = stream?.getAudioTracks()[0];
    if (!stream || !track || busyRef.current) {
      if (!track)
        setError("No microphone: start the camera (it asks for the mic too) or type the question.");
      return;
    }
    track.enabled = true;
    chunksRef.current = [];
    const recorder = new MediaRecorder(new MediaStream([track]));
    recorder.ondataavailable = (event) => chunksRef.current.push(event.data);
    recorder.onstop = () => {
      track.enabled = false;
      const recording = new Blob(chunksRef.current, { type: recorder.mimeType });
      void recordingToWav(recording)
        .then((wav) => send({ audio: wav, label: "Listening, looking…" }))
        .catch(() => setError("Could not read the recording; try again or type the question."));
    };
    recorderRef.current = recorder;
    recorder.start();
    setRecording(true);
  };

  const stopTalking = (): void => {
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== "inactive") recorder.stop();
    recorderRef.current = null;
    setRecording(false);
  };

  const onAsk = (event: FormEvent): void => {
    event.preventDefault();
    unlockAudio();
    if (!typed.trim()) return;
    void send({ question: typed, label: "Looking…" });
    setTyped("");
  };

  const toggleWalk = async (): Promise<void> => {
    unlockAudio();
    setError(null);
    try {
      if (walk) {
        setBusy("Writing the walk report…");
        const done = await wearables.finishWalk(walk.walk_id);
        setReport(done);
        setWalk(null);
        setPatrol(false);
        say(done.summary.spoken, null);
      } else {
        setReport(null);
        setWalk(await wearables.startWalk(mode));
        setPatrol(true);
      }
    } catch (e) {
      setError(errorText(e, "The walk could not be started or finished."));
    } finally {
      setBusy(null);
    }
  };

  if (auth.phase === "checking") return <p className={styles.wrap}>Checking who you are…</p>;
  if (auth.phase === "demo")
    return (
      <p className={styles.wrap}>
        Glasses mode needs the live API; it is not part of the replay demo.
      </p>
    );
  if (!owner)
    return (
      <div className={styles.wrap}>
        <OwnerOnly what="Glasses mode" checking={false} />
      </div>
    );

  const captionAlert = latest?.alert && latest.verified;
  return (
    <div className={styles.wrap}>
      <header className={styles.head}>
        <div>
          <h1 className={styles.title}>Glasses mode</h1>
          <p className={styles.sub}>
            Your point of view as a Rulecam camera. Hold to talk; patrol speaks only verified
            alerts.
          </p>
        </div>
        <span className={styles.chipWarn} data-testid="source">
          {SOURCE_LABELS[source]}
        </span>
      </header>

      <section className={styles.view} aria-label="Point of view">
        {still ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img className={styles.still} src={still.url} alt="Uploaded point-of-view photo" />
        ) : (
          <video ref={videoRef} className={styles.video} playsInline muted />
        )}
        {camera !== "on" && !still && (
          <div className={styles.viewEmpty}>
            <button
              type="button"
              className={styles.start}
              onClick={() => void startCamera()}
              disabled={camera === "starting"}
            >
              {camera === "starting" ? "Starting…" : "Start glasses mode"}
            </button>
            <label className={styles.sub}>
              or upload a photo{" "}
              <input
                type="file"
                accept="image/jpeg,image/png"
                capture="environment"
                onChange={(e) => void onPhoto(e.target.files?.[0])}
              />
            </label>
          </div>
        )}
        <div className={styles.hud}>
          <span className={styles.chip}>{MODES.find((m) => m.mode === mode)?.label}</span>
          {recording && <span className={styles.chipAlert}>● Listening</span>}
          {patrol && <span className={styles.chipWarn}>Patrol · {patrolFrames} frames</span>}
          {walk && <span className={styles.chipWarn}>Walk · {walk.frames} frames</span>}
          {busy && <span className={styles.chip}>{busy}</span>}
          {status && (
            <span className={status.glimmer === "down" ? styles.chipWarn : styles.chip}>
              {glimmerLabel(status.glimmer)}
            </span>
          )}
        </div>
        {latest?.spoken && (
          <p className={captionAlert ? styles.captionAlert : styles.caption}>{latest.spoken}</p>
        )}
      </section>

      <div className={styles.modes} role="radiogroup" aria-label="What you are doing">
        {MODES.map((m) => (
          <button
            key={m.mode}
            type="button"
            role="radio"
            aria-checked={mode === m.mode}
            title={m.hint}
            className={mode === m.mode ? styles.modeOn : styles.mode}
            onClick={() => setMode(m.mode)}
            disabled={walk !== null}
          >
            {m.label}
          </button>
        ))}
      </div>

      <div className={styles.controls}>
        <button
          type="button"
          className={patrol ? styles.sideOn : styles.side}
          onClick={() => {
            unlockAudio();
            setPatrol((p) => !p);
          }}
          aria-pressed={patrol}
        >
          {patrol ? "Patrol on" : "Patrol off"}
        </button>
        <button
          type="button"
          className={recording ? styles.pttRec : styles.ptt}
          onPointerDown={startTalking}
          onPointerUp={stopTalking}
          onPointerLeave={() => recording && stopTalking()}
          onContextMenu={(e) => e.preventDefault()}
          disabled={camera !== "on" || (busy !== null && !recording)}
          aria-label="Hold to talk"
        >
          {recording ? "Release to ask" : "Hold to talk"}
        </button>
        <button
          type="button"
          className={walk ? styles.sideOn : styles.side}
          onClick={() => void toggleWalk()}
          disabled={busy !== null && !walk}
        >
          {walk ? "Finish walk" : "Start safety walk"}
        </button>
      </div>

      <form className={styles.ask} onSubmit={onAsk}>
        <input
          className={styles.input}
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          placeholder="Or type: is this aisle safe?"
          aria-label="Question"
        />
        <button className={styles.btn} type="submit" disabled={busy !== null || !typed.trim()}>
          Ask
        </button>
      </form>

      <div className={styles.toggles}>
        <label>
          <input
            type="checkbox"
            checked={speakSite}
            onChange={(e) => setSpeakSite(e.target.checked)}
          />
          Speak alerts from the site&apos;s cameras
        </label>
      </div>

      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}

      {latest && <AnswerCard answer={latest} voice={lastVoice} />}
      {report && <ReportCard report={report} />}

      {answers.length > 1 && (
        <section className={styles.history} aria-label="Earlier answers">
          {answers.slice(1).map((a) => (
            <div key={a.answer_id} className={styles.histItem}>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img className={styles.thumb} src={frameUrl(a.marked ?? a.frame)} alt="" />
              <span>
                <strong>{a.kind === "question" ? a.question : "Patrol"}</strong> —{" "}
                {a.spoken || a.detail || "nothing to say"}
                {a.alert && <> · {verdictLabel(a)}</>}
              </span>
            </div>
          ))}
        </section>
      )}

      <p className={styles.note}>
        {status
          ? `Models: first look ${status.look ? modelName(status.look) : "Spark"}, counting ${status.count}, answers ${modelName(status.compose)}, speech in ${status.transcribe ? "Muse Voice Transcribe" : "off"}, voice ${status.voice ?? "browser"}. `
          : ""}
        The phone camera stands in for the glasses&apos; camera. Paired Ray-Ban Meta or Oakley Meta
        glasses play the answers and alerts as ordinary Bluetooth audio, and can be the microphone.
        The glasses&apos; own camera needs Meta&apos;s Wearables Device Access Toolkit
        (apps/glasses). Nothing is heard unless the talk button is held.
      </p>
      <audio ref={audioRef} hidden onEnded={() => undefined} />
    </div>
  );
}

function AnswerCard({ answer, voice }: { answer: PovAnswer; voice: string | null }) {
  const badge = answer.alert ? (answer.verified ? styles.chipAlert : styles.chipWarn) : styles.chip;
  return (
    <section className={styles.card} aria-label="Answer" data-testid="answer">
      <div>
        <span className={badge}>{verdictLabel(answer)}</span>
      </div>
      {answer.heard !== null && (
        <p className={styles.muted}>Heard: “{answer.heard || "nothing"}”</p>
      )}
      {answer.question && answer.heard === null && (
        <p className={styles.muted}>Asked: “{answer.question}”</p>
      )}
      {answer.first && (
        <p className={styles.spoken}>
          {answer.first.text}{" "}
          <span className={styles.mono}>
            first line, {modelName(answer.first.by)}, {(answer.first.at_ms / 1000).toFixed(1)} s
          </span>
        </p>
      )}
      <p className={styles.spoken}>
        {answer.spoken || (answer.kind === "patrol" ? "Nothing to warn about." : "")}
      </p>
      {answer.detail && <p>{answer.detail}</p>}
      {answer.hazards.length > 0 && (
        <ul className={styles.list}>
          {answer.hazards.map((h, i) => (
            <li key={i}>
              <strong>{h.severity}</strong>: {h.what} ({h.where})
            </li>
          ))}
        </ul>
      )}
      {answer.gaps.length > 0 && (
        <ul className={styles.list} aria-label="Shelf gaps">
          {answer.gaps.map((g) => (
            <li key={g.number}>
              Gap {g.number}: {g.section}, about {g.facings} facings, next to {g.neighbours}
            </li>
          ))}
        </ul>
      )}
      {answer.rejected.length > 0 && (
        <p className={styles.muted}>Spark rejected SAM 3.1 labels: {answer.rejected.join("; ")}</p>
      )}
      {answer.counts.length > 0 && (
        <p className={styles.muted}>
          SAM 3.1 counted: {answer.counts.map((c) => `${c.concept} ${c.count}`).join(", ")}
        </p>
      )}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        className={styles.marked}
        src={frameUrl(answer.marked ?? answer.frame)}
        alt="The frame the models looked at"
      />
      <p className={styles.mono}>
        {(answer.total_ms / 1000).toFixed(1)} s total · {stageLine(answer.stages)}
      </p>
      <p className={styles.mono}>
        First look: {answer.looked_by || "none"} · source: {SOURCE_LABELS[answer.source]}
        {voice ? ` · voice: ${voice}` : ""}
      </p>
    </section>
  );
}

function ReportCard({ report }: { report: WalkReport }) {
  return (
    <section className={styles.card} aria-label="Safety walk report">
      <span className={styles.chipOk}>Safety walk report · {report.answers.length} frames</span>
      <p className={styles.spoken}>{report.summary.spoken}</p>
      <p>{report.summary.summary}</p>
      {report.summary.hazards.length > 0 && (
        <ul className={styles.list}>
          {report.summary.hazards.map((h, i) => (
            <li key={i}>
              <strong>{h.severity}</strong>: {h.what} ({h.where}) — {h.action}{" "}
              <span className={styles.mono}>frames {h.answer_ids.join(", ")}</span>
            </li>
          ))}
        </ul>
      )}
      {report.summary.restock.length > 0 && <p>Restock: {report.summary.restock.join("; ")}</p>}
      <p className={styles.mono}>
        Chained as evidence: {report.chain_hashes.length} records (
        {report.chain_hashes.map((h) => h.slice(0, 10)).join(", ")}) · written by{" "}
        {modelName(report.model)} in {(report.ms / 1000).toFixed(1)} s
      </p>
    </section>
  );
}
