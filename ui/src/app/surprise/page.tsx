"use client";

import { useState } from "react";

import { SurpriseView } from "@/components/ops/SurpriseView";
import { useDemo, useStream } from "@/lib/stream";

/** Only http(s) URLs are shown as images; a server-side file path is never fetched blindly. */
function heatmapUrl(path: string): string | null {
  return /^https?:\/\//.test(path) ? path : null;
}

export default function SurprisePage() {
  const state = useStream();
  const demo = useDemo();
  const [message, setMessage] = useState("");
  // No backend route drafts a rule from a surprise yet, so only the demo offers it.
  const suggest = demo
    ? () => setMessage("Demo: the drafted rule is backtested, then waits for your approval.")
    : null;

  return (
    <section aria-label="Surprise Lens">
      <h2>Surprise Lens</h2>
      {message && (
        <p className="muted" role="status">
          {message}
        </p>
      )}
      <p className="muted" data-testid="surprise-status">
        Planned: Surprise Lens needs the V-JEPA 2 world model, which is not running yet.
        {demo ? " The surprises below come from the recorded demo." : " Nothing is scored here."}
      </p>
      {state.surprises.map((event) => (
        <SurpriseView
          key={event.event_id}
          event={event}
          heatmapUrl={heatmapUrl(event.heatmap_path)}
          onSuggest={suggest}
        />
      ))}
    </section>
  );
}
