"use client";

import { useEffect, useRef, useState } from "react";

import styles from "./crowd.module.css";
import type { AckState, CrowdAlert } from "./crowdApi";
import { LEVEL_COLOUR, clock, crowdApi, fileUrl } from "./crowdApi";

const ROLE_KEY = "rulecam.crowd.role";

function secondsBetween(from: string | null | undefined, to: string | null | undefined): string {
  if (!from || !to) return "–";
  return `${Math.round((new Date(to).getTime() - new Date(from).getTime()) / 1000)} s`;
}

function checkText(alert: CrowdAlert): string {
  if (alert.check) {
    const verdict = alert.check.agrees ? "confirmed" : "did not see it";
    return `Muse Glimmer ${verdict} (${Math.round(alert.check.confidence * 100)}%): ${alert.check.seen}`;
  }
  return alert.check_note ?? "No frame check";
}

function AckLine({ ack }: { ack: AckState | undefined }) {
  if (!ack) return null;
  const label =
    ack.status === "acknowledged"
      ? "Acknowledged"
      : ack.status === "false_alarm"
        ? "Marked false alarm"
        : ack.escalating
          ? "Waiting for a steward to answer"
          : "Not acknowledged";
  return (
    <div className={styles.small}>
      <strong>{label}</strong>
      {ack.notified.length > 0 && (
        <>
          {" · notified "}
          {ack.notified.map((n) => `${n.role} ${clock(n.at)}`).join(" → ")}
        </>
      )}
      {ack.taps.length > 0 && (
        <> · taps: {ack.taps.map((t) => `${t.role} “${t.button}” ${clock(t.at)}`).join(", ")}</>
      )}
      {ack.arrival && (
        <div>
          Steward on camera ({ack.arrival.concept} in {ack.arrival.zone}):{" "}
          {ack.arrival.seen_at
            ? `seen at ${clock(ack.arrival.seen_at)} (${ack.arrival.people})`
            : ack.arrival.gave_up
              ? `not seen in 5 min (${ack.arrival.checks} looks)`
              : `looking… (${ack.arrival.checks} looks)`}
        </div>
      )}
    </div>
  );
}

/** Alerts newest first: the check, latency, who was told, the taps and the announcements. */
export function AlertsPanel({
  alerts,
  acks,
}: {
  alerts: CrowdAlert[];
  acks: Record<string, AckState>;
}) {
  const [role, setRole] = useState("stewards");
  const [autoplay, setAutoplay] = useState(false);
  const [message, setMessage] = useState("");
  const played = useRef<Set<string>>(new Set());
  const first = useRef(true);

  useEffect(() => {
    try {
      setRole(localStorage.getItem(ROLE_KEY) ?? "stewards");
    } catch {
      // private mode
    }
  }, []);

  useEffect(() => {
    const newest = alerts.find((a) => a.sent && a.audio.some((x) => x.audio));
    if (!newest) return;
    if (first.current) {
      // announcements already on screen when the page opens are not replayed
      first.current = false;
      alerts.forEach((a) => played.current.add(a.alert_id));
      return;
    }
    if (played.current.has(newest.alert_id)) return;
    played.current.add(newest.alert_id);
    if (!autoplay) return;
    // an announcement whose meaning did not survive the round trip is never played by itself
    const queue = newest.audio
      .filter((x) => x.audio && x.meaning !== "different")
      .map((x) => fileUrl(x.audio ?? ""));
    const playNext = (): void => {
      const src = queue.shift();
      if (!src) return;
      const audio = new Audio(src);
      audio.onended = playNext;
      void audio.play().catch(() => setMessage("The browser blocked autoplay; press play."));
    };
    playNext();
  }, [alerts, autoplay]);

  const tap = async (alert: CrowdAlert, button: string): Promise<void> => {
    try {
      localStorage.setItem(ROLE_KEY, role);
    } catch {
      // not remembered
    }
    try {
      await crowdApi.ack(alert.alert_id, role, button);
      setMessage(`${button} recorded for ${role}.`);
    } catch (exc) {
      setMessage(exc instanceof Error ? exc.message : "Could not record the tap.");
    }
  };

  return (
    <div className={styles.card}>
      <div className={styles.row}>
        <h2>Alerts and announcements</h2>
        <label className={styles.small}>
          <input
            type="checkbox"
            checked={autoplay}
            onChange={(e) => setAutoplay(e.target.checked)}
          />{" "}
          play new announcements
        </label>
        <label className={styles.small}>
          my role{" "}
          <input
            className={styles.input}
            value={role}
            onChange={(e) => setRole(e.target.value)}
            size={14}
          />
        </label>
      </div>
      {message && <span className={styles.small}>{message}</span>}
      {alerts.length === 0 && <p className={styles.muted}>No alerts yet.</p>}
      {alerts.map((alert) => (
        <div
          key={alert.alert_id}
          className={styles.alert}
          style={{ borderColor: LEVEL_COLOUR[alert.level] }}
        >
          <div className={styles.row}>
            <span className={styles.badge} style={{ background: LEVEL_COLOUR[alert.level] }}>
              {alert.level}
            </span>
            <strong>{alert.sent ? "Sent" : "Held"}</strong>
            <span className={styles.small}>
              reading {clock(alert.at)} · Spark +{secondsBetween(alert.at, alert.assessed_at)} ·
              staff told +{secondsBetween(alert.at, alert.notified_at ?? alert.sent_at)}
            </span>
          </div>
          <span>{alert.assessment.why}</span>
          <span className={styles.small}>{alert.held_reason ?? checkText(alert)}</span>
          {alert.sent && <AckLine ack={acks[alert.alert_id]} />}
          {alert.sent && (
            <div className={styles.row}>
              {["On my way", "False alarm", "Escalate"].map((button) => (
                <button
                  key={button}
                  type="button"
                  className={styles.button}
                  onClick={() => void tap(alert, button)}
                >
                  {button}
                </button>
              ))}
            </div>
          )}
          {alert.audio.map((a, k) => (
            <div key={k} className={styles.row}>
              <span className={styles.small}>
                <strong>[{a.lang}]</strong> {a.text}
                {a.meaning && (
                  <span
                    style={{ color: a.meaning === "different" ? "#c62828" : undefined }}
                    title={a.heard ? `Heard back: ${a.heard} (WER ${a.wer})` : undefined}
                  >
                    {" "}
                    · heard back:{" "}
                    {a.meaning === "same"
                      ? "same meaning (Muse Spark)"
                      : a.meaning === "different"
                        ? `DIFFERENT: ${a.meaning_note ?? ""}`
                        : `not checked (${a.meaning_note ?? ""})`}
                  </span>
                )}
              </span>
              {a.audio && <audio controls preload="none" src={fileUrl(a.audio)} />}
            </div>
          ))}
          {alert.chain_hash && (
            <span className={styles.small}>evidence chain: {alert.chain_hash.slice(0, 16)}…</span>
          )}
        </div>
      ))}
    </div>
  );
}
