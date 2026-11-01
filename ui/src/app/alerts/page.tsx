"use client";

import { useEffect, useState } from "react";

import { AlertCard } from "@/components/ops/AlertCard";
import { api, ApiError } from "@/lib/api";
import type { ActionRecord } from "@/lib/events";
import type { Alert } from "@/lib/ops";
import { useDemo, useDispatch, useStream } from "@/lib/stream";

const ROLE_KEY = "rulecam.alerts.role";
const NEXT_STATUS: Record<string, Alert["status"] | null> = {
  "On my way": "acknowledged",
  "False alarm": "false_alarm",
  Escalate: null,
};

function readRole(): string {
  try {
    return localStorage.getItem(ROLE_KEY) ?? "";
  } catch {
    return "";
  }
}

export default function AlertsPage() {
  const state = useStream();
  const dispatch = useDispatch();
  const demo = useDemo();
  const [role, setRole] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => setRole(readRole()), []);

  useEffect(() => {
    // A golden replay (?demo=…) shows only the alerts it recorded: no API call, no fixtures.
    if (demo !== false) return;
    api
      .alerts()
      .then((alerts) => dispatch({ type: "alerts-loaded", alerts }))
      .catch(() => setMessage("Could not reach the Rulecam API; alerts will appear live."));
  }, [demo, dispatch]);

  const saveRole = (value: string): void => {
    setRole(value);
    try {
      localStorage.setItem(ROLE_KEY, value);
    } catch {
      // private mode: the role just isn't remembered
    }
  };

  const press = async (alert: Alert, button: string): Promise<void> => {
    if (!role) {
      setMessage("Enter your role first (for example aide or shift_lead).");
      return;
    }
    setBusy(true);
    try {
      if (!demo) await api.respond(alert.alert_id, button, role);
      const status = NEXT_STATUS[button];
      if (status && alert.event_id)
        dispatch({ type: "alert-status", eventId: alert.event_id, status });
      setMessage(button === "Escalate" ? "Escalated to the next role." : `Recorded: ${button}.`);
    } catch (e) {
      setMessage(e instanceof ApiError ? `Not sent: ${e.message}` : "Not sent");
    } finally {
      setBusy(false);
    }
  };

  const timeline = (eventId: string | null): ActionRecord[] => {
    const fromEvents = state.events.find((v) => v.candidate.event_id === eventId)?.actions ?? [];
    return fromEvents.filter((a) => a.kind === "notify" || a.kind === "escalate");
  };
  const visible = state.alerts.filter((a) => !role || a.role === role || a.status !== "open");

  return (
    <section className="alerts-page" aria-label="Alerts">
      <h2>Alerts</h2>
      <label className="role-field">
        My role{" "}
        <input
          value={role}
          onChange={(e) => saveRole(e.target.value.trim())}
          placeholder="aide"
          pattern="[A-Za-z][A-Za-z0-9_-]*"
          maxLength={64}
        />
      </label>
      {message && (
        <p className="muted" role="status">
          {message}
        </p>
      )}
      {visible.length === 0 && <p className="muted">No alerts.</p>}
      {visible.map((alert) => (
        <AlertCard
          key={alert.alert_id}
          alert={alert}
          timeline={timeline(alert.event_id)}
          busy={busy}
          onPress={(button) => void press(alert, button)}
        />
      ))}
    </section>
  );
}
