"use client";

import { LatencyPanel } from "@/components/LatencyPanel";
import { RuleCard } from "@/components/RuleCard";
import { VerifierPanel } from "@/components/VerifierPanel";
import { blindCameras, noAlertsText, ruleCards } from "@/lib/store";
import { useStream } from "@/lib/stream";

export default function MonitorPage() {
  const state = useStream();
  const cards = ruleCards(state);
  const blind = blindCameras(state);
  return (
    <div className="monitor">
      <section aria-label="Rules">
        <h2>Rules</h2>
        {blind.map((cameraId) => (
          <p key={cameraId} className="blind-note" data-testid="blind-camera">
            {cameraId} is BLIND: none of its rules is being checked.
          </p>
        ))}
        {cards.length === 0 && <p className="muted">{noAlertsText(state)}</p>}
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
