import type { CSSProperties, ReactNode } from "react";

import styles from "./kit.module.css";
import { cx } from "./cx";

export interface Zone {
  /** Stable key; defaults to the name. */
  id?: string;
  /** The zone as the site names it: "Gate A", "Stage front". */
  name: string;
  /** The measured density in `unit`; `null` when the zone has no reading yet. */
  density: number | null;
}

export interface ZoneDensityBarProps {
  zones: Zone[];
  /** The unit of `density`. SAM 3.1's person tracks give people per m². */
  unit?: string;
  /** The density the site set as its limit; drawn as a line and turns a bar red at or past it. */
  limit?: number | null;
  /** An earlier warning level; turns a bar amber at or past it. */
  warnAt?: number | null;
  /** The scale's top; defaults to the larger of 1.25 × limit and the highest reading. */
  max?: number;
  /** A heading line above the bars. */
  title?: ReactNode;
  /** Extra content on the right of the heading (a LiveBadge, a time). */
  aside?: ReactNode;
  className?: string;
}

type Tone = "ok" | "warn" | "bad" | "none";

function toneOf(d: number | null, warnAt?: number | null, limit?: number | null): Tone {
  if (d === null) return "none";
  if (typeof limit === "number" && d >= limit) return "bad";
  if (typeof warnAt === "number" && d >= warnAt) return "warn";
  return "ok";
}

/** Density per zone against the site's own limit: one bar a zone, the limit as a line. */
export function ZoneDensityBar({
  zones,
  unit = "people/m²",
  limit,
  warnAt,
  max,
  title,
  aside,
  className,
}: ZoneDensityBarProps) {
  const readings = zones.map((z) => z.density ?? 0);
  const top = max ?? Math.max(typeof limit === "number" ? limit * 1.25 : 0, ...readings, 1);
  const pct = (v: number) => `${Math.min(100, Math.max(0, (v / top) * 100))}%`;
  return (
    <figure className={cx(styles.zones, className)} data-testid="zone-density">
      {(title || aside) && (
        <figcaption className={styles.zonesHead}>
          <span>{title}</span>
          {aside}
        </figcaption>
      )}
      <ul className={styles.zonesList}>
        {zones.map((z) => {
          const tone = toneOf(z.density, warnAt, limit);
          const scale = z.density === null ? 0 : Math.min(1, Math.max(0, z.density / top));
          const fill = { transform: `scaleX(${scale})` } as CSSProperties;
          const said =
            z.density === null
              ? `${z.name}: no reading`
              : `${z.name}: ${z.density.toFixed(1)} ${unit}${typeof limit === "number" ? `, limit ${limit}` : ""}`;
          return (
            <li key={z.id ?? z.name} className={styles.zone} data-tone={tone}>
              <span className={styles.zoneName} title={z.name}>
                {z.name}
              </span>
              <span className={styles.track} role="img" aria-label={said}>
                <span className={styles.fill} data-tone={tone} style={fill} />
                {typeof warnAt === "number" && <span className={styles.warnMark} style={{ left: pct(warnAt) }} />}
                {typeof limit === "number" && <span className={styles.limit} style={{ left: pct(limit) }} />}
              </span>
              <span className={styles.zoneValue} data-tone={tone}>
                {z.density === null ? "no reading" : `${z.density.toFixed(1)} ${unit}`}
              </span>
            </li>
          );
        })}
      </ul>
      {(typeof limit === "number" || typeof warnAt === "number") && (
        <p className={styles.zonesLegend}>
          {typeof limit === "number" && (
            <span className={styles.legendKey}>
              <span className={styles.legendLimit} aria-hidden="true" />
              {`Limit ${limit} ${unit}`}
            </span>
          )}
          {typeof warnAt === "number" && (
            <span className={styles.legendKey}>
              <span className={styles.legendWarn} aria-hidden="true" />
              {`Warning ${warnAt} ${unit}`}
            </span>
          )}
        </p>
      )}
    </figure>
  );
}
