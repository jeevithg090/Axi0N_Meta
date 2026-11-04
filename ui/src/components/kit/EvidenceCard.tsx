"use client";

import { useState, type CSSProperties, type ReactNode } from "react";

import { icons } from "../icons";
import styles from "./kit.module.css";
import { cx } from "./cx";

export interface EvidenceFrame {
  src: string;
  alt?: string;
}

export interface EvidenceCardProps {
  /** The strip's frames, oldest first (URLs, or `{src, alt}`). Empty shows why there are none. */
  frames: (string | EvidenceFrame)[];
  /** The record's SHA-256 (or any hash); shown shortened, copied in full. */
  hash?: string | null;
  /** What the hash is: "Record hash", "Daily root". */
  hashLabel?: string;
  /** The browser verify page for this record or day. */
  verifyHref?: string;
  /** A short title, e.g. the rule id or the event. */
  title?: ReactNode;
  /** One line under the frames: what the strip shows. */
  caption?: ReactNode;
  /** Facts as label and value pairs: camera, time, measurement. Values are set as measured. */
  facts?: { label: string; value: ReactNode }[];
  /** Said in place of the strip when there are no frames. */
  emptyText?: string;
  className?: string;
}

/** A hash shortened to its first ten and last six characters. */
export function shortHash(hash: string): string {
  return hash.length > 20 ? `${hash.slice(0, 10)}…${hash.slice(-6)}` : hash;
}

/** One piece of evidence: the frame strip, what it shows, and the hash that pins it. */
export function EvidenceCard({
  frames,
  hash,
  hashLabel = "Record hash",
  verifyHref,
  title,
  caption,
  facts,
  emptyText = "No frames are published for this record.",
  className,
}: EvidenceCardProps) {
  const [copied, setCopied] = useState<"idle" | "done" | "failed">("idle");
  const list = frames.map((f) => (typeof f === "string" ? { src: f } : f));
  const style = { "--frame-count": Math.max(1, Math.min(list.length, 4)) } as CSSProperties;

  const copy = async () => {
    if (!hash) return;
    try {
      await navigator.clipboard.writeText(hash);
      setCopied("done");
    } catch {
      setCopied("failed");
    }
  };

  return (
    <figure className={cx(styles.evidence, className)} data-testid="evidence-card">
      <div className={styles.frames} style={style}>
        {list.length === 0 ? (
          <p className={styles.framesEmpty}>{emptyText}</p>
        ) : (
          list.map((f, i) => (
            <div key={`${f.src}-${i}`} className={styles.frame}>
              {/* eslint-disable-next-line @next/next/no-img-element -- strips are plain files, any size */}
              <img src={f.src} alt={f.alt ?? `Frame ${i + 1} of ${list.length}`} loading="lazy" width={320} height={240} />
              <span className={styles.frameIndex} aria-hidden="true">
                {i + 1}
              </span>
            </div>
          ))
        )}
      </div>
      <figcaption className={styles.evidenceBody}>
        {title && <p className={styles.evidenceTitle}>{title}</p>}
        {caption && <p className={styles.evidenceCaption}>{caption}</p>}
        {facts && facts.length > 0 && (
          <dl className={styles.evidenceMeta}>
            {facts.map((f) => (
              <div key={f.label} style={{ display: "contents" }}>
                <dt>{f.label}</dt>
                <dd>{f.value}</dd>
              </div>
            ))}
          </dl>
        )}
        {(hash || verifyHref) && (
          <div className={styles.hashRow}>
            {hash && (
              <>
                <span className={styles.hashLabel}>{hashLabel}</span>
                <code className={styles.hash} title={hash} data-testid="evidence-hash">
                  {shortHash(hash)}
                </code>
              </>
            )}
            <span className={styles.hashActions}>
              {hash && (
                <button type="button" className={styles.smallBtn} onClick={() => void copy()}>
                  {copied === "done" ? "Copied" : copied === "failed" ? "Copy failed" : "Copy hash"}
                </button>
              )}
              {verifyHref && (
                <a className={styles.smallBtn} href={verifyHref}>
                  {icons.shield}
                  Verify
                </a>
              )}
            </span>
          </div>
        )}
      </figcaption>
    </figure>
  );
}
