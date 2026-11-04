"use client";

import { useEffect, useState, type FormEvent } from "react";

import { api, ApiError } from "@/lib/api";
import {
  DISPUTE_REASONS,
  disputeError,
  MAX_REASON_TEXT,
  type DisputeOutcome,
  type DisputeReason,
  type WorkerStrings,
} from "@/lib/worker";

import styles from "./worker.module.css";

interface Props {
  code: string;
  strings: WorkerStrings;
  onClose: () => void;
  onDisputed: () => void;
}

/** The anonymous tap-dispute sheet for one event card. */
export function DisputeSheet({ code, strings, onClose, onDisputed }: Props) {
  const [reason, setReason] = useState<DisputeReason>("lookalike_object");
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [outcome, setOutcome] = useState<DisputeOutcome | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSending(true);
    setError(null);
    try {
      const result = await api.dispute({
        event_code: code,
        reason_choice: reason,
        reason_text: text,
      });
      setOutcome(result.outcome);
      onDisputed();
    } catch (err) {
      setError(disputeError(err instanceof ApiError ? err.status : 0, strings));
    } finally {
      setSending(false);
    }
  };

  const titleId = `dispute-${code}`;
  return (
    <div className={`sheet-backdrop ${styles.backdrop}`}>
      <div
        className={`sheet ${styles.sheet}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <h2 id={titleId}>{strings.sheetTitle(code)}</h2>
        {outcome ? (
          <p role="status" className={`outcome outcome-${outcome}`}>
            {strings.outcomes[outcome]}
          </p>
        ) : (
          <form onSubmit={submit}>
            <fieldset>
              <legend>{strings.reason}</legend>
              {DISPUTE_REASONS.map((choice, index) => (
                <label key={choice} className={`choice ${styles.choice}`}>
                  <input
                    type="radio"
                    name="reason"
                    value={choice}
                    checked={reason === choice}
                    onChange={() => setReason(choice)}
                    autoFocus={index === 0}
                  />
                  {strings.reasons[choice]}
                </label>
              ))}
            </fieldset>
            <label className={`details ${styles.details}`}>
              {strings.details}
              <textarea
                value={text}
                maxLength={MAX_REASON_TEXT}
                onChange={(e) => setText(e.target.value)}
                aria-describedby={`${titleId}-hint`}
              />
            </label>
            <p id={`${titleId}-hint`} className="muted">
              {strings.detailsHint} {strings.anonymous}
            </p>
            {error && (
              <p role="alert" className="outcome outcome-error">
                {error}
              </p>
            )}
            <button
              type="submit"
              className={`tap primary ${styles.tap} ${styles.primary}`}
              disabled={sending}
            >
              {sending ? strings.sending : strings.submit}
            </button>
          </form>
        )}
        <button type="button" className={`tap ${styles.tap}`} onClick={onClose}>
          {strings.close}
        </button>
      </div>
    </div>
  );
}
