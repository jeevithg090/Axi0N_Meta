import type { Metadata } from "next";
import Link from "next/link";

import { StatusPage } from "./StatusPage";

export const metadata: Metadata = { title: "Page not found · Rulecam" };

/** The 404 page for any address the site does not serve. */
export default function NotFound() {
  return (
    <StatusPage
      eyebrow="404"
      title="There is no page at this address."
      body="The link may be old, or the address may have a typo."
    >
      <Link href="/">Start page</Link>
      <Link href="/demo?demo=1">Recorded replay</Link>
    </StatusPage>
  );
}
