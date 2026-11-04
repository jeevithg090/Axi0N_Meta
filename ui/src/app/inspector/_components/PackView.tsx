"use client";

import { useMemo, useState } from "react";

import { formatValue } from "../_lib/engine";
import { AUDIENCE_NAMES, type Metric, type ProofPack } from "../_lib/types";
import styles from "../inspector.module.css";
import { WorkerCertificate } from "./WorkerCertificate";

const HEADLINE_SECTIONS = ["alerts", "response", "disputes", "governance"] as const;
const SECTION_TITLES: Record<string, string> = {
  alerts: "Alerts and verification",
  response: "Response",
  disputes: "Worker disputes",
  governance: "Governance: plans and protected privacy settings",
  forklift: "Near misses (rules that measure a distance, e.g. forklift separation)",
  hazard: "Hazards seen and cleared",
  rule: "By rule",
  shift: "By shift",
  camera: "By camera",
};
const CHECK_TEXT: Record<"page" | "search" | "none", string> = {
  page: "quote found on the page",
  search: "quote found in the search result for the page",
  none: "quote not confirmed",
};
const SECTION_ORDER = [...HEADLINE_SECTIONS, "forklift", "hazard", "rule", "shift", "camera"];

function utc(ts: string): string {
  return ts.replace("T", " ").replace(/\.\d+/, "").replace("Z", " UTC");
}

function Refs({
  metric,
  records,
}: {
  metric: Metric;
  records: Map<string, Record<string, unknown>>;
}) {
  if (metric.refs.length === 0) return null;
  return (
    <details className={styles.details}>
      <summary>{metric.refs.length} record(s)</summary>
      <ul className={styles.mono}>
        {metric.refs.slice(0, 50).map((key) => {
          const record = records.get(key);
          return (
            <li key={key}>
              {key} · {String(record?.kind ?? "?")} · {String(record?.hash ?? "").slice(0, 16)}…
            </li>
          );
        })}
        {metric.refs.length > 50 ? <li>… and {metric.refs.length - 50} more</li> : null}
      </ul>
    </details>
  );
}

