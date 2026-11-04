"use client";

// How a new alert gets a person's attention: a buzz, a short tone, a system notification when
// the app is in the background, and the alert read aloud in their language. Speech plays one
// line at a time, so two alerts never talk over each other.

import { audioUrl, staffApi } from "./client";
import { SPEECH_TAGS } from "./i18n";
import type { Lang, SpeechLine, StaffItem } from "./types";

let queue: Promise<void> = Promise.resolve();
let toneContext: AudioContext | null = null;

/** Call from a tap (onboarding, a button) so later tones and speech may play. */
export function unlockAudio(): void {
  try {
    toneContext ??= new AudioContext();
    void toneContext.resume();
  } catch {
    // no Web Audio: buzz and speech still work
  }
}

export function buzz(severity: string): void {
  const pattern =
    severity === "high" || severity === "critical" ? [300, 120, 300, 120, 300] : [200, 100, 200];
  try {
    navigator.vibrate?.(pattern);
  } catch {
    // some browsers throw outside a gesture
  }
}

export function tone(): void {
  const ctx = toneContext;
  if (!ctx) return;
  const now = ctx.currentTime;
  for (const [start, freq] of [
    [0, 880],
    [0.18, 660],
  ] as const) {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(0.0001, now + start);
    gain.gain.exponentialRampToValueAtTime(0.25, now + start + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + start + 0.16);
    osc.connect(gain).connect(ctx.destination);
    osc.start(now + start);
    osc.stop(now + start + 0.17);
  }
}

export async function notifyInBackground(item: StaffItem): Promise<void> {
  if (typeof document === "undefined" || !document.hidden) return;
  if (typeof Notification === "undefined" || Notification.permission !== "granted") return;
  try {
    const registration = await navigator.serviceWorker?.ready;
    await registration?.showNotification(item.title, {
      body: item.body,
      tag: item.item_id,
      icon: "/staff-icon-192.png",
      badge: "/staff-icon-192.png",
      data: { url: `/staff?item=${encodeURIComponent(item.item_id)}` },
    });
  } catch {
    // notifications are a bonus; the feed and the buzz still show it
  }
}

function playWav(name: string): Promise<void> {
  return new Promise((resolve) => {
    const audio = new Audio(audioUrl(name));
    audio.onended = () => resolve();
    audio.onerror = () => resolve();
    audio.play().catch(() => resolve());
  });
}

function speakWithPhone(text: string, lang: Lang): Promise<void> {
  return new Promise((resolve) => {
    if (typeof speechSynthesis === "undefined") {
      resolve();
      return;
    }
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = SPEECH_TAGS[lang];
    utterance.onend = () => resolve();
    utterance.onerror = () => resolve();
    speechSynthesis.speak(utterance);
  });
}

/** Say a line: the server's WAV when it made one, else the phone's own voice. */
export function sayLine(line: Pick<SpeechLine, "text" | "audio">, lang: Lang): Promise<void> {
  queue = queue.then(() => (line.audio ? playWav(line.audio) : speakWithPhone(line.text, lang)));
  return queue;
}

/** Fetch the item's spoken line in `lang` (written by Muse Spark) and say it. */
export async function readAloud(item: StaffItem, lang: Lang, role: string): Promise<SpeechLine> {
  let line: SpeechLine;
  try {
    line = await staffApi.speech(item.item_id, lang, role);
  } catch {
    line = { lang, text: `${item.title}. ${item.body}`, audio: null, engine: null, model: null };
  }
  await sayLine(line, lang);
  return line;
}
