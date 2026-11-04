import type { World } from "./worlds";
import styles from "./usecases.module.css";

/**
 * One frame of the footage a world was run on, in black and white, with where it came from said
 * underneath: `short` for the landing rows, the full credit on the use-case page.
 */
export function UseCaseStill({
  world,
  caption = "short",
  priority = false,
  className,
}: {
  world: World;
  caption?: "short" | "full";
  priority?: boolean;
  className?: string;
}) {
  const { still } = world;
  return (
    <figure className={`${styles.still} ${className ?? ""}`} data-testid={`still-${world.slug}`}>
      {/* eslint-disable-next-line @next/next/no-img-element -- a fixed 1200×750 still in public/ */}
      <img
        src={still.src}
        alt={still.alt}
        width={1200}
        height={750}
        loading={priority ? "eager" : "lazy"}
        fetchPriority={priority ? "high" : "auto"}
        decoding="async"
      />
      <figcaption>{caption === "full" ? still.credit : still.label}</figcaption>
    </figure>
  );
}
