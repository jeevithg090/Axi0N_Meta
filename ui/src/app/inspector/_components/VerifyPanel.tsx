"use client";

import { useEffect, useState } from "react";

import { dayHashes } from "../_lib/api";
import type { SharedPack } from "../_lib/types";
import { anchorDay, verifyPack, type Check, type DayAnchor, type PackReport } from "../_lib/verify";
import styles from "../inspector.module.css";

const LIVE_TEXT: Record<DayAnchor["live"], string> = {
  match: "the site's log today reproduces this root exactly",
  extended: "the site's log reproduces this root; records were added after the pack was made",
  mismatch: "the site's log NO LONGER reproduces this root",
  unavailable: "the site's log could not be reached from here",
};

function Line({ label, check }: { label: string; check: Check }) {
  return (
    <li>
      <span className={check.ok ? styles.ok : styles.bad}>{check.ok ? "✓" : "✗"}</span> {label}{" "}
      <span className={styles.muted}>
        ({check.passed}/{check.total})
      </span>
      {check.problems.length > 0 ? (
        <ul className={styles.muted}>
          {check.problems.map((p, i) => (
            <li key={i}>{p}</li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}

/** Runs every check in this browser and shows the result: nothing is taken from the site. */
export function VerifyPanel({ shared, token }: { shared: SharedPack; token: string | null }) {
  const [report, setReport] = useState<PackReport | null>(null);
  const [anchors, setAnchors] = useState<DayAnchor[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [seconds, setSeconds] = useState<number | null>(null);

  useEffect(() => {
    let live = true;
    setReport(null);
    setAnchors(null);
    const started = performance.now();
    verifyPack(shared, token)
      .then((r) => {
        if (!live) return;
        setSeconds((performance.now() - started) / 1000);
        setReport(r);
      })
      .catch((e: unknown) => live && setError(e instanceof Error ? e.message : "check failed"));
    Promise.all(shared.pack.days.map(async (d) => anchorDay(d, await dayHashes(d.day))))
      .then((a) => live && setAnchors(a))
      .catch(() => live && setAnchors([]));
    return () => {
      live = false;
    };
  }, [shared, token]);

  if (error) return <div className={styles.bannerBad}>✗ The check could not run: {error}</div>;
  if (!report) {
    return (
      <div className={styles.banner} role="status" aria-live="polite">
        Checking {shared.pack.records.length} records in your browser…
      </div>
    );
  }
  const broken = shared.pack.days.filter((d) => !d.chain_ok);
  return (
    <div
      className={report.ok ? styles.bannerOk : styles.bannerBad}
      role="status"
      aria-live="polite"
    >
      <b>
        {report.ok ? "✓ Verified in your browser" : "✗ This pack does not check out"} ·{" "}
        {shared.pack.records.length} records · {shared.pack.metrics.length} numbers
        {seconds !== null ? ` · ${seconds.toFixed(1)} s` : ""}
      </b>
      <ul className={styles.checks}>
        <Line
          label="The pack is exactly the one this link was made for (SHA-256)"
          check={report.packHash}
        />
        <Line
          label="Every disclosed record hashes to its recorded hash and its Merkle proof reaches its day's root"
          check={report.records}
        />
        <Line
          label="No record inside the period was left out (whole window disclosed, with both edges)"
          check={report.windows}
        />
        <Line
          label="Every number recounted from the records (this and the previous period)"
          check={report.numbers}
        />
        <Line
          label="Every summary sentence re-filled from the recomputed numbers"
          check={report.summaries}
        />
        <Line label="Each rule's gate matches its chained approval" check={report.rules} />
        <Line
          label="No number is counted per person or per tracked individual (rule, camera and shift only)"
          check={report.perPerson}
        />
      </ul>
      <div className={styles.muted} style={{ marginTop: 8 }}>
        {shared.pack.days.map((d) => {
          const anchor = anchors?.find((a) => a.day === d.day);
          return (
            <div key={d.day}>
              {d.day}: root <span className={styles.mono}>{d.root.slice(0, 16)}…</span> over {d.n}{" "}
              records ·{" "}
              {anchor ? (
                <span className={anchor.live === "mismatch" ? styles.bad : undefined}>
                  {LIVE_TEXT[anchor.live]}
                </span>
              ) : (
                "asking the site's log…"
              )}
              {anchor && anchor.committed !== "none"
                ? ` · root committed to git: ${anchor.committed}`
                : " · no root committed to git for this day"}
            </div>
          );
        })}
        {broken.length > 0 ? (
          <div className={styles.bad}>
            The site&apos;s own check found a broken chain on {broken.map((d) => d.day).join(", ")}.
          </div>
        ) : null}
        {shared.expires_at ? (
          <div>Link expires {shared.expires_at.replace("T", " ").slice(0, 16)} UTC.</div>
        ) : null}
      </div>
    </div>
  );
}