function MetricTable({
  metrics,
  records,
}: {
  metrics: Metric[];
  records: Map<string, Record<string, unknown>>;
}) {
  return (
    <table className={styles.table}>
      <thead>
        <tr>
          <th>What</th>
          <th className={styles.num}>This period</th>
          <th className={styles.num}>Previous</th>
          <th>Evidence</th>
        </tr>
      </thead>
      <tbody>
        {metrics.map((m) => (
          <tr key={m.id}>
            <td>
              {m.label}
              {m.spec.rule ? <div className={styles.mono}>{m.spec.rule}</div> : null}
            </td>
            <td className={styles.num}>{formatValue(m.value, m.unit)}</td>
            <td className={styles.num}>{formatValue(m.previous, m.unit)}</td>
            <td>
              <Refs metric={m} records={records} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** The pack's contents: summaries, rules with clauses, every number with its records. */
export function PackView({ pack }: { pack: ProofPack }) {
  const records = useMemo(
    () => new Map(pack.records.map((r) => [r.key, r.record])),
    [pack.records],
  );
  const [audience, setAudience] = useState(pack.summaries[0]?.audience ?? null);
  const summary = pack.summaries.find((s) => s.audience === audience);
  const bySection = useMemo(() => {
    const map = new Map<string, Metric[]>();
    for (const m of pack.metrics) map.set(m.section, [...(map.get(m.section) ?? []), m]);
    return map;
  }, [pack.metrics]);

  return (
    <div>
      <p className={styles.muted}>
        Site <b>{pack.site}</b> · {utc(pack.period.start)} to {utc(pack.period.end)} · compared with
        the same length before · privacy mode {pack.privacy_mode} · shifts{" "}
        {pack.period.shifts.map((s) => `${s.name} from ${s.start_hour}:00`).join(", ")} (
        {pack.period.timezone})
      </p>
      <p className={styles.disclaimer}>{pack.disclaimer}</p>

      {pack.summaries.length > 0 ? (
        <section className={styles.card}>
          <h2>Summary</h2>
          <div className={styles.tabs} role="tablist">
            {pack.summaries.map((s) => (
              <button
                key={s.audience}
                type="button"
                role="tab"
                aria-selected={s.audience === audience}
                className={s.audience === audience ? styles.tabOn : styles.tab}
                onClick={() => setAudience(s.audience)}
              >
                {AUDIENCE_NAMES[s.audience]}
                {s.lang !== "en" ? ` (${s.lang})` : ""}
              </button>
            ))}
          </div>
          {summary ? (
            <ul>
              {summary.text.map((line, i) => (
                <li key={i}>{line}</li>
              ))}
            </ul>
          ) : null}
          <p className={styles.muted}>
            Written by {pack.summaries[0]?.model}. Every number in these sentences is filled in from
            the tables below, and your browser re-fills them from the numbers it recomputed.
          </p>
        </section>
      ) : null}

      {pack.audiences.includes("worker_rep") ? <WorkerCertificate pack={pack} /> : null}

      <section className={styles.card}>
        <h2>Rules in force</h2>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>Rule</th>
              <th>Clause it gives evidence for</th>
              <th>Proved before going live</th>
            </tr>
          </thead>
          <tbody>
            {pack.rules.map((r) => (
              <tr key={r.rule_id}>
                <td>
                  {r.text || r.rule_id}
                  <div className={styles.mono}>
                    {r.rule_id} · {r.severity} · in force {formatValue(r.active_s, "seconds")}
                  </div>
                </td>
                <td>
                  {r.clause ? (
                    <>
                      <b>{r.clause.ref}</b>{" "}
                      <span className={r.clause.checked ? styles.ok : styles.bad}>
                        {CHECK_TEXT[r.clause.check ?? (r.clause.checked ? "page" : "none")]}
                      </span>
                      <blockquote className={styles.quote}>{r.clause.quote}</blockquote>
                      <a
                        href={r.clause.url}
                        target="_blank"
                        rel="noreferrer"
                        className={styles.mono}
                      >
                        {r.clause.url}
                      </a>
                      <div className={styles.muted}>
                        {r.clause.source === "plan"
                          ? "Cited by the plan"
                          : "Found by Muse Spark 1.3"}
                        {` · ${r.clause.detail}`}
                      </div>
                    </>
                  ) : (
                    <span className={styles.muted}>{r.clause_note || "none cited"}</span>
                  )}
                </td>
                <td>
                  Gate at approval:{" "}
                  <span className={r.gate === "pass" ? styles.ok : styles.bad}>{r.gate}</span>
                  {r.gate_reasons.map((reason, i) => (
                    <div key={i} className={styles.muted}>
                      {reason}
                    </div>
                  ))}
                  {r.ci_detail ? <div>Rule CI: {r.ci_detail}</div> : null}
                  <div className={styles.muted}>{r.plan_ids.length} plan version(s)</div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {SECTION_ORDER.filter((s) => bySection.has(s)).map((section) => (
        <section key={section} className={styles.card}>
          <h2>{SECTION_TITLES[section] ?? section}</h2>
          <MetricTable metrics={bySection.get(section) ?? []} records={records} />
        </section>
      ))}

      {pack.incidents.length > 0 ? (
        <section className={styles.card}>
          <h2>Incidents ({pack.incidents.length})</h2>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>When</th>
                <th>Rule</th>
                <th>Camera</th>
                <th>Glimmer</th>
                <th>Route</th>
                <th>Record</th>
              </tr>
            </thead>
            <tbody>
              {pack.incidents.map((i) => (
                <tr key={i.event_id}>
                  <td>{utc(i.ts)}</td>
                  <td>{i.rule_id}</td>
                  <td>{i.camera_id}</td>
                  <td>{i.verdict ?? "-"}</td>
                  <td>{i.route ?? "-"}</td>
                  <td className={styles.mono}>{i.key}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : null}

      {pack.stills.length > 0 ? (
        <section className={styles.card}>
          <h2>Anonymised stills</h2>
          <div className={styles.stills}>
            {pack.stills.map((s) => (
              // eslint-disable-next-line @next/next/no-img-element
              <img key={s.sha256} src={s.data_uri} alt={`${s.kind} still of ${s.event_id}`} />
            ))}
          </div>
          <p className={styles.muted}>Illustrations only; the chain does not commit to images.</p>
        </section>
      ) : null}

      <section className={styles.card}>
        <h2>What this pack does not show</h2>
        <ul>
          {pack.notes.map((n, i) => (
            <li key={i}>{n}</li>
          ))}
        </ul>
      </section>
    </div>
  );
}
