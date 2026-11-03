"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";

import { AskTimeline } from "@/components/ask/AskTimeline";
import { OwnerOnly } from "@/components/OwnerOnly";
import { errorText } from "@/lib/api";
import {
  askReducer,
  isRunning,
  newExchange,
  streamAsk,
  type AskEvent,
  type Exchange,
} from "@/lib/ask";
import { useAuth, useOwnerControls } from "@/lib/auth";
import { useDemo } from "@/lib/stream";

/** Seconds since `since` while `running`, ticking once a second. */
function useElapsed(running: boolean, since: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!running) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [running]);
  return Math.max(0, Math.round((now - since) / 1000));
}

/**
 * Ask: the owner types any question about what the cameras recorded. Muse Spark plans the
 * steps; each SQL query, SAM 3.1 count and Glimmer look appears as it happens, then the answer.
 * Follow-ups continue the same conversation until "New conversation".
 */
export default function AskPage() {
  const owner = useOwnerControls();
  const { phase } = useAuth();
  const demo = useDemo() === true;
  const [question, setQuestion] = useState("");
  const [exchanges, setExchanges] = useState<Exchange[]>([]);
  const [startedAt, setStartedAt] = useState(0);
  const abort = useRef<AbortController | null>(null);

  const current = exchanges[exchanges.length - 1];
  const running = current !== undefined && isRunning(current);
  const elapsed = useElapsed(running, startedAt);

  useEffect(() => () => abort.current?.abort(), []);

  const apply = (event: AskEvent): void =>
    setExchanges((all) => {
      const last = all[all.length - 1];
      return last ? [...all.slice(0, -1), askReducer(last, event)] : all;
    });

  const submit = async (e: FormEvent): Promise<void> => {
    e.preventDefault();
    const text = question.trim();
    if (!text || running) return;
    const conversationId = current?.answer?.conversation_id ?? current?.conversationId ?? null;
    setExchanges((all) => [...all, newExchange(text, conversationId)]);
    setQuestion("");
    setStartedAt(Date.now());
    const controller = new AbortController();
    abort.current = controller;
    try {
      await streamAsk(
        { question: text, conversation_id: conversationId },
        apply,
        controller.signal,
      );
    } catch (error) {
      if (controller.signal.aborted) {
        apply({ type: "error", detail: "Stopped." });
      } else {
        apply({ type: "error", detail: errorText(error, "Could not reach the Rulecam API.") });
      }
    }
  };

  const reset = (): void => {
    abort.current?.abort();
    setExchanges([]);
  };

  if (demo) {
    return (
      <section aria-label="Ask">
        <h2>Ask</h2>
        <p className="muted">Ask answers from the live cameras; the recorded demo has none.</p>
      </section>
    );
  }
  if (!owner) {
    return (
      <section aria-label="Ask">
        <h2>Ask</h2>
        <OwnerOnly checking={phase === "checking"} what="Asking about the cameras" />
      </section>
    );
  }

  const turn = current?.turns.length ?? 0;
  return (
    <section className="ask-page" aria-label="Ask">
      <h2>Ask</h2>
      <p className="muted">
        Any question about what the cameras recorded. Muse Spark plans the steps; SQL over the
        tracks, SAM 3.1 counts and Muse Glimmer looks appear as they run.
      </p>
      {exchanges.map((exchange, k) => (
        <AskTimeline key={k} exchange={exchange} />
      ))}
      {running && (
        <p className="ask-progress" role="status">
          <span className="ask-spinner" aria-hidden="true" /> Working… {elapsed} s
          {turn > 0 ? ` · turn ${turn}` : ""}{" "}
          <button type="button" onClick={() => abort.current?.abort()}>
            Stop
          </button>
        </p>
      )}
      <form className="ask-form" onSubmit={(e) => void submit(e)}>
        <textarea
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              e.currentTarget.form?.requestSubmit();
            }
          }}
          placeholder={
            exchanges.length
              ? "Ask a follow-up…"
              : "How many customers were at the checkout at 1 pm?"
          }
          rows={3}
          maxLength={2000}
          aria-label="Question"
        />
        <div className="composer-row">
          <button type="submit" className="primary" disabled={running || !question.trim()}>
            {exchanges.length ? "Ask follow-up" : "Ask"}
          </button>
          {exchanges.length > 0 && (
            <button type="button" onClick={reset} disabled={running}>
              New conversation
            </button>
          )}
        </div>
      </form>
    </section>
  );
}
