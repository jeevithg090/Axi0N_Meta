"use client";

// What the supermarket story proved on 4 Nov, read from the live report: the cross-count that
// fixed Ask's over-count, and a spill seen and cleared in 1.9 minutes (its photo is a SAMPLE edit).

import { LiveBadge, Timeline, TimelineItem } from "@/components/kit";
import { UseCaseProof } from "@/components/usecases/UseCaseProof";
import { REPO_URL } from "@/fixtures/live-campaign";

import { CROSS_COUNT, HAZARD, REPORT } from "./proofData";
import styles from "./supermarket.module.css";

export function SupermarketProof() {
  const [first] = CROSS_COUNT.frames;
  return (
    <UseCaseProof
      id="sm-proof"
      testId="supermarket-proof"
      title="What one question proved"
      badge={{ mode: "replay", detail: "recorded 4 Nov 2026" }}
      reading={{
        value: first.askNow,
        unit: "people",
        struck: { value: first.tracker, label: "Ask's answer before: the tracker's count" },
        caption: `The ${CROSS_COUNT.camera} camera, one frame at ${first.at} UTC. Counted by hand: about ${first.byHand.replace("~", "")}.`,
      }}
      verdict={<p className={styles.proofLine}>Three counts of the same frame, none seeing the others:</p>}
      chips={[
        { model: "SAM 3.1", job: `${first.sam} boxes` },
        { model: "Muse Spark 1.3", job: `${first.spark} points` },
        { model: "Muse Glimmer 30B", job: `counted ${first.glimmer}` },
      ]}
      reason={`“${CROSS_COUNT.reconciled}.”`}
      footnote={`${CROSS_COUNT.error} on two busy frames.`}
      after={
        <>
          Source: <a href={`${REPO_URL}/blob/main/${REPORT}`}>{REPORT}</a>
        </>
      }
    >
      <section className={styles.proofCard} aria-labelledby="sm-hazard">
        <div className={styles.proofCardHead}>
          <h3 id="sm-hazard">A spill, seen and cleared in {HAZARD.minutes}</h3>
          <LiveBadge mode="sample" detail="the spill photo is a Muse Image edit" />
        </div>
        <Timeline label="The spill task">
          <TimelineItem time={HAZARD.seen} title="Seen on the aisle camera" tone="instrument" meta="Muse Spark found it, SAM 3.1 boxed the puddle, Glimmer agreed" />
          <TimelineItem time={HAZARD.alerted} title="Staff alerted" tone="person" meta={`Chain record ${HAZARD.detectRecord}`} />
          <TimelineItem
            time={HAZARD.cleared}
            title={`Cleared, ${HAZARD.minutes} after it was seen`}
            tone="ok"
            meta={`Proof photo checked by Spark (${HAZARD.sparkProof}) and Glimmer (${HAZARD.glimmerProof}); chain record ${HAZARD.clearedRecord}`}
          />
        </Timeline>
        <p className={styles.muted}>Chain intact that day: {HAZARD.chainRecords}.</p>
      </section>
      <section className={styles.proofCard} aria-labelledby="sm-counts">
        <div className={styles.proofCardHead}>
          <h3 id="sm-counts">The same frames, before and after</h3>
        </div>
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th scope="col">Frame (UTC)</th>
                <th scope="col">By hand</th>
                <th scope="col">Before</th>
                <th scope="col">Ask now</th>
              </tr>
            </thead>
            <tbody>
              {CROSS_COUNT.frames.map((f) => (
                <tr key={f.at}>
                  <th scope="row" className={styles.mono}>
                    {f.at}
                  </th>
                  <td className={styles.mono}>{f.byHand}</td>
                  <td className={styles.mono}>
                    <s>{f.tracker}</s>
                  </td>
                  <td className={styles.mono}>
                    <b>{f.askNow}</b>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className={styles.muted}>
          No single model was right: SAM over-counts busy views and Spark&apos;s points miss people half
          hidden; the three counts bracket the truth.
        </p>
      </section>
    </UseCaseProof>
  );
}
