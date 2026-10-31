"use client";

import Link from "next/link";
import type { ReactNode } from "react";

import { useStream } from "@/lib/stream";

import { ModeBadge } from "./ModeBadge";
import { RelayHud } from "./RelayHud";

/** Every screen sits in this shell: title, LIVE/REPLAY badge and the relay HUD strip (§5.7). */
export function Shell({ children }: { children: ReactNode }) {
  const state = useStream();
  return (
    <>
      <header className="shell-header">
        <Link href="/" className="brand">
          Rulecam
        </Link>
        <nav className="nav">
          <Link href="/monitor">Live monitor</Link>
        </nav>
        <ModeBadge state={state} />
      </header>
      <RelayHud relay={state.relay} />
      <main className="main">{children}</main>
    </>
  );
}
