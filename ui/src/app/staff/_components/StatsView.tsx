"use client";

import { useEffect, useState } from "react";

import { errorText } from "@/lib/api";

import { staffApi } from "../_lib/client";
import { duration, roleWords, type Words } from "../_lib/i18n";
import type { Bucket, StaffStats } from "../_lib/types";
import styles from "../staff.module.css";

function ms(value: number | null): string {
  if (value === null) return "–";
  return value < 1000 ? `${Math.round(value)} ms` : `${(value / 1000).toFixed(1)} s`;
}

function BucketCard({ bucket, words, label }: { bucket: Bucket; words: Words; label: string }) {
  const answered = bucket.items ? bucket.acknowledged / bucket.items : 0;
  return (
    <div className={styles.statCard}>
      <div className={styles.statHead}>
        <span className={styles.statKey}>{label}</span>
        <span className={styles.statLabel}>
          {bucket.items} {words.items} · {bucket.acknowledged} {words.acked}
        </span>
      </div>
      <div className={styles.statGrid}>
        <div>
          <div className={styles.statNum}>{duration(bucket.ack_p50_s)}</div>
          <div className={styles.statLabel}>
            {words.timeToAck} ({words.median})
          </div>
        </div>
        <div>
          <div className={styles.statNum}>{duration(bucket.ack_p90_s)}</div>
          <div className={styles.statLabel}>{words.p90}</div>
        </div>
        <div>
          <div className={styles.statNum}>{duration(bucket.done_p50_s)}</div>
          <div className={styles.statLabel}>{words.timeToFix}</div>
        </div>
        <div>
          <div className={styles.statNum}>{ms(bucket.phone_p50_ms)}</div>
          <div className={styles.statLabel}>
            {words.phone} ({words.median})
          </div>
        </div>
        <div>
          <div className={styles.statNum}>{bucket.escalated}</div>
          <div className={styles.statLabel}>{words.stage.escalated}</div>
        </div>
        <div>
          <div className={styles.statNum}>{bucket.false_alarms}</div>
          <div className={styles.statLabel}>{words.actions.false_alarm}</div>
        </div>
      </div>
      <div className={styles.bar} aria-label={`${Math.round(answered * 100)}% ${words.acked}`}>
        <div className={styles.barFill} style={{ width: `${Math.round(answered * 100)}%` }} />
      </div>
    </div>
  );
}

export function StatsView({ words }: { words: Words }) {
  const [stats, setStats] = useState<StaffStats | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    const load = () =>
      staffApi
        .stats(24)
        .then((s) => live && setStats(s))
        .catch((e: unknown) => live && setError(errorText(e, "Could not load")));
    void load();
    const id = window.setInterval(() => void load(), 10000);
    return () => {
      live = false;
      window.clearInterval(id);
    };
  }, []);
  if (error && !stats) return <p className={styles.empty}>{error}</p>;
  if (!stats) return null;
  return (
    <div className={styles.stats}>
      <p className={styles.sectionLabel}>{words.byRole} · 24 h · never per person</p>
      {stats.by_role.map((b) => (
        <BucketCard key={b.key} bucket={b} words={words} label={roleWords(b.key)} />
      ))}
      <p className={styles.sectionLabel}>
        {words.byShift} ({stats.timezone})
      </p>
      {stats.by_shift
        .filter((b) => b.items > 0)
        .map((b) => (
          <BucketCard
            key={b.key}
            bucket={b}
            words={words}
            label={`${b.key} ${stats.shifts[b.key] ?? ""}`}
          />
        ))}
    </div>
  );
}
