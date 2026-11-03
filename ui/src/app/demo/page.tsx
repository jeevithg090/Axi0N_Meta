"use client";

import Link from "next/link";
import type { ReactNode } from "react";

import { StripFrames } from "@/components/VerifierPanel";
import {
  GOLDEN_RUNS,
  PENDING_RUNS,
  type GoldenRun,
  type GoldenVerification,
} from "@/fixtures/golden";
import {
  decisionText,
  goldenActions,
  goldenCandidates,
  plural,
  VERDICT_TEXT,
} from "@/lib/golden";
import { useDemo, useGolden } from "@/lib/stream";

function Check({ ok, children }: { ok: boolean; children: ReactNode }) {
  return <li className={ok ? "check-ok" : "check-bad"}>{children}</li>;
}

function CandidateStory({ run, v }: { run: GoldenRun; v: GoldenVerification }) {
  const candidate = goldenCandidates(run).find((c) => c.event_id === v.event_id);
  return (
    <div data-testid="golden-candidate">
      <p>
        <strong>{v.rule_id}</strong> fired on {run.camera_id}
        {v.measurement ? ` at ${v.measurement.value.toFixed(2)} ${v.measurement.unit}` : ""} (severity{" "}
        {v.severity}).
      </p>
      {candidate && <StripFrames paths={candidate.strip_paths} />}
      <p>
        <strong>Tier 1 · Glimmer:</strong> {VERDICT_TEXT[v.tier1.verdict] ?? v.tier1.verdict} (
        {Math.round(v.tier1.score * 100)}% violation) — {v.tier1.reason}
      </p>
      <p data-testid="golden-decision">{decisionText(v)}</p>
      <ul className="actions">
        {goldenActions(run, v.event_id).map((a, i) => (
          <li key={`${a.kind}-${i}`}>
            {a.kind}
            {a.role ? ` → ${a.role}` : ""} <span className="muted">{a.detail}</span>
          </li>
        ))}
      </ul>
      <ul>
        <Check ok={v.tier1_matches_chain}>Replayed verdict matches the chained one</Check>
        <Check ok={v.decision_matches_chain}>Replayed decision matches the chained one</Check>
      </ul>
    </div>
  );
}

