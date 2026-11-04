"use client";

// The recipient's page: no login, read-only. The link's token is the permission; the browser
// checks the pack itself (VerifyPanel) before anyone has to trust a number on it.

import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { ApiError } from "@/lib/api";

import { PackView } from "../_components/PackView";
import { VerifyPanel } from "../_components/VerifyPanel";
import { getShared, sharedDocumentUrl } from "../_lib/api";
import type { SharedPack } from "../_lib/types";
import styles from "../inspector.module.css";

function SharedView() {
  const token = useSearchParams().get("t");
  const [shared, setShared] = useState<SharedPack | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) {
      setError("This page needs the link the site shared with you.");
      return;
    }
    getShared(token)
      .then(setShared)
      .catch((e: unknown) =>
        setError(e instanceof ApiError ? e.message : "The pack could not be loaded."),
      );
  }, [token]);

  if (error) return <p className={styles.bannerBad}>✗ {error}</p>;
  if (!shared) return <p className={styles.banner}>Loading the proof pack…</p>;
  return (
    <>
      <h1>{shared.pack.title}</h1>
      <VerifyPanel shared={shared} token={token} />
      <p className={styles.muted}>
        Pack hash <span className={styles.mono}>{shared.pack_hash}</span> ·{" "}
        {token ? <a href={sharedDocumentUrl(token, "pdf")}>Download PDF</a> : null}
      </p>
      <PackView pack={shared.pack} />
    </>
  );
}

export default function InspectorViewPage() {
  return (
    <main className={styles.page}>
      <p className={styles.muted}>Rulecam proof pack · read-only · checked in your browser</p>
      <Suspense fallback={<p className={styles.banner}>Loading…</p>}>
        <SharedView />
      </Suspense>
    </main>
  );
}
