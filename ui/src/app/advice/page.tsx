"use client";

import { useEffect, useState } from "react";

import { AdviceCardView } from "@/components/advice/AdviceCardView";
import type { AdviceCard, SealStatus } from "@/lib/advice";
import { api, ApiError } from "@/lib/api";
import { useDemo } from "@/lib/stream";

export default function AdvicePage() {
  const demo = useDemo();
  const [cards, setCards] = useState<AdviceCard[]>([]);
  const [seals, setSeals] = useState<SealStatus[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  const load = async (): Promise<void> => {
    try {
      const [loaded, sealed] = await Promise.all([api.advice(), api.seals()]);
      setCards(loaded);
      setSeals(sealed);
    } catch (e) {
      setMessage(e instanceof ApiError ? e.message : "Could not reach the Rulecam API");
    }
  };

  useEffect(() => {
    // A golden replay carries no advice cards; the page only reads the live API.
    if (demo === false) void load();
  }, [demo]);

  const accept = async (id: string): Promise<void> => {
    setBusy(true);
    try {
      const { preregistered_seq } = await api.acceptAdvice(id);
      setMessage(`Pre-registered at event ${preregistered_seq}.`);
      await load();
    } catch (e) {
      setMessage(e instanceof ApiError ? `Not accepted: ${e.message}` : "Not accepted");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="advice-page" aria-label="Site Advisor">
      <h2>Site Advisor</h2>
      <p className="muted">
        Every number is clickable and independently recomputed by Glimmer; accepted advice becomes a
        pre-registered check that reports back. Stations, never people.
      </p>
      {message && (
        <p className="muted" role="status">
          {message}
        </p>
      )}
      {demo !== false && (
        <p className="muted">Advice cards come from the live API (not the golden run).</p>
      )}
      {demo === false && cards.length === 0 && !message && (
        <p className="muted">Nothing worth your time today.</p>
      )}
      {cards.map((card) => (
        <AdviceCardView
          key={card.recommendation.id}
          card={card}
          seals={seals}
          busy={busy}
          onAccept={() => void accept(card.recommendation.id)}
        />
      ))}
    </section>
  );
}
