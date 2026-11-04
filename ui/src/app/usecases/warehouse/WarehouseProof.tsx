"use client";

// The forklift rule's proof, from records already in the repository: the 1 Nov live run's
// measurement and Glimmer verdict (T02), and the 2 Nov golden runs' strips, decisions and chains
// (worker_passes_4m, driver_only_4m). Shown to everyone, labelled as recorded; never as live.

import Link from "next/link";

import { EvidenceCard, VerdictPill, formatMs } from "@/components/kit";
import { UseCaseProof } from "@/components/usecases/UseCaseProof";
import { goldenRun, type GoldenRun, type GoldenVerification } from "@/fixtures/golden";
import { campaignTest, distanceLimit, firstEvent } from "@/fixtures/live-campaign";
import { decisionText, goldenCandidates } from "@/lib/golden";

/** One recorded verification and the strip Glimmer looked at for it. */
function strip(run: GoldenRun, pick: (v: GoldenVerification) => boolean) {
  const v = run.steps.verify.find(pick) ?? run.steps.verify[0];
  if (!v) return null;
  const candidate = goldenCandidates(run).find((c) => c.event_id === v.event_id);
  return { v, frames: (candidate?.strip_paths ?? []).slice(0, 4) };
}

export function WarehouseProof() {
  const t02 = campaignTest("T02");
  const reading = firstEvent(t02);
  const limit = t02.rules[0] ? distanceLimit(t02.rules[0].predicate) : null;
  const passes = goldenRun("worker_passes_4m");
  const driver = goldenRun("driver_only_4m");
  const alert = passes ? strip(passes, (v) => v.tier1.verdict === "violation") : null;
  const excluded = driver ? strip(driver, (v) => v.tier1.verdict === "no_violation") : null;

  return (
    <UseCaseProof
      id="wh-proof"
      testId="warehouse-proof"
      title="What one forklift rule proved"
      badge={{ mode: "replay", detail: "recorded 1 and 2 Nov 2026" }}
      reading={{
        value: reading.measurement ? reading.measurement.value.toFixed(2) : "unknown",
        unit: reading.measurement?.unit ?? "m",
        caption: `A worker passing a moving forklift, measured by SAM 3.1 on the calibrated floor${
          limit !== null ? `; the rule says under ${limit} m` : ""
        }. Live run, 1 Nov (${t02.id}).`,
      }}
      verdict={<VerdictPill verdict={reading.verdict.verdict} confidence={reading.verdict.score} />}
      reason={reading.verdict.reason}
      chips={[
        { model: "SAM 3.1", job: "measured the gap" },
        { model: "Muse Glimmer 30B", job: "checked the frames", ms: reading.verdict.latency_ms },
      ]}
      footnote={`Event to checked decision ${formatMs(reading.event_to_decision_s * 1000)}.`}
      after={
        <>
          Replay both runs with no model calls:{" "}
          <Link href="/demo?demo=worker_passes_4m">worker passes</Link> and{" "}
          <Link href="/demo?demo=driver_only_4m">seated driver</Link>.
        </>
      }
    >
      {passes && alert && (
        <EvidenceCard
          title="Verified alert: a worker passes the forklift"
          caption={`Golden run recorded ${passes.recorded_at.slice(0, 10)}: the strip Glimmer looked at.`}
          frames={alert.frames.map((src, i) => ({ src, alt: `Strip frame ${i + 1}: a worker beside the moving forklift` }))}
          facts={[
            {
              label: "Measured",
              value: <span className="measured">{alert.v.measurement ? `${alert.v.measurement.value.toFixed(2)} m` : "unknown"}</span>,
            },
            { label: "Glimmer", value: <VerdictPill verdict={alert.v.tier1.verdict} confidence={alert.v.tier1.score} model="" /> },
            { label: "Decision", value: decisionText(alert.v) },
          ]}
          hash={passes.steps.evidence.root}
          hashLabel={`Daily root, ${passes.steps.evidence.n} records`}
          verifyHref={passes.verify_url}
        />
      )}
      {driver && excluded && (
        <EvidenceCard
          title="Operator excluded: the seated driver"
          caption={`Golden run recorded ${driver.recorded_at.slice(0, 10)}: the only person is the driver in the cab.`}
          frames={excluded.frames.map((src, i) => ({ src, alt: `Strip frame ${i + 1}: the driver seated in the forklift` }))}
          facts={[
            { label: "Glimmer", value: <VerdictPill verdict={excluded.v.tier1.verdict} confidence={excluded.v.tier1.score} model="" /> },
            { label: "Why", value: excluded.v.tier1.reason },
            { label: "Decision", value: decisionText(excluded.v) },
          ]}
          hash={driver.steps.evidence.root}
          hashLabel={`Daily root, ${driver.steps.evidence.n} records`}
          verifyHref={driver.verify_url}
        />
      )}
    </UseCaseProof>
  );
}