function GoldenStory({ run }: { run: GoldenRun }) {
  const { plan, rule_ci, evidence, verify, score } = run.steps;
  const query = `?demo=${run.name}`;
  return (
    <div className="golden" data-testid="golden-run">
      <section aria-label="Run">
        <h2>Golden run · {run.name}</h2>
        <p>{run.description}</p>
        <p className="muted">
          Recorded live on {run.recorded_at.slice(0, 10)} with every model reply stored; this page
          and the other screens replay it with no model calls. Clip: {run.clip.description}{" "}
          {run.clip.license ? `(${run.clip.license})` : ""}
        </p>
        {/* Video Player for Golden Run */}
        <div style={{ margin: "16px 0", border: "2px solid var(--ink, #111)", background: "var(--cam, #0a0c0e)", overflow: "hidden" }}>
          <div className="camhud" style={{ display: "flex", justifyContent: "space-between", padding: "8px 12px", background: "#111", color: "#ECEAE3", fontSize: "12px", fontWeight: 700 }}>
            <span>EVALUATION CLIP · Mixkit Stock #45848 ({run.clip.description})</span>
            <span style={{ color: "var(--machine, #3DB8FF)" }}>OPERATOR ONBOARD · 0 FALSE ALERTS</span>
          </div>
          <video
            src="/videos/warehouse_forklift.mp4"
            controls
            autoPlay
            loop
            muted
            playsInline
            style={{ width: "100%", maxHeight: "380px", display: "block", objectFit: "contain", background: "#0a0c0e" }}
          />
          <div style={{ padding: "10px 14px", background: "var(--card, #1e2022)", borderTop: "1px solid rgba(255,255,255,0.1)", fontSize: "12px", color: "var(--muted, #8E8B82)" }}>
            <strong>Offline Eval Verification:</strong> 21.76 s recording of a forklift operating in a warehouse aisle. 
            The seated driver is recognized during warm-up; Rule CI proves boundary safety (16/16) and mutation resilience (6/6). Zero false alarms.
          </div>
        </div>

        <p>
          Watch it replay: <Link href={`/planner${query}`}>planner</Link> ·{" "}
          <Link href={`/plans/${plan.plan_id}${query}`}>plan and acceptance trace</Link> ·{" "}
          <Link href={`/monitor${query}`}>live monitor</Link>
        </p>
      </section>

      <section aria-label="Planner">
        <h2>1 · The request and the planner</h2>
        <p>“{plan.request}”</p>
        <ol>
          {plan.planner_steps.map((s) => (
            <li key={s.step}>
              <strong>{s.step}</strong> {s.status}
              {s.detail ? <span className="muted"> · {s.detail}</span> : null}
            </li>
          ))}
        </ol>
      </section>

      <section aria-label="Rule">
        <h2>2 · The rule and Rule CI</h2>
        {plan.rules.map((rule) => (
          <p key={rule.id}>
            <strong>{rule.id}</strong> · severity {rule.severity}
          </p>
        ))}
        <ul>
          <Check ok={plan.rule_hashes_match_run}>The runtime ran exactly these rules (hashes)</Check>
          {plan.rules.map((rule) => (
            <Check key={rule.id} ok={rule.canonical}>
              {rule.id} is in its canonical form
            </Check>
          ))}
          {rule_ci.map((ci) => (
            <Check key={ci.rule_id} ok={ci.ci_green && ci.nonvacuous}>
              Rule CI {ci.ci_green ? "green" : "red"}: {ci.summary}
              {ci.matches_plan ? " (re-run, same as at approval)" : " (differs from approval)"}
            </Check>
          ))}
        </ul>
      </section>

      <section aria-label="Runtime">
        <h2>3 · Approved, then the camera</h2>
        <p>
          The scored pass gave {plural(score.candidates, "candidate")} and{" "}
          {plural(score.alerts, "alert")} on a {score.status} clip
          {score.status === "negative"
            ? `: ${plural(score.false_alerts_on_negative, "false alert")} by the eval harness's count.`
            : "."}
        </p>
        {verify.map((v) => (
          <CandidateStory key={v.event_id} run={run} v={v} />
        ))}
      </section>

      <section aria-label="Evidence">
        <h2>4 · The evidence chain</h2>
        <ul>
          <Check ok={evidence.ok}>
            Chain intact: {evidence.n} records from the day&apos;s first, hash-linked
          </Check>
          <Check ok={evidence.reproduced === evidence.candidates}>
            {evidence.reproduced}/{evidence.candidates} decisions re-derived from the stored track
            window
          </Check>
        </ul>
        <p>
          Daily root: <code>{evidence.root}</code>
        </p>
        <p>
          <a href={run.verify_url} data-testid="verify-link">
            Verify it in your browser
          </a>{" "}
          <span className="muted">
            (your browser recomputes every SHA-256 of the{" "}
            <a href={run.chain_url}>chain file</a>; nothing is trusted from a server)
          </span>
        </p>
      </section>

      {PENDING_RUNS.map((p) => (
        <section key={p.name} className="pending" aria-label={`Pending: ${p.name}`}>
          <h2>Next golden run · {p.title}</h2>
          <p className="muted">Not recorded yet. Needs: {p.needs}</p>
        </section>
      ))}
    </div>
  );
}

export default function DemoPage() {
  const demo = useDemo();
  const golden = useGolden();
  if (demo === null) return <p className="muted">Loading…</p>;
  if (!golden) {
    return (
      <section className="golden" aria-label="Golden runs">
        <h2>Golden runs</h2>
        <p>
          Golden runs are recorded live runs that replay with no model calls, labelled REPLAY.
        </p>
        <ul>
          {GOLDEN_RUNS.map((run) => (
            <li key={run.name}>
              <Link href={`/demo?demo=${run.name}`}>{run.name}</Link>{" "}
              <span className="muted">{run.description}</span>
            </li>
          ))}
        </ul>
      </section>
    );
  }
  return <GoldenStory run={golden} />;
}
