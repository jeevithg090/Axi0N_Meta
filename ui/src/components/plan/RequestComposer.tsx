"use client";

import { useRef, useState, type FormEvent } from "react";

import { api, ApiError, type RequestItemInput } from "@/lib/api";
import { recordingToWav } from "@/lib/wav";

type Recorder = { recorder: MediaRecorder; chunks: Blob[] };

/** The request kind a file's type maps to (demo mode makes no upload call to ask the server). */
function kindOf(type: string): RequestItemInput["kind"] {
  if (type === "application/pdf") return "pdf";
  if (type.startsWith("image/")) return "photo";
  if (type.startsWith("video/")) return "video";
  return "voice";
}

function describe(item: RequestItemInput): string {
  if (item.kind === "text") return `“${item.text ?? ""}”`;
  return `${item.kind}: ${item.file ?? ""}`;
}

/**
 * The owner's request in any mix of formats: typed text, a photo, PDF or walk-through video,
 * and a voice note recorded in the browser. Files are uploaded first; then the whole request
 * goes to the Site Planner, whose progress appears in the step list.
 */
export function RequestComposer({
  demo,
  onSubmitted,
}: {
  demo: boolean;
  onSubmitted?: (planId: string) => void;
}) {
  const [text, setText] = useState("");
  const [items, setItems] = useState<RequestItemInput[]>([]);
  const [status, setStatus] = useState<string>("");
  const [recording, setRecording] = useState(false);
  const recorderRef = useRef<Recorder | null>(null);

  const addUpload = async (blob: Blob, name: string): Promise<void> => {
    if (demo) {
      setItems((prev) => [...prev, { kind: kindOf(blob.type), file: `uploads/demo-${name}` }]);
      return;
    }
    setStatus(`Uploading ${name}…`);
    try {
      const result = await api.upload(blob, name);
      setItems((prev) => [...prev, { kind: result.kind, file: result.file }]);
      setStatus("");
    } catch (error) {
      setStatus(error instanceof ApiError ? `Upload failed: ${error.message}` : "Upload failed");
    }
  };

  const toggleRecording = async (): Promise<void> => {
    if (recorderRef.current) {
      recorderRef.current.recorder.stop();
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      const state: Recorder = { recorder, chunks: [] };
      recorder.ondataavailable = (event) => state.chunks.push(event.data);
      recorder.onstop = () => {
        stream.getTracks().forEach((track) => track.stop());
        recorderRef.current = null;
        setRecording(false);
        const recording = new Blob(state.chunks, { type: recorder.mimeType || "audio/webm" });
        // Voice Transcribe takes WAV only: re-encode the browser's WebM/MP4 recording first.
        recordingToWav(recording)
          .then((wav) => addUpload(wav, "voice-note.wav"))
          .catch(() => setStatus("Could not convert the recording; type the request instead."));
      };
      recorderRef.current = state;
      recorder.start();
      setRecording(true);
    } catch {
      setStatus("Microphone not available: allow it in the browser, or type the request.");
    }
  };

  const submit = async (event: FormEvent): Promise<void> => {
    event.preventDefault();
    const all = text.trim() ? [{ kind: "text" as const, text: text.trim() }, ...items] : items;
    if (all.length === 0) return;
    if (demo) {
      setStatus("Demo: the recorded plan below came from this kind of request.");
      return;
    }
    try {
      const { plan_id } = await api.requestPlan(all);
      setStatus(`Planning ${plan_id}…`);
      setText("");
      setItems([]);
      onSubmitted?.(plan_id);
    } catch (error) {
      setStatus(error instanceof ApiError ? error.message : "Request failed");
    }
  };

  return (
    <form className="composer" onSubmit={submit} aria-label="Describe what you want watched">
      <label htmlFor="request">What should the cameras watch?</label>
      <textarea
        id="request"
        rows={3}
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="e.g. quiero pasar la próxima inspección"
      />
      <div className="composer-row">
        <button type="button" onClick={toggleRecording} aria-pressed={recording}>
          {recording ? "■ Stop recording" : "● Record voice"}
        </button>
        <label className="file-button">
          Attach photo, PDF or video
          <input
            type="file"
            accept="image/jpeg,image/png,image/webp,application/pdf,video/mp4"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) void addUpload(file, file.name);
              e.target.value = "";
            }}
          />
        </label>
        <button type="submit" className="primary">
          Plan it
        </button>
      </div>
      {items.length > 0 && (
        <ul className="attached" data-testid="attached-items">
          {items.map((item, i) => (
            <li key={`${item.file ?? item.text}-${i}`}>{describe(item)}</li>
          ))}
        </ul>
      )}
      {status && (
        <p className="muted" role="status">
          {status}
        </p>
      )}
    </form>
  );
}
