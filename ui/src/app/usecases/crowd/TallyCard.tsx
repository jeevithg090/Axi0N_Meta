"use client";

import { useState } from "react";

import styles from "./crowd.module.css";
import type { ZoneTally } from "./crowdApi";
import { clock, fileUrl } from "./crowdApi";

/**
 * The hottest zone counted three ways: SAM 3.1, Muse Spark pointing and the Tier 1 model
 * (`checker`: a recorded run's model, else the role).
 */
export function TallyCard({ tally, checker = "Tier 1 model" }: { tally: ZoneTally | null; checker?: string }) {
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);
  if (!tally) {
    return (
      <div className={styles.card}>
        <h2>Three-way count</h2>
        <p className={styles.muted}>Waiting for the first count of the hottest zone.</p>
      </div>
    );
  }
  const counts: [string, number | null][] = [
    ["SAM 3.1", tally.sam],
    ["Muse Spark (points)", tally.spark],
    [checker, tally.glimmer],
  ];
  return (
    <div className={styles.card}>
      <div className={styles.row}>
        <h2>Three-way count: {tally.zone}</h2>
        <span
          className={styles.badge}
          style={{ background: tally.disagree ? "var(--color-bad)" : "var(--color-ok)" }}
        >
          {tally.disagree ? "counts disagree" : "counts agree"}
        </span>
        <span className={styles.small}>
          reading {clock(tally.at)} · {tally.seconds} s
        </span>
      </div>
      <div className={styles.row}>
        {counts.map(([name, value]) => (
          <div key={name} style={{ minWidth: 110 }}>
            <div className={styles.big}>{value ?? "–"}</div>
            <div className={styles.small}>{name}</div>
          </div>
        ))}
        <div className={styles.small}>
          range {tally.low}–{tally.high} · spread {Math.round(tally.spread * 100)}%
          {tally.spark_said != null && tally.spark_said !== tally.spark
            ? ` · Spark wrote ${tally.spark_said}`
            : ""}
        </div>
      </div>
      {tally.crop && (
        <div className={styles.frame} style={{ maxWidth: 420 }}>
          {/* eslint-disable-next-line @next/next/no-img-element -- live API image */}
          <img
            src={fileUrl(tally.crop)}
            alt={`The ${tally.zone} zone as the models counted it`}
            onLoad={(e) =>
              setSize({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })
            }
          />
          {size && (
            <svg className={styles.overlay} viewBox={`0 0 ${size.w} ${size.h}`} aria-hidden>
              {tally.points_px.map(([x, y], k) => (
                <circle key={k} cx={x} cy={y} r={5} fill="#00e5ff" stroke="#000" strokeWidth={1} />
              ))}
            </svg>
          )}
        </div>
      )}
      <span className={styles.small}>
        Cyan dots: where Muse Spark pointed.{" "}
        {tally.glimmer_note ? `${checker}: ${tally.glimmer_note}` : ""}
        {tally.notes.length ? ` (${tally.notes.join("; ")})` : ""} Muse Spark weighs the range in
        its next assessment.
      </span>
    </div>
  );
}
