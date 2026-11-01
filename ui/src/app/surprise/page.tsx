"use client";

import { useState } from "react";

import { SurpriseView } from "@/components/ops/SurpriseView";
import { api, ApiError } from "@/lib/api";
import { isDemo, useStream } from "@/lib/stream";

/** Only http(s) URLs are shown as images; a server-side file path is never fetched blindly. */
function heatmapUrl(path: string): string | null {
  return /^https?:\/\//.test(path) ? path : null;
}

export default function SurprisePage() {
  const state = useStream();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  const suggest = async (id: string): Promise<void> => {
    setBusy(true);
    try {
      if (isDemo()) {
        setMessage("Demo: the drafted rule is backtested, then waits for your approval.");
      } else {
        const { rule_id } = await api.suggestRule(id);
        setMessage(`Drafted ${rule_id}: open the plan to review and approve it.`);
      }
    } catch (e) {
      setMessage(e instanceof ApiError ? `Not drafted: ${e.message}` : "Not drafted");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section aria-label="Surprise Lens">
      <h2>Surprise Lens</h2>
      {message && (
        <p className="muted" role="status">
          {message}
        </p>
      )}
      {state.surprises.length === 0 && (
        <p className="muted">Nothing unusual. The world model is calibrated on normal activity.</p>
      )}
      {state.surprises.map((event) => (
        <SurpriseView
          key={event.event_id}
          event={event}
          heatmapUrl={heatmapUrl(event.heatmap_path)}
          busy={busy}
          onSuggest={() => void suggest(event.event_id)}
        />
      ))}
    </section>
  );
}
