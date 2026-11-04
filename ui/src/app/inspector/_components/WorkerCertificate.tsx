"use client";

// The crew representative's certificate: the few facts a union or works-council rep asks about
// cameras at work, each read from a number the browser recounts (two-key settings from
// worker/two_key.py's chained `rule_change` records, disputes, the rules in force). The crew's
// own-language summary from Muse Spark sits under the "Crew representative" tab.

import { formatValue } from "../_lib/engine";
import type { ProofPack } from "../_lib/types";
import styles from "../inspector.module.css";

function value(pack: ProofPack, id: string): number | null {
  return pack.metrics.find((m) => m.id === id)?.value ?? null;
}

export function WorkerCertificate({ pack }: { pack: ProofPack }) {
  const without = value(pack, "settings_without_both_keys");
  const applied = value(pack, "settings_applied");
  const proposed = value(pack, "setting_proposals");
  const refused = value(pack, "settings_rejected");
  const filed = value(pack, "disputes_filed");
  const upheld = value(pack, "disputes_upheld");
  const rejected = value(pack, "disputes_rejected");
  const crew = pack.summaries.find((s) => s.audience === "worker_rep");
  const facts: { ok: boolean | null; text: string }[] = [
    {
      ok: without === 0,
      text:
        without === 0
          ? "No protected privacy setting (privacy mode, retention, the per-person metrics ban) was changed without both the owner's and the crew representative's key."
          : `${formatValue(without, "count")} protected setting change(s) were applied without both keys.`,
    },
    {
      ok: null,
      text: `Protected settings proposed ${formatValue(proposed, "count")}, changed with both keys ${formatValue(applied, "count")}, refused ${formatValue(refused, "count")}.`,
    },
    {
      ok: null,
      text: `${pack.rules.length} rule(s) were in force, each listed below in the owner's own words with how it was tested before going live.`,
    },
    {
      ok: null,
      text: `Workers disputed ${formatValue(filed, "count")} alert(s): ${formatValue(upheld, "count")} upheld as false alarms, ${formatValue(rejected, "count")} rejected.`,
    },
    {
      ok: true,
      text: "Every number is per rule, camera or shift. None is per person; your browser checks every recipe for that.",
    },
  ];
  return (
    <section className={styles.card}>
      <h2>Crew representative certificate</h2>
      <ul className={styles.checks}>
        {facts.map((f, i) => (
          <li key={i}>
            {f.ok === null ? (
              "·"
            ) : (
              <span className={f.ok ? styles.ok : styles.bad}>{f.ok ? "✓" : "✗"}</span>
            )}{" "}
            {f.text}
          </li>
        ))}
      </ul>
      {crew ? (
        <p className={styles.muted}>
          A summary in the crew&apos;s language ({crew.lang}) is under the Crew representative tab.
        </p>
      ) : null}
    </section>
  );
}
