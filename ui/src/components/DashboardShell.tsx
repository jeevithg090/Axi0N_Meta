"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { GOLDEN_RUNS } from "@/fixtures/golden";
import { useAuth, type AuthPhase } from "@/lib/auth";
import { API_PROXY } from "@/lib/config";
import { gpuLine, healthBadge, HealthProvider, useSharedHealth, type HealthLoad, type SystemBadge } from "@/lib/health";
import { useDemo, useDemoQuery, useStream } from "@/lib/stream";
import type { UiState } from "@/lib/store";
import { useTheme } from "@/lib/theme";

import { icons, RulecamMark, type IconName } from "./icons";
import { ModeBadge } from "./ModeBadge";

/**
 * A nav destination. A static badge is only a statement that is always true there ("REPLAY" on
 * the golden replay, "SAMPLE" on a screen of hand-written sample content); live counts come from
 * the API in `liveBadge`, and an unknown count shows no badge rather than a made-up one.
 */
interface NavItem {
  href: string;
  label: string;
  icon: IconName;
  badge?: string;
  badgeTone?: "replay" | "sample" | "count";
  /** Leaves the app (a static page with its own query); the demo query is not appended. */
  external?: boolean;
}

interface NavGroup {
  group: string;
  items: NavItem[];
}

/** What the shell knows from the API; `null` means unknown (not reachable, or not allowed). */
interface LiveStats {
  plansCount: number | null;
  totalIncidents: number | null;
  cameras: number | null;
}

const UNKNOWN_STATS: LiveStats = {
  plansCount: null,
  totalIncidents: null,
  cameras: null,
};

const VERIFY_URL = GOLDEN_RUNS[0]?.verify_url ?? "/verify/";

/** One engine, three worlds: the use cases first, then build, run and trust. */
export const NAV_GROUPS: NavGroup[] = [
  {
    group: "Use cases",
    items: [
      { href: "/usecases/supermarket", label: "Supermarket", icon: "cart" },
      { href: "/usecases/warehouse", label: "Warehouse", icon: "forklift" },
      { href: "/usecases/crowd", label: "Crowd", icon: "crowd" },
    ],
  },
  {
    group: "Build",
    items: [
      { href: "/planner", label: "Planner", icon: "pen" },
      { href: "/plans", label: "Plans", icon: "file", badgeTone: "count" },
      { href: "/ci", label: "Rule CI", icon: "check" },
      { href: "/extensions", label: "Extensions", icon: "code" },
    ],
  },
  {
    group: "Run",
    items: [
      { href: "/monitor", label: "Monitor", icon: "monitor", badge: "SAMPLE", badgeTone: "sample" },
      { href: "/streams", label: "Camera fleet", icon: "video", badgeTone: "count" },
      { href: "/alerts", label: "Alerts", icon: "bell" },
      { href: "/ask", label: "Ask", icon: "chat" },
      { href: "/advice", label: "Advice", icon: "bars" },
      { href: "/site", label: "Site tools", icon: "globe" },
    ],
  },
  {
    group: "Trust",
    items: [
      { href: "/demo", label: "Golden replay", icon: "play", badge: "REPLAY", badgeTone: "replay" },
      { href: VERIFY_URL, label: "Verify evidence", icon: "shield", external: true },
      { href: "/incidents", label: "Incidents", icon: "alert", badgeTone: "count" },
      { href: "/worker", label: "Worker page", icon: "qr" },
      { href: "/audit", label: "Audit", icon: "hash" },
    ],
  },
  {
    group: "System",
    items: [{ href: "/system", label: "System and GPUs", icon: "cpu" }],
  },
];

/** The live count shown on a nav item, or `undefined` when there is none or it is unknown. */
function liveBadge(href: string, stats: LiveStats): string | undefined {
  if (href === "/plans" && stats.plansCount !== null) return String(stats.plansCount);
  if (href === "/incidents" && stats.totalIncidents !== null) return String(stats.totalIncidents);
  if (href === "/streams" && stats.cameras !== null) return String(stats.cameras);
  return undefined;
}

