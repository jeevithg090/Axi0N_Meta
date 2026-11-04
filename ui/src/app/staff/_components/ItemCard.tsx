"use client";

import { useEffect, useState } from "react";

import { mediaUrl } from "../_lib/client";
import { countdown, duration, roleWords, type Words } from "../_lib/i18n";
import type { StaffAction, StaffItem, Stage } from "../_lib/types";
import styles from "../staff.module.css";

const CLOSED = new Set(["done", "false_alarm"]);
const DONE_STAGES = new Set<Stage>(["acknowledged", "on_my_way", "done"]);
const WARN_STAGES = new Set<Stage>(["escalated", "false_alarm"]);

function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return undefined;
    const id = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(id);
  }, [active]);
  return now;
}

function clock(ts: string): string {
  return new Date(ts).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function ago(ts: string, now: number): string {
  return duration((now - Date.parse(ts)) / 1000);
}

/** The next tap that moves the item on: Acknowledge → On my way → Done. */
export function nextAction(item: StaffItem): StaffAction | null {
  if (item.status === "open") return "acknowledge";
  if (item.status === "acknowledged") return "on_my_way";
  if (item.status === "on_my_way") return "done";
  return null;
}

function cardClass(item: StaffItem, fresh: boolean): string {
  const parts = [styles.card];
  if (CLOSED.has(item.status)) parts.push(styles.cardClosed);
  else if (item.source === "crowd") parts.push(styles.cardCrowd);
  else if (item.source === "shelf") parts.push(styles.cardShelf);
  else if (item.severity === "high" || item.severity === "critical") parts.push(styles.cardHigh);
  if (fresh) parts.push(styles.cardNew);
  return parts.join(" ");
}

function SourceTag({ item, words }: { item: StaffItem; words: Words }) {
  const tone =
    item.source === "crowd"
      ? styles.tagViolet
      : item.source === "shelf"
        ? styles.tagBlue
        : item.source === "review"
          ? styles.tagAmber
          : item.severity === "high" || item.severity === "critical"
            ? styles.tagRed
            : styles.tagAmber;
  return <span className={`${styles.tag} ${tone}`}>{words.source[item.source]}</span>;
}

function Loop({ item, words }: { item: StaffItem; words: Words }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={styles.loop}>
      <div className={styles.loopSummary}>
        <span>
          <b>{duration(item.ack_s)}</b> {words.timeToAck}
        </span>
        <span>
          <b>{duration(item.fix_s)}</b> {words.timeToFix}
        </span>
        <button
          type="button"
          className={styles.loopToggle}
          onClick={() => setOpen(!open)}
          aria-expanded={open}
        >
          {words.loop} {open ? "▴" : "▾"}
        </button>
      </div>
      {open ? (
        <ol className={styles.steps} aria-label={words.loop}>
          {item.timeline.map((step, k) => (
            <li
              key={`${step.stage}-${k}`}
              className={`${styles.step} ${DONE_STAGES.has(step.stage) ? styles.stepDone : ""} ${
                WARN_STAGES.has(step.stage) ? styles.stepWarn : ""
              }`}
            >
              <span className={styles.stepTime}>+{duration(step.after_s)}</span>
              <strong>{words.stage[step.stage]}</strong> · {step.label}
            </li>
          ))}
        </ol>
      ) : null}
    </div>
  );
}

export interface ItemCardProps {
  item: StaffItem;
  words: Words;
  fresh: boolean;
  busy: boolean;
  translated: string | null;
  message: string | null;
  onAct: (item: StaffItem, action: StaffAction) => void;
  onRead: (item: StaffItem) => void;
}

