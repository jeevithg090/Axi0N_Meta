"use client";

import type { ActionRecord } from "@/lib/events";
import type { Alert } from "@/lib/ops";

const STATUS_TEXT: Record<Alert["status"], string> = {
  open: "Open",
  acknowledged: "Acknowledged",
  false_alarm: "False alarm",
};

const time = (iso: string): string => iso.slice(11, 19);

/** One alert on the phone: large buttons, its status in words, and the escalation so far. */
export function AlertCard({
  alert,
  timeline,
  busy,
  onPress,
}: {
  alert: Alert;
  timeline: ActionRecord[];
  busy: boolean;
  onPress: (button: string) => void;
}) {
  const open = alert.status === "open";
  return (
    <article
      className={`alert alert-${alert.status}`}
      data-testid="alert-card"
      data-status={alert.status}
    >
      <header>
        <h3>{alert.title}</h3>
        <span className="pill">{STATUS_TEXT[alert.status]}</span>
      </header>
      <p>{alert.body}</p>
      <p className="muted">
        For: {alert.role} · {time(alert.ts)} UTC
      </p>
      {timeline.length > 0 && (
        <ol className="timeline" aria-label="Escalation">
          {timeline.map((step, i) => (
            <li key={`${step.kind}-${i}`}>
              {time(step.ts)} {step.kind === "escalate" ? "escalated to" : "notified"}{" "}
              {step.role ?? "—"}
              {step.detail ? <span className="muted"> · {step.detail}</span> : null}
            </li>
          ))}
        </ol>
      )}
      {open && (
        <div className="alert-buttons">
          {alert.buttons.map((button) => (
            <button
              key={button}
              type="button"
              className={button === "On my way" ? "primary big" : "big"}
              disabled={busy}
              onClick={() => onPress(button)}
            >
              {button}
            </button>
          ))}
        </div>
      )}
    </article>
  );
}
