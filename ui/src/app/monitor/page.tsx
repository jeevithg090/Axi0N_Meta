"use client";

import { LatencyPanel } from "@/components/LatencyPanel";
import { RuleCard } from "@/components/RuleCard";
import { VerifierPanel } from "@/components/VerifierPanel";
import { ruleCards } from "@/lib/store";
import { useStream } from "@/lib/stream";

export default function MonitorPage() {
  const state = useStream();
  const cards = ruleCards(state);
  return (
    <div className="monitor">
      <section aria-label="Rules">
        <h2>Rules</h2>
        {cards.length === 0 && <p className="muted">No alerts yet. Rules are running.</p>}
        <div className="cards">
          {cards.map((card) => (
            <RuleCard key={`${card.ruleId}@${card.cameraId}`} card={card} />
          ))}
        </div>
        <LatencyPanel relay={state.relay} />
      </section>
      <section aria-label="Verifier">
        <h2>Verifier</h2>
        {state.events.map((view) => (
          <VerifierPanel key={view.candidate.event_id} view={view} />
        ))}
      </section>
    </div>
  );
}