/**
 * The header badge: REPLAY on a golden replay; otherwise what `GET /health` says. The event
 * socket is an enhancement only: it may fill the badge while /health has not answered yet, and it
 * never makes the badge OFFLINE.
 */
export function shellBadge(demo: boolean | null, health: HealthLoad, stream: UiState): SystemBadge {
  if (demo) return "REPLAY";
  const fromHealth = healthBadge(health);
  if (fromHealth === "…" && stream.connection === "open" && stream.badge) return stream.badge;
  return fromHealth;
}

/** The badge this page shows, from the shared /health poll. */
function useShellBadge(): SystemBadge {
  const demo = useDemo();
  const stream = useStream();
  const health = useSharedHealth(demo === false);
  return shellBadge(demo, health, stream);
}

const PHONE_QUERY = "(max-width: 760px)";

/** Whether the viewport is phone-sized (the width at which `.shell-sidebar` becomes a drawer). */
function isPhone(): boolean {
  return typeof window !== "undefined" && (window.matchMedia?.(PHONE_QUERY)?.matches ?? false);
}

/**
 * Routes for people outside the owner's console, framed bare (the wordmark and the badge, no
 * sidebar): the worker QR page, the station tablet, the staff companion PWA, and the proof
 * portal's inspector view for outside recipients.
 */
const BARE_ROUTES: RegExp[] = [
  /^\/worker\/[^/]+\/[^/]+/,
  /^\/station\//,
  /^\/staff(\/|$)/,
  /^\/inspector\/view(\/|$)/,
];

/** Which frame a route sits in: the landing page, a bare outside-facing screen, or the app. */
export function chromeFor(pathname: string): "page" | "bare" | "app" {
  if (pathname === "/") return "page";
  if (BARE_ROUTES.some((route) => route.test(pathname))) return "bare";
  return "app";
}

function isActive(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}

/**
 * The header's page name: the group and nav label of the current page, or `null` where no
 * breadcrumb belongs (the landing page and the golden replay, which carry their own titles).
 */
function pageName(pathname: string, mounted: boolean): { group: string; label: string } | null {
  if (pathname === "/" || pathname === "/demo" || pathname.startsWith("/demo/")) return null;
  for (const group of NAV_GROUPS) {
    for (const item of group.items) {
      if (!item.external && isActive(pathname, item.href)) return { group: group.group, label: item.label };
    }
  }
  // A route outside the nav (a 404 among them) is named from the URL only after mount: the
  // prerendered not-found page has another path, and naming it there breaks hydration (QA-B17).
  const first = pathname.split("/").filter(Boolean)[0];
  return mounted && first ? { group: "Rulecam", label: first.replace(/-/g, " ") } : null;
}

/** The session line in the sidebar footer, from the real auth state (no made-up user). */
function sessionLabel(phase: AuthPhase): string {
  switch (phase) {
    case "owner":
      return "Owner session";
    case "visitor":
      return "Visitor · read-only";
    case "demo":
      return "Replay, no login";
    case "offline":
      return "API not reachable";
    default:
      return "Checking…";
  }
}

/** Light and dark, as two pressed-state buttons. */
export function ThemeSwitch({ compact = false }: { compact?: boolean }) {
  const { isDark, setTheme } = useTheme();
  return (
    <div className="theme-switch" role="group" aria-label="Colour theme">
      <button type="button" aria-pressed={!isDark} onClick={() => setTheme("light")} aria-label="Light theme">
        {icons.sun}
        {!compact && <span className="theme-word">Light</span>}
      </button>
      <button type="button" aria-pressed={isDark} onClick={() => setTheme("dark")} aria-label="Dark theme">
        {icons.moon}
        {!compact && <span className="theme-word">Dark</span>}
      </button>
    </div>
  );
}

/** The wordmark, linking home. */
function Wordmark({ query, compact = false }: { query: string; compact?: boolean }) {
  return (
    <Link href={`/${query}`} className="wordmark" aria-label="Rulecam home">
      <RulecamMark className="wordmark-mark" />
      {!compact && <span>Rulecam</span>}
    </Link>
  );
}

