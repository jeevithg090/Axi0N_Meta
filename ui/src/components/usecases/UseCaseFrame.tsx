"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { icons } from "@/components/icons";
import { StepFlow, type FlowStep } from "@/components/kit";
import { useDemoQuery } from "@/lib/stream";

import { LiveStatusChip } from "./LiveStatusChip";
import { WORLDS, type WorldSlug } from "./worlds";
import styles from "./usecases.module.css";

/**
 * The journey for each world, step by step, in the project's own facts (SUBMISSION.md's model
 * map and docs/DEMO.md). A use-case page shows the live data; this frame says what it is.
 */
export const JOURNEYS: Record<WorldSlug, FlowStep[]> = {
  supermarket: [
    { name: "Describe", text: "Ask about the floor in your own words, typed or spoken.", models: ["Muse Voice Transcribe"] },
    { name: "Plan", text: "Muse Spark picks the cameras, the window and the queries to run.", models: ["Muse Spark 1.3"] },
    { name: "Prove", text: "It can watch the clip of the window it chose, and shows every step it took.", models: ["Muse Spark 1.2"] },
    { name: "Trust", text: "Each answer shows the events, tables and frames it came from.", models: ["Muse Glimmer 30B"] },
  ],
  warehouse: [
    { name: "Describe", text: "“Keep people at least 2 metres away from a moving forklift.”", models: ["Muse Spark 1.3"] },
    { name: "Plan", text: "SAM 3.1 surveys the aisle and calibrates metres from a pallet.", models: ["SAM 3.1"] },
    { name: "Prove", text: "Rule CI attacks the rule with boundary cases and broken copies.", models: [{ model: "Rule CI", meta: false }] },
    { name: "Trust", text: "Glimmer checks every alert on the frames; the decision is hash-chained.", models: ["Muse Glimmer 30B"] },
  ],
  crowd: [
    { name: "Describe", text: "Say where crowding worries you: a gate, a stage front, an aisle.", models: ["Muse Spark 1.3"] },
    { name: "Plan", text: "SAM 3.1 person tracks become people per square metre in each zone.", models: ["SAM 3.1"] },
    { name: "Prove", text: "Muse Spark watches the clip of each crowded spell.", models: ["Muse Spark 1.2"] },
    { name: "Trust", text: "Counts only, never identities: no face is ever recognised." },
  ],
};

/** The world a /usecases/<slug> path belongs to, or null. */
export function worldOf(pathname: string) {
  const slug = pathname.split("/")[2];
  return WORLDS.find((w) => w.slug === slug) ?? null;
}

/** The shared use-case frame: the world's promise, the three-world switcher, live status, the journey. */
export function UseCaseFrame({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const query = useDemoQuery();
  const world = worldOf(pathname);

  return (
    <div className={styles.frame} data-testid="usecase-frame" data-world={world?.slug ?? "none"}>
      <header className={styles.hero}>
        <div className={styles.heroTop}>
          <nav aria-label="Use cases" className={styles.switcher}>
            {WORLDS.map((w) => (
              <Link
                key={w.slug}
                href={`/usecases/${w.slug}${query}`}
                className={styles.switch}
                aria-current={world?.slug === w.slug ? "page" : undefined}
              >
                {icons[w.icon]}
                {w.name}
              </Link>
            ))}
          </nav>
          <LiveStatusChip />
        </div>
        <div className={styles.heroBody}>
          <h1 className={styles.title}>{world ? world.promise : "One engine, three worlds"}</h1>
          <p className={styles.line}>
            {world ? world.line : "The same planner, Rule CI and verifier, pointed at three kinds of site."}
          </p>
        </div>
        {world && (
          <StepFlow
            steps={JOURNEYS[world.slug]}
            label={`How Rulecam works for a ${world.name.toLowerCase()}`}
            size="compact"
            as="h2"
            className={styles.journey}
          />
        )}
      </header>
      <div className={styles.body}>{children}</div>
    </div>
  );
}
