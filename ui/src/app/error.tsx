"use client";

import Link from "next/link";

import { StatusPage } from "./StatusPage";

/** Route-level error boundary: a page that throws shows this instead of a blank screen. */
export default function RouteError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <StatusPage
      eyebrow="Something went wrong"
      title="This page could not load."
      body={`${error.digest ? `Reference ${error.digest}. ` : ""}The rest of Rulecam still works. Try again, or go back to the start page. If the API is not running, the live pages have nothing to show; the recorded replay works without it.`}
    >
      <button type="button" onClick={reset}>
        Try again
      </button>
      <Link href="/">Start page</Link>
      <Link href="/demo?demo=1">Recorded replay</Link>
    </StatusPage>
  );
}
