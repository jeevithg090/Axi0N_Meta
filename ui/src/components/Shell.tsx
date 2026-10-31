"use client";

import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";

import { isDemo, useStream } from "@/lib/stream";

import { ModeBadge } from "./ModeBadge";
import { RelayHud } from "./RelayHud";

const LINKS = [
  { href: "/planner", label: "Planner" },
  { href: "/plans", label: "Plans" },
  { href: "/monitor", label: "Live monitor" },
];

/** Every screen sits in this shell: title, LIVE/REPLAY badge and the relay HUD strip (§5.7). */
export function Shell({ children }: { children: ReactNode }) {
  const state = useStream();
  const [query, setQuery] = useState("");
  useEffect(() => setQuery(isDemo() ? "?demo=1" : ""), []);
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
        <ModeBadge state={state} />
      </header>
      <RelayHud relay={state.relay} />
      <main className="main">{children}</main>
    </>
  );
}
