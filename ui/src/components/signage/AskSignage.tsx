"use client";

// `/ask`: the owner's free-form questions, answered live by the API (`POST /ask/stream`). There is
// no local or scripted fallback: when the API cannot be reached, the page says so.

import React, { useEffect, useRef, useState, type FormEvent } from "react";
import { AskTimeline } from "@/components/ask/AskTimeline";
import { ApiError, errorText } from "@/lib/api";
import {
  askReducer,
  isRunning,
  newExchange,
  streamAsk,
  type AskEvent,
  type Exchange,
} from "@/lib/ask";

/** One-click questions: only the question text is preset; the answer always comes live. */
const PRESET_QUESTIONS = [
  {
    label: "Checkout queue",
    q: "When was the checkout queue longest, how many tills were open, and do we need another till?",
  },
  {
    label: "Shelves and spills",
    q: "Were any shelf gaps or floor hazards found today, and how fast were they fixed?",
  },
  {
    label: "Dwell time",
    q: "On average, how long did a customer spend at the checkout?",
  },
  {
    label: "Forklift near-misses",
    q: "How many times did a person come within 2 metres of a moving forklift today?",
  },
];

/** Plain words for why a question could not be answered. */
export function unreachableText(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 503) return `The API is up but cannot answer: ${error.message}`;
    if (error.status === 401 || error.status === 403)
      return "Asking is for the site owner: log in first.";
    return `The API answered ${error.status}: ${error.message}`;
  }
  return errorText(
    error,
    "The Rulecam API is unreachable, so nothing was answered. Check the API and try again.",
  );
}

export function AskSignage() {
  const [question, setQuestion] = useState<string>("");
  const [exchanges, setExchanges] = useState<Exchange[]>([]);
  const [startedAt, setStartedAt] = useState<number>(0);
  const [elapsed, setElapsed] = useState<number>(0);
  const abort = useRef<AbortController | null>(null);

  const current = exchanges[exchanges.length - 1];
  const running = current !== undefined && isRunning(current);

  useEffect(() => {
    if (!running) return;
    setElapsed(0);
    const timer = window.setInterval(() => {
      setElapsed(Math.max(0, Math.round((Date.now() - startedAt) / 1000)));
    }, 500);
    return () => window.clearInterval(timer);
  }, [running, startedAt]);

  const apply = (event: AskEvent): void =>
    setExchanges((all) => {
      const last = all[all.length - 1];
      return last ? [...all.slice(0, -1), askReducer(last, event)] : all;
    });

  const executeQuestion = async (qText: string): Promise<void> => {
    const text = qText.trim();
    if (!text || running) return;
    const conversationId = current?.answer?.conversation_id ?? null;
    setExchanges((all) => [...all, newExchange(text, conversationId)]);
    setQuestion("");
    setStartedAt(Date.now());
    const controller = new AbortController();
    abort.current = controller;
    let receivedAny = false;
    try {
      await streamAsk(
        { question: text, conversation_id: conversationId },
        (ev) => {
          receivedAny = true;
          apply(ev);
        },
        controller.signal,
      );
      if (!receivedAny) apply({ type: "error", detail: unreachableText(null) });
    } catch (error) {
      apply({
        type: "error",
        detail: controller.signal.aborted ? "Stopped." : unreachableText(error),
      });
    }
  };

  const submit = async (e: FormEvent): Promise<void> => {
    e.preventDefault();
    await executeQuestion(question);
  };

  return (
    <div style={{ paddingBottom: "var(--s6)" }}>
      <section className="wrap pagehead">
        <div className="grid">
          <div className="s8">
            <div className="ctx">
              Ask (`POST /ask`) &nbsp;·&nbsp;{" "}
              <b>Muse Spark 1.3 + SQL on SAM 3.1 tracks + Tier 1 model looks + Spark video</b>
            </div>
            <h1 className="h1">
              Ask anything about what your <span className="hi">cameras saw</span>.
            </h1>
            <p className="lead" style={{ marginTop: "var(--s2)", maxWidth: "66ch" }}>
              Each question gets a fresh read-only DuckDB snapshot of the track store
              (`track_points`, `track_spans`, `captions`, identities, the supermarket checks and
              staff tasks). Muse Spark chooses its own tools step by step (SQL, SAM 3.1 counts,
              Tier 1 model looks, watching the recorded clip) and every answer gets a SHA-256 run
              receipt. Answers stream live from the API; nothing on this page is scripted.
            </p>
          </div>
        </div>
      </section>

      <div className="tape" />

      <section className="wrap sec sec-last" style={{ paddingTop: "var(--s4)" }}>
        <div style={{ marginBottom: "var(--s3)" }}>
          <div className="dm small" style={{ fontWeight: 700, marginBottom: "8px" }}>
            EXAMPLE QUESTIONS (ANSWERED LIVE):
          </div>
          <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
            {PRESET_QUESTIONS.map((p) => (
              <button
                key={p.label}
                type="button"
                className="btn o"
                disabled={running}
                onClick={() => void executeQuestion(p.q)}
                style={{ padding: "8px 14px", fontSize: "13px" }}
              >
                ▶ {p.label}
              </button>
            ))}
            {exchanges.length > 0 && (
              <button
                type="button"
                className="tab"
                disabled={running}
                onClick={() => setExchanges([])}
              >
                ↺ New conversation
              </button>
            )}
          </div>
        </div>

        <form
          onSubmit={(e) => void submit(e)}
          style={{
            border: "1px solid var(--color-rule)",
            borderRadius: "var(--radius-card)",
            background: "var(--card)",
            padding: "var(--s3)",
            marginBottom: "var(--s4)",
          }}
        >
          <label
            htmlFor="ask-input"
            className="dm small"
            style={{ fontWeight: 700, display: "block", marginBottom: "8px" }}
          >
            ASK ABOUT COUNTS, QUEUES, DWELL TIMES, SHELVES, HAZARDS OR NEAR-MISSES:
          </label>
          <textarea
            id="ask-input"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                e.currentTarget.form?.requestSubmit();
              }
            }}
            placeholder="e.g. How many customers were waiting at the checkout at 11 am?"
            rows={2}
            maxLength={2000}
            aria-label="Question"
            style={{
              width: "100%",
              padding: "12px",
              border: "1px solid var(--color-rule)",
              borderRadius: "var(--radius-card)",
              background: "var(--paper)",
              fontFamily: "var(--font-dm-mono, monospace)",
              fontSize: "14px",
              marginBottom: "12px",
            }}
          />
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span className="mono" style={{ fontSize: "12px" }}>
              Read-only `SELECT` / `WITH` only · results are never cut silently
            </span>
            <div style={{ display: "flex", gap: "8px" }}>
              {running && (
                <button type="button" className="tab" onClick={() => abort.current?.abort()}>
                  Stop
                </button>
              )}
              <button type="submit" className="btn" disabled={running || !question.trim()}>
                {running ? `Answering (${elapsed}s)…` : "Ask →"}
              </button>
            </div>
          </div>
        </form>

        {exchanges.length === 0 && (
          <p className="lead" style={{ maxWidth: "66ch" }}>
            No questions yet. Ask one above; the answer and every step the models take appear here
            as they happen.
          </p>
        )}
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--s4)" }}>
          {exchanges.map((exchange, k) => (
            <div
              key={k}
              style={{
                border: "1px solid var(--color-rule)",
                borderRadius: "var(--radius-card)",
                background: "var(--paper-2)",
                padding: "var(--s3)",
              }}
            >
              <AskTimeline exchange={exchange} />
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
