"use client";

// Steps 2 to 6 of the supermarket story: advice from the answer, accept (pre-register + watch),
// the watch alerting staff with the marked photo, tasks closed with proof, and the owner brief.

import { useState } from "react";

import { errorText } from "@/lib/api";

import { speak } from "./AskPanel";
import {
  age,
  type Brief,
  type Camera,
  type CheckKind,
  fileUrl,
  type Finding,
  incidentUrl,
  retail,
  type RetailCard,
  type StaffTask,
  type StoreRow,
  type Watch,
} from "./api";
import styles from "./supermarket.module.css";

function useAction(): [
  boolean,
  string,
  (work: () => Promise<void>, fail: string) => Promise<void>,
] {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const run = async (work: () => Promise<void>, fail: string): Promise<void> => {
    setBusy(true);
    setError("");
    try {
      await work();
    } catch (e) {
      setError(errorText(e, fail));
    } finally {
      setBusy(false);
    }
  };
  return [busy, error, run];
}

export function AdviceCard({ card }: { card: RetailCard }) {
  return (
    <div className={styles.card} data-testid="advice-card">
      <strong>{card.title}</strong>
      <p className={styles.muted}>From the question: “{card.question}”</p>
      <div>
        <em>Saw</em>
        <ul>
          {card.saw.map((s) => (
            <li key={s}>{s}</li>
          ))}
        </ul>
      </div>
      <div>
        <em>Try</em>: {card.change}{" "}
        <span className={styles.muted}>
          ({card.recommendation.who_role}; undo: {card.recommendation.revert})
        </span>
      </div>
      {card.gain && (
        <div className={styles.ok}>
          <em>What-if</em>: {card.gain}
        </div>
      )}
      <table className={styles.table}>
        <tbody>
          {card.numbers.map((n) => (
            <tr key={n.id}>
              <td>{n.grounded ? "✓" : "✗ hidden"}</td>
              <td>{n.label}</td>
              <td>
                {n.value} {n.unit}
              </td>
              <td className={styles.muted}>{n.definition}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className={styles.muted}>
        ✓ = the number appears in the Ask answer or its tool results; ✗ = not found there, so it is
        not shown in the card.
      </p>
      <div className={styles.card}>
        <strong>Pre-registered check (switchback)</strong>
        <span>
          {card.plan.n_pairs} pairs of {card.plan.block_minutes}-minute blocks, change on / off,
          measured by a {card.watch.kind} watch on {card.watch.camera_id} every {card.watch.every_s}{" "}
          s. Smallest effect it can detect: {card.plan.min_detectable_effect.toFixed(2)} (
          {card.sigma_note}).
        </span>
        <span className={styles.muted}>{card.plan.decision_rule}</span>
        <span className={styles.mono}>
          plan hash {card.plan.preregistration_hash.slice(0, 24)}…
        </span>
        {card.preregistered_seq !== null && (
          <span className={styles.ok}>
            Pre-registered on the evidence chain at record #{card.preregistered_seq}, before
            anything changed. Watch {card.watch_id} is on.
          </span>
        )}
        {card.progress && card.progress.watch_readings > 0 && (
          <span>
            Watch readings so far: {card.progress.watch_readings}, mean{" "}
            {card.progress.mean_reading ?? "?"}. Pairs done: {card.progress.pairs_done} of{" "}
            {card.progress.pairs_planned}: too early to tell.
          </span>
        )}
      </div>
    </div>
  );
}

/** Record the last few minutes as a switchback block (change on or off) and show the read-out. */
export function SwitchbackControls({
  card,
  onCard,
}: {
  card: RetailCard;
  onCard: (card: RetailCard) => void;
}) {
  const [busy, error, run] = useAction();
  const [minutes, setMinutes] = useState(card.plan.block_minutes);
  const record = (state: "on" | "off") =>
    run(async () => {
      const end = new Date();
      const start = new Date(end.getTime() - minutes * 60_000);
      onCard(await retail.block(card.recommendation.id, state, start, end));
    }, "The block was not recorded.");
  const blocks = card.blocks ?? [];
  return (
    <div className={styles.card} data-testid="switchback">
      <strong>Switchback test</strong>
      <div className={styles.row}>
        <span>The last</span>
        <input
          className={styles.input}
          style={{ flex: "0 0 80px" }}
          type="number"
          min={1}
          value={minutes}
          onChange={(e) => setMinutes(Math.max(1, Number(e.target.value)))}
          aria-label="Block minutes"
        />
        <span>minutes were a block with the change</span>
        <button
          type="button"
          className={styles.ghost}
          disabled={busy}
          onClick={() => void record("on")}
        >
          ON
        </button>
        <button
          type="button"
          className={styles.ghost}
          disabled={busy}
          onClick={() => void record("off")}
        >
          OFF
        </button>
      </div>
      {error && <p className={styles.error}>{error}</p>}
      {blocks.length > 0 && (
        <table className={styles.table}>
          <tbody>
            {blocks.map((b) => (
              <tr key={`${b.start}-${b.state}`}>
                <td>{b.state.toUpperCase()}</td>
                <td>
                  {new Date(b.start).toLocaleTimeString()}–{new Date(b.end).toLocaleTimeString()}
                </td>
                <td>
                  {b.mean} from {b.readings} readings
                </td>
                <td className={styles.muted}>chain #{b.seq ?? "?"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {card.verdict && (
        <span className={card.verdict.label === "worked" ? styles.ok : styles.muted}>
          Read-out by the pre-registered rule: <strong>{card.verdict.label}</strong> after{" "}
          {card.verdict.pairs_done} of {card.plan.n_pairs} pairs (effect{" "}
          {card.verdict.effect.toFixed(2)}, p = {card.verdict.p_value.toFixed(3)}); card is now{" "}
          {card.recommendation.status}.
        </span>
      )}
    </div>
  );
}

export function AdvicePanel({
  receipt,
  cameras,
  card,
  onCard,
}: {
  receipt: string | null;
  cameras: Camera[];
  card: RetailCard | null;
  onCard: (card: RetailCard) => void;
}) {
  const [busy, error, run] = useAction();
  const [camera, setCamera] = useState("");
  const chosen = camera || cameras[0]?.camera_id || "";
  return (
    <div className={styles.steps}>
      <div className={styles.row}>
        <select value={chosen} onChange={(e) => setCamera(e.target.value)} aria-label="Camera">
          {cameras.map((c) => (
            <option key={c.camera_id} value={c.camera_id}>
              {c.name} ({c.camera_id})
            </option>
          ))}
        </select>
        <button
          type="button"
          className={styles.button}
          disabled={busy || !receipt}
          onClick={() =>
            void run(
              async () => onCard(await retail.advice(receipt ?? "", chosen || null)),
              "No advice card.",
            )
          }
        >
          Turn the answer into advice
        </button>
        {card && card.preregistered_seq === null && (
          <button
            type="button"
            className={styles.button}
            disabled={busy}
            onClick={() =>
              void run(
                async () => onCard(await retail.accept(card.recommendation.id)),
                "Accept failed.",
              )
            }
          >
            Accept: pre-register and start watching
          </button>
        )}
      </div>
      {!receipt && <p className={styles.muted}>Ask a question first (step 1).</p>}
      {busy && <p className={styles.muted}>Muse Spark is drafting the card from the answer…</p>}
      {error && <p className={styles.error}>{error}</p>}
      {card && <AdviceCard card={card} />}
      {card && card.preregistered_seq !== null && (
        <SwitchbackControls card={card} onCard={onCard} />
      )}
    </div>
  );
}

export function FindingCard({ finding }: { finding: Finding }) {
  const img = finding.evidence.marked ?? finding.evidence.frames.at(-1) ?? null;
  const lanes = Array.isArray(finding.detail.lanes) ? finding.detail.lanes.length : null;
  return (
    <div className={`${styles.card} ${finding.act ? styles.alert : ""}`} data-testid="finding">
      <div className={styles.row}>
        <span className={styles.pill}>{finding.kind}</span>
        <strong>{finding.camera_name || finding.camera_id}</strong>
        <span className={styles.muted}>
          {age(finding.checked_at)} · {finding.seconds.toFixed(1)} s
        </span>
      </div>
      <div>{finding.headline}</div>
      {finding.staff_message && (
        <div>
          {finding.alert_id ? "Staff told" : "Suggested (no new alert)"}:{" "}
          <em>{finding.staff_message}</em>{" "}
          {finding.alert_status && <span className={styles.pill}>{finding.alert_status}</span>}
        </div>
      )}
      {typeof finding.detail.reason === "string" && (
        <div className={styles.muted}>{finding.detail.reason}</div>
      )}
      {typeof finding.detail.summary === "string" && (
        <div className={styles.muted}>{finding.detail.summary}</div>
      )}
      {typeof finding.detail.announcement === "string" && finding.detail.announcement && (
        <div>
          Announced: <em>{finding.detail.announcement}</em>
        </div>
      )}
      {finding.chain_seq != null && (
        <div className={styles.muted}>
          Alert on the evidence chain at record #{finding.chain_seq}
        </div>
      )}
      {lanes !== null && <div className={styles.muted}>{lanes} lanes read</div>}
      {img && (
        // eslint-disable-next-line @next/next/no-img-element -- API image
        <img className={styles.image} src={fileUrl(img)} alt={`${finding.kind} evidence`} />
      )}
      <div className={styles.muted}>
        {finding.calls
          .map((c) => `${c.model} (${c.role}) ${c.seconds}s${c.error ? " ✗" : ""}`)
          .join(" · ")}
      </div>
    </div>
  );
}

const KINDS: CheckKind[] = ["queue", "hazard", "shelf", "exit"];

export function WatchPanel({
  cameras,
  watches,
  findings,
  onChange,
}: {
  cameras: Camera[];
  watches: Watch[];
  findings: Finding[];
  onChange: () => void;
}) {
  const [busy, error, run] = useAction();
  const [latest, setLatest] = useState<Finding | null>(null);
  const check = (kind: CheckKind, cam: string) =>
    run(async () => {
      setLatest(await retail.check(kind, cam));
      onChange();
    }, `The ${kind} check failed.`);
  const photo = (kind: "hazard" | "shelf", file: File | undefined) =>
    file &&
    run(async () => {
      setLatest(await retail.checkPhoto(kind, file));
      onChange();
    }, "The photo check failed.");
  const active = watches.filter((w) => w.active);
  return (
    <div className={styles.steps}>
      <table className={styles.table}>
        <thead>
          <tr>
            <th>Camera</th>
            <th>Recorded</th>
            <th>Check now</th>
          </tr>
        </thead>
        <tbody>
          {cameras.map((c) => (
            <tr key={c.camera_id}>
              <td>
                {c.name} <span className={styles.muted}>{c.camera_id}</span>
              </td>
              <td className={styles.muted}>{age(c.last)}</td>
              <td className={styles.row}>
                {KINDS.map((k) => (
                  <button
                    key={k}
                    type="button"
                    className={styles.ghost}
                    disabled={busy}
                    onClick={() => void check(k, c.camera_id)}
                  >
                    {k}
                  </button>
                ))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className={styles.row}>
        <label className={styles.ghost}>
          Shelf photo{" "}
          <input
            type="file"
            accept="image/jpeg,image/png"
            hidden
            onChange={(e) => void photo("shelf", e.target.files?.[0])}
          />
        </label>
        <label className={styles.ghost}>
          Floor photo{" "}
          <input
            type="file"
            accept="image/jpeg,image/png"
            hidden
            onChange={(e) => void photo("hazard", e.target.files?.[0])}
          />
        </label>
        {busy && <span className={styles.muted}>The models are looking…</span>}
      </div>
      {error && <p className={styles.error}>{error}</p>}
      <div>
        <strong>Standing watches</strong>
        {active.length === 0 && (
          <p className={styles.muted}>None yet: accept an advice card or add one.</p>
        )}
        {active.map((w) => (
          <div key={w.id} className={styles.row}>
            <span className={styles.pill}>{w.kind}</span> {w.camera_id} every {w.every_s} s → role “
            {w.role}”{w.advice_id && <span className={styles.muted}>from {w.advice_id}</span>}
            <span className={styles.muted}>last run {age(w.last_run) || "never"}</span>
            <button
              type="button"
              className={styles.ghost}
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  setLatest(await retail.runWatch(w.id));
                  onChange();
                }, "The watch run failed.")
              }
            >
              Run now
            </button>
            <button
              type="button"
              className={styles.ghost}
              onClick={() =>
                void run(async () => {
                  await retail.stopWatch(w.id);
                  onChange();
                }, "Stop failed.")
              }
            >
              Stop
            </button>
          </div>
        ))}
      </div>
      <div className={styles.grid2}>
        {(latest ? [latest, ...findings.filter((f) => f.id !== latest.id)] : findings)
          .slice(0, 6)
          .map((f) => (
            <FindingCard key={f.id} finding={f} />
          ))}
      </div>
    </div>
  );
}

export function TasksPanel({ tasks, onChange }: { tasks: StaffTask[]; onChange: () => void }) {
  const [busy, error, run] = useAction();
  const prove = (task: StaffTask, file: File | undefined) =>
    file &&
    run(async () => {
      await retail.proveTask(task.id, file);
      onChange();
    }, "The proof check failed.");
  if (tasks.length === 0) {
    return (
      <p className={styles.muted}>
        No staff tasks yet: a shelf or hazard check that finds something opens one.
      </p>
    );
  }
  return (
    <div className={styles.grid2}>
      {error && <p className={styles.error}>{error}</p>}
      {tasks.slice(0, 8).map((t) => (
        <div
          key={t.id}
          className={`${styles.card} ${t.status === "done" ? styles.ok : styles.alert}`}
        >
          <div className={styles.row}>
            <span className={styles.pill}>{t.kind === "shelf" ? "restock" : "hazard"}</span>
            <strong>{t.where}</strong>
            <span className={styles.pill}>{t.status}</span>
            <span className={styles.muted}>for “{t.role}”</span>
          </div>
          <ul>
            {t.items.map((i) => (
              <li key={i}>{i}</li>
            ))}
          </ul>
          <div className={styles.muted}>
            Seen {age(t.seen_at)}
            {t.done_at &&
              ` · fixed in ${t.minutes_to_fix ?? "?"} min (checked by ${t.closed_by})${
                t.fixed_seq !== null
                  ? `, chain records #${t.detected_seq ?? "?"} seen → #${t.fixed_seq} fixed`
                  : ""
              }`}
          </div>
          <a className={styles.muted} href={incidentUrl(t.id)}>
            Incident report (seen → alerted → {t.done_at ? "cleared" : "still open"})
          </a>
          {t.proof?.spark?.summary && (
            <div className={styles.muted}>Spark: {t.proof.spark.summary}</div>
          )}
          {t.proof?.glimmer?.summary && (
            <div className={styles.muted}>Tier 1 model: {t.proof.glimmer.summary}</div>
          )}
          <div className={styles.thumbs}>
            {t.marked && (
              // eslint-disable-next-line @next/next/no-img-element -- API image
              <img src={fileUrl(t.marked)} alt="before" />
            )}
            {t.proof_image && (
              // eslint-disable-next-line @next/next/no-img-element -- API image
              <img src={fileUrl(t.proof_image)} alt="after" />
            )}
          </div>
          {t.status !== "done" && (
            <div className={styles.row}>
              <label className={styles.ghost}>
                Close with a photo{" "}
                <input
                  type="file"
                  accept="image/jpeg,image/png"
                  hidden
                  disabled={busy}
                  onChange={(e) => void prove(t, e.target.files?.[0])}
                />
              </label>
              <button
                type="button"
                className={styles.ghost}
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    await retail.setTask(t.id, "on_my_way");
                    onChange();
                  }, "Update failed.")
                }
              >
                On my way
              </button>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

const STATUS_WORDS: Record<string, string> = {
  ok: "ok",
  below_chain: "below the chain",
  camera_blind: "camera blind",
  too_few: "too few to show a rate",
  not_ready: "not running",
};

/** This store's retail work scored the Chain Mode way (Wilson interval, small-crew floor). */
export function StoreRowPanel() {
  const [busy, error, run] = useAction();
  const [row, setRow] = useState<StoreRow | null>(null);
  const says = new Map(row?.fit.rules.map((r) => [r.rule_id, r.says]) ?? []);
  return (
    <div className={styles.card} data-testid="store-row">
      <div className={styles.row}>
        <strong>Chain scorecard row</strong>
        <button
          type="button"
          className={styles.ghost}
          disabled={busy}
          onClick={() => void run(async () => setRow(await retail.scorecard()), "No scorecard.")}
        >
          Score the last 24 h
        </button>
      </div>
      {error && <p className={styles.error}>{error}</p>}
      {row && (
        <table className={styles.table}>
          <tbody>
            {row.scorecard.scores.map((s) => (
              <tr key={s.rule_id}>
                <td>{says.get(s.rule_id) ?? s.rule_id}</td>
                <td>
                  {s.compliant} on time · {s.violations} late
                  {s.blind ? ` · ${s.blind} not watched` : ""}
                </td>
                <td>
                  {s.rate !== null
                    ? `${Math.round(s.rate * 100)}% (95%: ${Math.round((s.lo ?? 0) * 100)}–${Math.round((s.hi ?? 0) * 100)}%)`
                    : "–"}
                </td>
                <td className={styles.muted}>{STATUS_WORDS[s.status] ?? s.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {row && (
        <span className={styles.muted}>
          {row.fit.pack_id} · store {row.fit.store_id} · {row.scorecard.provenance} data, the same
          scorecard HQ uses for every store.
        </span>
      )}
    </div>
  );
}

export function BriefPanel({
  brief,
  onBrief,
}: {
  brief: Brief | null;
  onBrief: (b: Brief) => void;
}) {
  const [busy, error, run] = useAction();
  const [lang, setLang] = useState("English");
  return (
    <div className={styles.steps}>
      <div className={styles.row}>
        <select value={lang} onChange={(e) => setLang(e.target.value)} aria-label="Brief language">
          <option>English</option>
          <option>Spanish</option>
          <option>Hindi</option>
        </select>
        <button
          type="button"
          className={styles.button}
          disabled={busy}
          onClick={() => void run(async () => onBrief(await retail.brief(lang)), "No brief.")}
        >
          Write today’s brief
        </button>
        {brief && (
          <button
            type="button"
            className={styles.ghost}
            onClick={() => speak(brief.text, brief.lang)}
          >
            Read aloud
          </button>
        )}
      </div>
      {error && <p className={styles.error}>{error}</p>}
      {brief && (
        <div className={styles.card} data-testid="brief">
          <strong>{brief.headline}</strong>
          <p className={styles.answer}>{brief.text}</p>
          {brief.decisions.length > 0 && (
            <ul>
              {brief.decisions.map((d) => (
                <li key={d}>Decide: {d}</li>
              ))}
            </ul>
          )}
          <span className={styles.muted}>
            {brief.model} · {brief.seconds.toFixed(1)} s · counts from stored checks:{" "}
            {JSON.stringify(brief.stats)}
          </span>
        </div>
      )}
    </div>
  );
}