export function ItemCard({
  item,
  words,
  fresh,
  busy,
  translated,
  message,
  onAct,
  onRead,
}: ItemCardProps) {
  const escalating = item.escalates_at !== null && !CLOSED.has(item.status);
  const now = useNow(escalating);
  const next = nextAction(item);
  const closed = CLOSED.has(item.status);
  const left = item.escalates_at ? Date.parse(item.escalates_at) - now : 0;
  const where = item.camera_name ?? item.camera_id;
  const confidence = item.verdict?.confidence ?? null;

  return (
    <article className={cardClass(item, fresh)} aria-label={item.title} data-item={item.item_id}>
      <div className={styles.cardHead}>
        <div className={styles.meta}>
          <SourceTag item={item} words={words} />
          {item.sample ? (
            <span className={`${styles.tag} ${styles.tagAmber}`}>{words.sample}</span>
          ) : null}
          {item.dry_run ? <span className={styles.tag}>DRY RUN</span> : null}
          <span className={`${styles.tag} ${closed ? styles.tagGreen : ""}`}>
            {words.status[item.status]}
          </span>
          <span>
            {clock(item.ts)} · {ago(item.ts, now)}
          </span>
          {where ? <span>· {where}</span> : null}
        </div>
        <h2 className={styles.title}>{item.title}</h2>
        {translated ? (
          <p className={styles.translated} lang="und">
            {translated}
          </p>
        ) : null}
        <p className={styles.body}>{item.body}</p>
      </div>

      {item.thumbnails > 0 ? (
        <div className={styles.strip} aria-label="Evidence">
          {Array.from({ length: Math.min(item.thumbnails, 8) }, (_, k) => (
            // eslint-disable-next-line @next/next/no-img-element -- evidence stills come from the API
            <img
              key={k}
              src={mediaUrl(item.item_id, k)}
              alt={`Evidence still ${k + 1}`}
              loading="lazy"
            />
          ))}
        </div>
      ) : null}

      <div className={styles.facts}>
        {item.verdict ? (
          <div className={styles.fact}>
            <span className={styles.factLabel}>
              {words.verifiedBy} {item.verdict.model}
            </span>
            <span className={styles.factValue}>
              {item.verdict.verdict}
              {confidence !== null ? ` · ${Math.round(confidence * 100)}%` : ""}
            </span>
            {confidence !== null ? (
              <div className={styles.confidence}>
                <div
                  className={styles.confidenceFill}
                  style={{ width: `${Math.round(confidence * 100)}%` }}
                />
              </div>
            ) : null}
          </div>
        ) : null}
        {item.measurement ? (
          <div className={styles.fact}>
            <span className={styles.factLabel}>Measured</span>
            <span className={styles.factValue}>{item.measurement}</span>
          </div>
        ) : null}
        <div className={styles.fact}>
          <span className={styles.factLabel}>Sent to</span>
          <span className={styles.factValue}>{item.roles.map(roleWords).join(" → ")}</span>
        </div>
      </div>
      {item.verdict?.reason ? <p className={styles.reason}>“{item.verdict.reason}”</p> : null}

      {escalating ? (
        <div className={styles.countdown} role="timer" aria-live="off">
          <span>{words.escalatesIn}</span>
          <span className={styles.countdownTime}>{countdown(left)}</span>
          {item.next_role ? (
            <span>
              {words.escalatesTo} <strong>{roleWords(item.next_role)}</strong>
            </span>
          ) : null}
        </div>
      ) : null}

      {item.do_now.length > 0 ? (
        <ul className={styles.doNow} aria-label={words.doNow}>
          {item.do_now.map((line) => (
            <li key={line}>→ {line}</li>
          ))}
        </ul>
      ) : null}
      {item.announcements.length > 0 ? (
        <ul className={styles.doNow} aria-label={words.announced}>
          {item.announcements.map((a) => (
            <li key={`${a.lang}-${a.text}`}>
              🔊 {a.lang}: {a.text}
            </li>
          ))}
        </ul>
      ) : null}

      <Loop item={item} words={words} />

      <div className={styles.actions}>
        {next ? (
          <button
            type="button"
            className={`${styles.primary} ${next === "on_my_way" ? styles.primaryGo : ""} ${
              next === "done" ? styles.primaryDone : ""
            }`}
            disabled={busy}
            onClick={() => onAct(item, next)}
          >
            {words.actions[next]}
          </button>
        ) : null}
        {!closed && next !== "done" ? (
          <button
            type="button"
            className={styles.secondary}
            disabled={busy}
            onClick={() => onAct(item, "done")}
          >
            {words.actions.done}
          </button>
        ) : null}
        {!closed ? (
          <button
            type="button"
            className={styles.secondary}
            disabled={busy}
            onClick={() => onAct(item, "escalate")}
          >
            {words.actions.escalate}
          </button>
        ) : null}
        {!closed ? (
          <button
            type="button"
            className={`${styles.secondary} ${styles.secondaryDanger}`}
            disabled={busy}
            onClick={() => onAct(item, "false_alarm")}
          >
            {words.actions.false_alarm}
          </button>
        ) : null}
        <button type="button" className={styles.iconButton} onClick={() => onRead(item)}>
          🔊 {words.readAloud}
        </button>
      </div>
      {message ? (
        <p className={styles.message} role="status">
          {message}
        </p>
      ) : null}
    </article>
  );
}
