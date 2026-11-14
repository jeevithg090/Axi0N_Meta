import type { ReactNode } from "react";

import styles from "./StatusPage.module.css";

/** A short full-page message (error or not found) with a row of actions. */
export function StatusPage({
  eyebrow,
  title,
  body,
  children,
}: {
  eyebrow: string;
  title: string;
  body: string;
  children: ReactNode;
}) {
  return (
    <section
      style={{
        maxWidth: "40rem",
        margin: "0 auto",
        padding: "var(--space-2xl) 16px",
        display: "grid",
        gap: "var(--space-lg)",
      }}
    >
      <p style={{ color: "var(--color-muted)", fontSize: "var(--text-sm)", margin: 0 }}>
        {eyebrow}
      </p>
      <h1 style={{ fontSize: "var(--text-section)", lineHeight: 1.1, margin: 0 }}>{title}</h1>
      <p style={{ fontSize: "var(--text-lead)", margin: 0 }}>{body}</p>
      <nav
        aria-label="Where to go next"
        className={styles.actions}
        style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-lg)" }}
      >
        {children}
      </nav>
    </section>
  );
}
