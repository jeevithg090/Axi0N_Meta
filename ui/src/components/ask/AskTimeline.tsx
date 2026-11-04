"use client";

import { Fragment, useState } from "react";

import {
  frameUrl,
  stepSummary,
  toolLabel,
  type ActionView,
  type AskAnswer,
  type AskReview,
  type AskStep,
  type Exchange,
  type TurnView,
} from "@/lib/ask";

const ROWS_SHOWN = 50;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown): string {
  if (value === null || value === undefined) return "";
  return typeof value === "string" ? value : JSON.stringify(value);
}

/** A SQL result: its columns and first rows, with the API's note when it was capped. */
function TableView({ result }: { result: Record<string, unknown> }) {
  const columns = Array.isArray(result.columns) ? result.columns.map(text) : [];
  const rows = Array.isArray(result.rows) ? (result.rows as unknown[][]) : [];
  return (
    <div className="ask-table-wrap">
      <table className="ask-table">
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c}>{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, ROWS_SHOWN).map((row, r) => (
            <tr key={r}>
              {row.map((cell, c) => (
                <td key={c}>{text(cell)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > ROWS_SHOWN && (
        <p className="muted">
          First {ROWS_SHOWN} of the {rows.length} rows Spark received.
        </p>
      )}
      {typeof result.note === "string" && <p className="muted">{result.note}</p>}
    </div>
  );
}

type Box = [number, number, number, number, number?];

/** A frame thumbnail with SAM's boxes drawn over it (in the image's own pixels). */
function Thumb({ name, boxes, label }: { name: string; boxes: Box[]; label: string }) {
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);
  return (
    <figure className="ask-thumb">
      {/* eslint-disable-next-line @next/next/no-img-element -- an owner-only API image */}
      <img
        src={frameUrl(name)}
        alt={label}
        loading="lazy"
        onLoad={(e) =>
          setSize({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })
        }
      />
      {size && boxes.length > 0 && (
        <svg viewBox={`0 0 ${size.w} ${size.h}`} aria-hidden="true">
          {boxes.map(([x1, y1, x2, y2], k) => (
            <rect key={k} x={x1} y={y1} width={x2 - x1} height={y2 - y1} />
          ))}
        </svg>
      )}
      <figcaption>{label}</figcaption>
    </figure>
  );
}

function FrameStrip({ step }: { step: AskStep }) {
  const result = isRecord(step.result) ? step.result : {};
  const counts = Array.isArray(result.frames) ? result.frames : [];
  return (
    <div className="ask-thumbs">
      {step.frames.map((name, k) => {
        const count = counts[k];
        const boxes = isRecord(count) && Array.isArray(count.boxes) ? (count.boxes as Box[]) : [];
        const at = isRecord(count) ? text(count.at) : (step.action.times?.[k] ?? "");
        const label = isRecord(count) ? `${text(count.count)} · ${at}` : at;
        return <Thumb key={name} name={name} boxes={boxes} label={label} />;
      })}
    </div>
  );
}

/** A step's result, shown the way each tool returns it. */
function ResultView({ step }: { step: AskStep }) {
  const result = step.result;
  if (!isRecord(result)) return <pre className="ask-json">{text(result)}</pre>;
  if (typeof result.error === "string") return <p className="ask-error">{result.error}</p>;
  if (Array.isArray(result.results)) {
    return (
      <>
        {result.results.filter(isRecord).map((part, k) => (
          <div key={k}>
            <pre className="ask-code">{text(part.sql)}</pre>
            {typeof part.error === "string" ? (
              <p className="ask-error">{part.error}</p>
            ) : (
              <TableView result={part} />
            )}
          </div>
        ))}
      </>
    );
  }
  if (Array.isArray(result.rows)) return <TableView result={result} />;
  const notes = Array.isArray(result.notes) ? result.notes.map(text) : [];
  const moments = Array.isArray(result.moments) ? result.moments.filter(isRecord) : [];
  return (
    <>
      {typeof result.fallback === "string" && <p className="muted">{result.fallback}</p>}
      {typeof result.answer === "string" && (
        <p>
          {result.answer}
          {typeof result.count === "number" && <strong> · count {result.count}</strong>}
        </p>
      )}
      {moments.length > 0 && (
        <ul className="ask-moments" data-testid="ask-moments">
          {moments.map((m, k) => (
            <li key={k}>
              <span className="muted">{text(m.at)}</span> {text(m.what)}
            </li>
          ))}
        </ul>
      )}
      {step.frames.length > 0 && <FrameStrip step={step} />}
      {notes.map((note) => (
        <p key={note} className="muted">
          {note}
        </p>
      ))}
      <details>
        <summary className="muted">Raw result</summary>
        <pre className="ask-json">{JSON.stringify(result, null, 2)}</pre>
      </details>
    </>
  );
}

function ActionRow({ view }: { view: ActionView }) {
  const { action, step } = view;
  if (action.action === "answer") return null;
  const what = action.sql ?? action.question ?? action.concept ?? "";
  return (
    <li className={`ask-action ask-action-${step ? "done" : "running"}`} data-testid="ask-action">
      <details>
        <summary>
          <strong>{toolLabel(action.action)}</strong>
          {action.camera_id ? <span className="muted"> · {action.camera_id}</span> : null}
          <span className="muted"> · {action.thought}</span>
          <span className="ask-meta">
            {step
              ? `${step.seconds.toFixed(1)} s${step.model ? ` · ${step.model}` : ""}`
              : "working…"}
          </span>
          {step && <span className="ask-summary">{stepSummary(step)}</span>}
        </summary>
        {action.action === "sql" ? (
          <pre className="ask-code">{action.sql}</pre>
        ) : (
          <p className="muted">
            {what}
            {action.times?.length
              ? action.action === "watch"
                ? ` · from ${action.times.join(" to ")}`
                : ` · at ${action.times.join(", ")}`
              : ""}
          </p>
        )}
        {step && <ResultView step={step} />}
      </details>
    </li>
  );
}

function TurnRow({ turn }: { turn: TurnView }) {
  const tools = turn.actions.filter((a) => a.action.action !== "answer");
  return (
    <li className={`ask-turn ask-turn-${turn.status}`} data-testid="ask-turn">
      <div className="ask-turn-head">
        <strong>Turn {turn.turn + 1}</strong>
        <span className="muted">
          {turn.status === "thinking"
            ? " · Muse Spark is choosing what to check…"
            : ` · Muse Spark ${turn.seconds?.toFixed(1) ?? "?"} s · ${
                tools.length ? `${tools.length} at once` : "answered"
              }`}
        </span>
      </div>
      {turn.reasoning && (
        <p className="ask-reasoning muted" data-testid="ask-reasoning">
          <em>Spark’s reasoning:</em> {turn.reasoning}
        </p>
      )}
      {tools.length > 0 && (
        <ul className="ask-actions">
          {turn.actions.map((view, k) => (
            <ActionRow key={k} view={view} />
          ))}
        </ul>
      )}
    </li>
  );
}

function AnswerView({ answer }: { answer: AskAnswer }) {
  return (
    <div className="ask-answer" data-testid="ask-answer">
      <p className="ask-answer-text">{answer.answer}</p>
      <p className="muted">
        {answer.seconds.toFixed(1)} s · {answer.turns.length} turns ·{" "}
        {answer.steps.filter((s) => s.action.action !== "answer").length} steps · models:{" "}
        {answer.models.join(", ")}
        {answer.receipt ? (
          <>
            {" "}
            · receipt <code title={answer.receipt}>{answer.receipt.slice(0, 12)}</code>
          </>
        ) : null}
      </p>
    </div>
  );
}

const REVIEW_LABEL: Record<AskReview["verdict"], string> = {
  accept: "accepted the answer",
  revise: "sent the answer back to Muse Spark",
  error: "could not review (the answer stands)",
};

/** Muse Spark Open's check of a draft: its verdict, problems and advice, and the draft itself. */
function ReviewRow({ review }: { review: AskReview }) {
  return (
    <li className={`ask-review ask-review-${review.verdict}`} data-testid="ask-review">
      <div className="ask-turn-head">
        <strong>Review</strong>
        <span className="muted">
          {" "}
          · {review.model || "second model"} {REVIEW_LABEL[review.verdict]} ·{" "}
          {review.seconds.toFixed(1)} s
        </span>
      </div>
      {review.problems.length > 0 && (
        <ul className="ask-review-problems">
          {review.problems.map((p, k) => (
            <li key={k}>{p}</li>
          ))}
        </ul>
      )}
      {review.advice && review.verdict === "revise" && <p className="muted">{review.advice}</p>}
      {review.detail && <p className="muted">{review.detail}</p>}
      {review.verdict === "revise" && review.draft && (
        <details>
          <summary>Draft it sent back</summary>
          <p className="ask-answer-text">{review.draft}</p>
        </details>
      )}
    </li>
  );
}

/** One question and its live timeline: Spark's turns, each turn's tools, reviews, the answer. */
export function AskTimeline({ exchange }: { exchange: Exchange }) {
  return (
    <article className="ask-exchange" data-testid="ask-exchange">
      <h3 className="ask-question">{exchange.question}</h3>
      <ol className="ask-turns">
        {exchange.turns.map((turn) => (
          <Fragment key={turn.turn}>
            <TurnRow turn={turn} />
            {exchange.reviews
              .filter((r) => r.turn === turn.turn)
              .map((review, k) => (
                <ReviewRow key={`review-${k}`} review={review} />
              ))}
            {exchange.reviewing === turn.turn && (
              <li className="ask-review ask-review-running" data-testid="ask-reviewing">
                <span className="muted">
                  Muse Spark Open is checking the answer against the steps…
                </span>
              </li>
            )}
          </Fragment>
        ))}
      </ol>
      {exchange.answer && <AnswerView answer={exchange.answer} />}
      {exchange.error && <p className="ask-error">{exchange.error}</p>}
    </article>
  );
}
