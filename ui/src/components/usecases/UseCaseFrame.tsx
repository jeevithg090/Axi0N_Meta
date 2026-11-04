"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { icons } from "@/components/icons";
import { useDemoQuery } from "@/lib/stream";

import { LiveStatusChip } from "./LiveStatusChip";
import { UseCaseStill } from "./UseCaseStill";
import { WORLDS } from "./worlds";
import styles from "./usecases.module.css";

/** The world a /usecases/<slug> path belongs to, or null. */
export function worldOf(pathname: string) {
  const slug = pathname.split("/")[2];
  return WORLDS.find((w) => w.slug === slug) ?? null;
}

/**
 * The shared use-case frame: the world's promise, the three-world switcher, the live status and
 * a still from the footage it ran on. Each page tells its own journey with real data below it.
 */
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
        <div className={styles.heroSplit}>
          <div className={styles.heroBody}>
            <h1 className={styles.title}>{world ? world.promise : "One engine, three worlds"}</h1>
            <p className={styles.line}>
              {world ? world.line : "The same planner, Rule CI and verifier, pointed at three kinds of site."}
            </p>
          </div>
          {world && <UseCaseStill world={world} caption="full" priority className={styles.heroStill} />}
        </div>
      </header>
      <div className={styles.body}>{children}</div>
    </div>
  );
}
