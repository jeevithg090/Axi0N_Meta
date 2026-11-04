"use client";

import Link from "next/link";
import type { ReactNode } from "react";

import { LiveAlerts } from "@/components/demo/LiveAlerts";
import { LiveBadge, StepFlow } from "@/components/kit";
import { StripFrames } from "@/components/VerifierPanel";
import {
  GOLDEN_RUNS,
  PENDING_RUNS,
  type GoldenRun,
  type GoldenVerification,
} from "@/fixtures/golden";
import { decisionText, goldenActions, goldenCandidates, plural, VERDICT_TEXT } from "@/lib/golden";
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
        {v.measurement ? ` at ${v.measurement.value.toFixed(2)} ${v.measurement.unit}` : ""}{" "}
        (severity {v.severity}).
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
      <header className="golden-hero">
        <LiveBadge mode="replay" detail={`recorded ${run.recorded_at.slice(0, 10)}, no model calls`} />
        <h1 className="golden-title">The golden replay</h1>
        <p className="lead">
          One request, followed from plain words to a hash-chained decision. Every model reply was
          stored, so this works with every GPU switched off.
        </p>
        <StepFlow
          label="What this replay shows"
          size="compact"
          as="h2"
          steps={[
            { name: "Describe", text: "The owner's request, in their words.", state: "done", href: "#planner" },
            { name: "Plan", text: "The planner's steps, with their real details.", state: "done", href: "#planner" },
            { name: "Prove", text: "Rule CI, re-run offline, same as at approval.", state: "done", href: "#rule" },
            { name: "Trust", text: "The chain, checked in your own browser.", state: "done", href: "#evidence" },
          ]}
        />
      </header>
      <LiveAlerts />
      <section aria-label="Run">
        <h2>Golden run · {run.name}</h2>
        <p>{run.description}</p>
        <p className="muted">
          Recorded live on {run.recorded_at.slice(0, 10)} with every model reply stored; this page
          and the other screens replay it with no model calls. Clip: {run.clip.description}{" "}
          {run.clip.license ? `(${run.clip.license})` : ""}
        </p>
        {run.clip.source && (
          <p data-testid="golden-clip">
            The clip is a stock video, not hosted here:{" "}
            <a href={run.clip.source}>watch {run.clip.name ?? "it"} at the source</a>. On this
            negative clip the scored replay gives{" "}
            {plural(score.false_alerts_on_negative, "false alert")}.
          </p>
        )}
        <p>
          Watch it replay: <Link href={`/planner${query}`}>planner</Link> ·{" "}
          <Link href={`/plans/${plan.plan_id}${query}`}>plan and acceptance trace</Link> ·{" "}
          <Link href={`/monitor${query}`}>live monitor</Link>
        </p>
      </section>

      <section aria-label="Planner" id="planner">
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

      <section aria-label="Rule" id="rule">
        <h2>2 · The rule and Rule CI</h2>
        {plan.rules.map((rule) => (
          <p key={rule.id}>
            <strong>{rule.id}</strong> · severity {rule.severity}
          </p>
        ))}
        <ul>
          <Check ok={plan.rule_hashes_match_run}>
            The runtime ran exactly these rules (hashes)
          </Check>
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

      <section aria-label="Runtime" id="runtime">
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

      <section aria-label="Evidence" id="evidence">
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
          Daily root: <code className="golden-root">{evidence.root}</code>
        </p>
        <p>
          <a href={run.verify_url} data-testid="verify-link" className="btn primary">
            Verify it in your browser
          </a>{" "}
          <span className="muted">
            (your browser recomputes every SHA-256 of the <a href={run.chain_url}>chain file</a>;
            nothing is trusted from a server)
          </span>
        </p>
      </section>

      {PENDING_RUNS.length > 0 && (
        <p className="muted" data-testid="pending-note">
          {PENDING_RUNS.length === 1
            ? "One more recorded run is coming"
            : `${PENDING_RUNS.length} more recorded runs are coming`}
          : {PENDING_RUNS.map((p) => p.title).join("; ")}.
        </p>
      )}
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
        <p>Golden runs are recorded live runs that replay with no model calls, labelled REPLAY.</p>
        <ul>
          {GOLDEN_RUNS.map((run) => (
            <li key={run.name}>
              <Link href={`/demo?demo=${run.name}`}>{run.name}</Link>{" "}
              <span className="muted">{run.description}</span>
            </li>
          ))}
        </ul>
        <LiveAlerts />
      </section>
    );
  }
  return <GoldenStory run={golden} />;
}