/** The landing page's bar: the wordmark, four anchors on the page, the badge and one action. */
function SiteHeader() {
  const badge = useShellBadge();
  const demoQuery = useDemoQuery();
  return (
    <header className="site-header">
      <div className="container site-header-in">
        <Wordmark query={demoQuery} />
        <nav className="site-nav" aria-label="On this page">
          <a href="#how">How it works</a>
          <a href="#worlds">Use cases</a>
          <a href="#models">Meta models</a>
          <a href="#measured">Results</a>
        </nav>
        <div className="site-header-tools">
          <ModeBadge label={badge} />
          <ThemeSwitch compact />
          <Link href={`/monitor${demoQuery}`} className="btn only-desktop">
            Open the console
          </Link>
        </div>
      </div>
    </header>
  );
}

export function DashboardShell({ children }: { children: React.ReactNode }) {
  // One /health poll for the page, never on a golden replay (nor before the URL has been read).
  const demo = useDemo();
  return (
    <HealthProvider enabled={demo === false}>
      <ShellFrame>{children}</ShellFrame>
    </HealthProvider>
  );
}

function ShellFrame({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const demo = useDemo();
  const badge = useShellBadge();
  const health = useSharedHealth(demo === false);
  const auth = useAuth();
  const demoQuery = useDemoQuery();
  const chrome = chromeFor(pathname);
  const [collapsedPref, setCollapsed] = useState<boolean>(false);
  const [mounted, setMounted] = useState<boolean>(false);
  useEffect(() => setMounted(true), []);
  // On a phone the sidebar is a drawer over the page, open or closed; an open drawer always
  // shows the full labels.
  const [mobileOpen, setMobileOpen] = useState<boolean>(false);
  const collapsed = collapsedPref && !mobileOpen;
  const [liveStats, setLiveStats] = useState<LiveStats>(UNKNOWN_STATS);

  // A new page closes the phone drawer.
  useEffect(() => {
    setMobileOpen(false);
  }, [pathname]);

  // Live telemetry from the API through the UI's proxy. A failed or forbidden call reads as
  // unknown (never a remembered or made-up value), and a golden replay polls nothing: what the
  // audience sees there is the recording, labelled REPLAY by the mode badge. The landing page
  // and the bare screens poll for themselves or not at all.
  useEffect(() => {
    if (demo !== false || chrome !== "app") {
      setLiveStats(UNKNOWN_STATS);
      return undefined;
    }
    let active = true;
    const json = async <T,>(path: string): Promise<T | null> => {
      try {
        const res = await fetch(`${API_PROXY}${path}`, { cache: "no-store" });
        return res.ok ? ((await res.json()) as T) : null;
      } catch {
        return null;
      }
    };
    const poll = async () => {
      const [p, inc, cams] = await Promise.all([
        json<unknown[]>("/plans"),
        json<unknown[]>("/incidents"),
        json<unknown[]>("/cameras"),
      ]);
      if (!active) return;
      setLiveStats({
        plansCount: p ? p.length : null,
        totalIncidents: inc ? inc.length : null,
        cameras: cams ? cams.length : null,
      });
    };
    void poll();
    const id = window.setInterval(() => void poll(), 6000);
    return () => {
      active = false;
      window.clearInterval(id);
    };
  }, [demo, chrome]);

  // Keyboard shortcut: Ctrl+B (or Cmd+B) toggles the sidebar.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "b") {
        e.preventDefault();
        setCollapsed((c) => !c);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (chrome === "page") {
    return (
      <div className="app" data-chrome="page">
        <SiteHeader />
        <main className="shell-main">{children}</main>
      </div>
    );
  }

  if (chrome === "bare") {
    return (
      <div className="app" data-chrome="bare">
        <header className="bare-header">
          <Wordmark query={demoQuery} />
          <ModeBadge label={badge} />
        </header>
        <main className="shell-main">{children}</main>
      </div>
    );
  }

  const crumb = pageName(pathname, mounted);
  const gpus = health.phase === "ok" ? health.health.gpus : undefined;
  const samGpu = gpus ? gpuLine(gpus.sam).state : "unknown";
  const glimmerGpu = gpus ? gpuLine(gpus.glimmer).state : "unknown";
  const toggleNav = () => (isPhone() ? setMobileOpen((o) => !o) : setCollapsed((c) => !c));

  return (
    <div className="app root" data-chrome="app">
      <aside
        aria-label="Rulecam navigation"
        className="shell-sidebar"
        data-collapsed={collapsed ? "true" : "false"}
        data-mobile-open={mobileOpen ? "true" : "false"}
      >
        <div className="side-head">
          <Wordmark query={demoQuery} compact={collapsed} />
          {!collapsed && (
            <button
              type="button"
              className="icon-btn"
              onClick={() => (isPhone() ? setMobileOpen(false) : setCollapsed(true))}
              aria-label={mobileOpen ? "Close navigation" : "Collapse sidebar"}
              title="Collapse sidebar (Ctrl+B)"
            >
              {mobileOpen ? icons.close : icons.sidebar}
            </button>
          )}
        </div>

        <nav aria-label="Primary navigation" className="sidebar-nav">
          {NAV_GROUPS.map((grp) => (
            <div key={grp.group} className="nav-group">
              <p className="nav-group-label" id={`nav-${grp.group.replace(/\s+/g, "-").toLowerCase()}`}>
                {grp.group}
              </p>
              <ul className="nav-list" aria-labelledby={`nav-${grp.group.replace(/\s+/g, "-").toLowerCase()}`}>
                {grp.items.map((item) => {
                  const active = !item.external && isActive(pathname, item.href);
                  const badge = liveBadge(item.href, liveStats) ?? item.badge;
                  const href = item.external ? item.href : `${item.href}${demoQuery}`;
                  return (
                    <li key={item.href}>
                      <Link
                        href={href}
                        className="nav-link"
                        aria-current={active ? "page" : undefined}
                        title={collapsed ? `${item.label}${badge ? ` (${badge})` : ""}` : undefined}
                        aria-label={collapsed ? item.label : undefined}
                      >
                        {icons[item.icon]}
                        <span className="nav-label">{item.label}</span>
                        {badge && (
                          <span className="nav-badge" data-tone={item.badgeTone ?? "count"}>
                            {badge}
                          </span>
                        )}
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </nav>

        <div className="side-foot">
          <dl className="gpu-list" aria-label="GPU state from the API">
            <div className="gpu-row">
              <dt>SAM 3.1 GPU</dt>
              <dd data-state={samGpu}>{samGpu}</dd>
            </div>
            <div className="gpu-row">
              <dt>Glimmer GPU</dt>
              <dd data-state={glimmerGpu}>{glimmerGpu}</dd>
            </div>
          </dl>
          <ThemeSwitch compact={collapsed} />
          <div className="session-line">
            <span>{sessionLabel(auth.phase)}</span>
            {auth.phase === "visitor" && <Link href="/login">Log in</Link>}
          </div>
        </div>
      </aside>
      {mobileOpen && (
        <button
          type="button"
          className="shell-backdrop"
          aria-label="Close navigation"
          onClick={() => setMobileOpen(false)}
        />
      )}

      <div className="shell-main-col">
        <header className="shell-header">
          <div className="shell-header-left">
            <button
              type="button"
              className="icon-btn"
              onClick={toggleNav}
              aria-label="Toggle navigation"
              aria-expanded={mobileOpen}
              title="Toggle sidebar (Ctrl+B)"
            >
              {icons.menu}
            </button>
            {crumb && (
              <div className="shell-crumb">
                <span className="shell-crumb-root">{crumb.group}</span>
                <span className="shell-crumb-sep" aria-hidden="true">
                  /
                </span>
                <strong>{crumb.label}</strong>
              </div>
            )}
          </div>

          <div className="shell-header-right">
            <ModeBadge label={badge} />
            <span className="shell-cameras" data-testid="shell-cameras">
              Cameras <b>{liveStats.cameras ?? "—"}</b>
            </span>
            <Link href={`/planner${demoQuery}`} className="btn primary">
              {icons.plus}
              New rule
            </Link>
          </div>
        </header>

        <main className="shell-main">{children}</main>
      </div>
    </div>
  );
}
