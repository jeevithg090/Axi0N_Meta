"use client";

// A small microphone recorder for the spoken request and the spoken near-miss report.

import { useCallback, useRef, useState } from "react";

export interface Recorder {
  recording: boolean;
  error: string | null;
  start: () => Promise<void>;
  stop: () => Promise<Blob | null>;
}

export function useRecorder(): Recorder {
  const [recording, setRecording] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);

  const start = useCallback(async () => {
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const media = new MediaRecorder(stream);
      chunks.current = [];
      media.ondataavailable = (event) => {
        if (event.data.size > 0) chunks.current.push(event.data);
      };
      media.start();
      recorder.current = media;
      setRecording(true);
    } catch {
      setError("The microphone is not available in this browser.");
    }
  }, []);

  const stop = useCallback(
    () =>
      new Promise<Blob | null>((resolve) => {
        const media = recorder.current;
        if (media === null) {
          resolve(null);
          return;
        }
        media.onstop = () => {
          media.stream.getTracks().forEach((track) => track.stop());
          recorder.current = null;
          setRecording(false);
          resolve(new Blob(chunks.current, { type: media.mimeType || "audio/webm" }));
        };
        media.stop();
      }),
    [],
  );

  return { recording, error, start, stop };
}
