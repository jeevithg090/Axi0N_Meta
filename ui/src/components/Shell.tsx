"use client";

import Link from "next/link";
import type { ReactNode } from "react";

import { useAuth } from "@/lib/auth";
import { useDemoQuery, useStream } from "@/lib/stream";

import { CostBadge, useRuntimeMetrics } from "./CostBadge";
import { ModeBadge } from "./ModeBadge";
import { RelayHud } from "./RelayHud";
import { VerifierBadge } from "./VerifierBadge";

const LINKS = [
  { href: "/demo", label: "Golden run" },
  { href: "/planner", label: "Planner" },
  { href: "/plans", label: "Plans" },
  { href: "/monitor", label: "Live monitor" },
  { href: "/advice", label: "Advice" },
];

/** Owner state in the header: a login link for visitors, log out for a session. */
export function OwnerControl() {
  const auth = useAuth();
  if (auth.phase === "owner" && auth.via === "session") {
    return (
      <button type="button" className="owner" onClick={() => void auth.logout()}>
        Owner · Log out
      </button>
    );
  }
  if (auth.phase === "owner") return <span className="pill">Owner (this machine)</span>;
  if (auth.phase === "visitor" || auth.phase === "offline") {
    return (
      <Link href="/login" className="owner">
        Owner login
      </Link>
    );
  }
  return null;
}

/** Every screen sits in this shell: title, LIVE/REPLAY badge and the relay HUD strip (§5.7). */
export function Shell({ children }: { children: ReactNode }) {
  const state = useStream();
  const metrics = useRuntimeMetrics();
  const query = useDemoQuery();
  return (
    <>
      <header className="shell-header">
        <Link href={`/${query}`} className="brand">
          Rulecam
        </Link>
        <nav className="nav">
          {LINKS.map((link) => (
            <Link key={link.href} href={`${link.href}${query}`}>
              {link.label}
            </Link>
          ))}
        </nav>
        <OwnerControl />
        <VerifierBadge metrics={metrics} />
        <CostBadge cost={metrics?.cost ?? null} />
        <ModeBadge state={state} />
      </header>
      <RelayHud relay={state.relay} />
      <main className="main">{children}</main>
    </>
  );
}
