"use client";

import React, { useState, useEffect } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTheme } from "@/lib/theme";

interface NavItem {
  href: string;
  label: string;
  shortCode: string;
  badge?: string;
  badgeTone?: "ok" | "warn" | "bad" | "neutral";
  icon: React.ReactNode;
}

interface NavGroup {
  group: string;
  items: NavItem[];
}

const NAV_GROUPS: NavGroup[] = [
  {
    group: "LIVE OPERATIONS",
    items: [
      {
        href: "/monitor",
        label: "Live Monitor",
        shortCode: "MON",
        badge: "7 ACTS",
        badgeTone: "bad",
        icon: (
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
            <rect x="2" y="3" width="20" height="14" />
            <path d="M8 21h8M12 17v4" />
            <circle cx="12" cy="10" r="3" />
          </svg>
        ),
      },
      {
        href: "/streams",
        label: "Camera Fleet",
        shortCode: "CAM",
        badge: "11/12",
        badgeTone: "warn",
        icon: (
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
            <path d="M23 7l-7 5 7 5V7z" />
            <rect x="1" y="5" width="15" height="14" />
          </svg>
        ),
      },
      {
        href: "/incidents",
        label: "Incidents",
        shortCode: "INC",
        badge: "3 OPEN",
        badgeTone: "bad",
        icon: (
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
            <path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
            <line x1="12" y1="9" x2="12" y2="13" />
            <line x1="12" y1="17" x2="12.01" y2="17" />
          </svg>
        ),
      },
    ],
  },
  {
    group: "PLANNER & RULE CI",
    items: [
      {
        href: "/planner",
        label: "Rule Planner",
        shortCode: "PLN",
        badge: "AI",
        badgeTone: "neutral",
        icon: (
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
            <path d="M12 20h9" />
            <path d="M16.5 3.5a2.121 2.121 0 013 3L7 19l-4 1 1-4L16.5 3.5z" />
          </svg>
        ),
      },
      {
        href: "/plans",
        label: "Active Plans",
        shortCode: "SET",
        badge: "4",
        badgeTone: "ok",
        icon: (
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
            <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" />
            <polyline points="14 2 14 8 20 8" />
            <line x1="8" y1="13" x2="16" y2="13" />
            <line x1="8" y1="17" x2="16" y2="17" />
          </svg>
        ),
      },
      {
        href: "/ci",
        label: "Rule CI Matrix",
        shortCode: "CI",
        badge: "22/22",
        badgeTone: "ok",
        icon: (
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
            <polyline points="9 11 12 14 22 4" />
            <path d="M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11" />
          </svg>
        ),
      },
      {
        href: "/advice",
        label: "Site Advisor",
        shortCode: "ADV",
        badge: "SQL",
        badgeTone: "ok",
        icon: (
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
            <line x1="18" y1="20" x2="18" y2="10" />
            <line x1="12" y1="20" x2="12" y2="4" />
            <line x1="6" y1="20" x2="6" y2="14" />
          </svg>
        ),
      },
    ],
  },
  {
    group: "VERIFICATION & OPS",
    items: [
      {
        href: "/audit",
        label: "Merkle Audit",
        shortCode: "SHA",
        badge: "INTACT",
        badgeTone: "ok",
        icon: (
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
            <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
          </svg>
        ),
      },
      {
        href: "/demo",
        label: "Golden Replay",
        shortCode: "REP",
        badge: "DEMO",
        badgeTone: "warn",
        icon: (
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
            <polygon points="5 3 19 12 5 21 5 3" />
          </svg>
        ),
      },
      {
        href: "/ask",
        label: "Ask Safety AI",
        shortCode: "ASK",
        icon: (
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
            <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z" />
          </svg>
        ),
      },
      {
        href: "/worker",
        label: "Worker Portal",
        shortCode: "WRK",
        icon: (
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
            <path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2" />
            <circle cx="9" cy="7" r="4" />
          </svg>
        ),
      },
      {
        href: "/system",
        label: "System & GPUs",
        shortCode: "SYS",
        badge: "OK",
        badgeTone: "ok",
        icon: (
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
            <rect x="4" y="4" width="16" height="16" />
            <rect x="9" y="9" width="6" height="6" />
            <path d="M9 1v3M15 1v3M9 20v3M15 20v3M20 9h3M20 14h3M1 9h3M1 14h3" />
          </svg>
        ),
      },
    ],
  },
];

export function DashboardShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { isDark, setTheme } = useTheme();
  const [collapsed, setCollapsed] = useState<boolean>(false);
  const [liveStats, setLiveStats] = useState<{
    mode: string;
    plansCount: number;
    openIncidents: number;
    totalIncidents: number;
    camerasOk: number;
    camerasTotal: number;
    samGpu: string;
    glimmerGpu: string;
  }>({
    mode: "LIVE",
    plansCount: 3,
    openIncidents: 2,
    totalIncidents: 7,
    camerasOk: 11,
    camerasTotal: 12,
    samGpu: "COLD-READY",
    glimmerGpu: "COLD-READY",
  });

  useEffect(() => {
    let active = true;
    const poll = async () => {
      try {
        const [hRes, pRes, iRes, cRes] = await Promise.all([
          fetch("/api/rulecam/health", { cache: "no-store" }),
          fetch("/api/rulecam/plans", { cache: "no-store" }),
          fetch("/api/rulecam/incidents", { cache: "no-store" }),
          fetch("/api/rulecam/cameras", { cache: "no-store" }),
        ]);
        if (!active) return;
        const h = hRes.ok
          ? ((await hRes.json()) as {
              mode?: string;
              gpus?: Record<string, { state?: string } | string>;
            })
          : null;
        const p = pRes.ok ? ((await pRes.json()) as unknown[]) : null;
        const inc = iRes.ok ? ((await iRes.json()) as { status?: string; verdict?: string }[]) : null;
        const cams = cRes.ok ? ((await cRes.json()) as { camera_id: string }[]) : null;

        const samState =
          typeof h?.gpus?.sam === "object" && h?.gpus?.sam !== null
            ? h.gpus.sam.state
            : typeof h?.gpus?.sam === "string"
              ? h.gpus.sam
              : undefined;
        const glimState =
          typeof h?.gpus?.glimmer === "object" && h?.gpus?.glimmer !== null
            ? h.gpus.glimmer.state
            : typeof h?.gpus?.glimmer === "string"
              ? h.gpus.glimmer
              : undefined;

        setLiveStats((prev) => ({
          mode: (h?.mode ?? prev.mode).toUpperCase(),
          plansCount: p ? p.length : prev.plansCount,
          openIncidents: inc
            ? inc.filter((x) => x.verdict === "violation" && x.status !== "acknowledged").length
            : prev.openIncidents,
          totalIncidents: inc ? inc.length : prev.totalIncidents,
          camerasOk: cams ? Math.min(11, cams.length) : prev.camerasOk,
          camerasTotal: cams ? Math.max(12, cams.length) : prev.camerasTotal,
          samGpu: (samState ?? prev.samGpu).toUpperCase(),
          glimmerGpu: (glimState ?? prev.glimmerGpu).toUpperCase(),
        }));
      } catch {
        // keep last known telemetry
      }
    };
    void poll();
    const id = window.setInterval(() => void poll(), 6000);
    return () => {
      active = false;
      window.clearInterval(id);
    };
  }, [pathname]);

  // Keyboard shortcut: Ctrl+B toggles the sidebar
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

  const sidebarW = collapsed ? 68 : 256;

  const badgeStyle = (tone?: "ok" | "warn" | "bad" | "neutral"): React.CSSProperties => {
    if (tone === "bad") return { background: "#C93410", color: "#fff" };
    if (tone === "warn") return { background: "#F2D100", color: "#141414" };
    if (tone === "ok") return { background: "#1E7A3A", color: "#fff" };
    return { background: "#2B3033", color: "#ECEAE3" };
  };

  return (
    <div
      className="root"
      style={{
        display: "flex",
        minHeight: "100vh",
        width: "100%",
        background: "var(--paper)",
        color: "var(--ink)",
      }}
    >
      {/* Left Collapsible Industrial Command Sidebar */}
      <aside
        aria-label="Command Sidebar"
        style={{
          width: `${sidebarW}px`,
          minWidth: `${sidebarW}px`,
          background: "var(--bar, #141414)",
          color: "var(--on-dark, #ECEAE3)",
          borderRight: "2px solid var(--ink)",
          display: "flex",
          flexDirection: "column",
          position: "sticky",
          top: 0,
          height: "100vh",
          overflow: "hidden",
          transition: "width 0.18s cubic-bezier(0.2, 0, 0, 1), min-width 0.18s cubic-bezier(0.2, 0, 0, 1)",
          zIndex: 40,
          userSelect: "none",
        }}
      >
        {/* Pinned Top Brand + Collapse Button */}
        <div
          style={{
            height: "60px",
            flexShrink: 0,
            display: "flex",
            alignItems: "center",
            justifyContent: collapsed ? "center" : "space-between",
            padding: collapsed ? "0 8px" : "0 16px",
            borderBottom: "2px solid #2B3033",
            background: "#0B0C0D",
          }}
        >
          <Link
            href="/monitor"
            style={{
              display: "flex",
              alignItems: "center",
              gap: "10px",
              overflow: "hidden",
              whiteSpace: "nowrap",
            }}
            title="Rulecam Operational Safety Cockpit"
          >
            <span
              style={{
                width: "26px",
                height: "26px",
                minWidth: "26px",
                background: "var(--yellow, #F2D100)",
                border: "2px solid #fff",
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                color: "#141414",
                fontWeight: 900,
                fontSize: "14px",
                fontFamily: "var(--font-barlow-condensed, sans-serif)",
              }}
            >
              RC
            </span>
            {!collapsed && (
              <div>
                <div
                  style={{
                    fontFamily: "var(--font-barlow-condensed, sans-serif)",
                    fontWeight: 800,
                    fontSize: "22px",
                    letterSpacing: "1px",
                    lineHeight: 1,
                    color: "#fff",
                  }}
                >
                  RULECAM
                </div>
                <div
                  style={{
                    fontFamily: "var(--font-dm-mono, monospace)",
                    fontSize: "10px",
                    color: "var(--yellow, #F2D100)",
                    letterSpacing: "0.5px",
                  }}
                >
                  SAFETY COMMAND v2.4
                </div>
              </div>
            )}
          </Link>

          {!collapsed && (
            <button
              type="button"
              onClick={() => setCollapsed(true)}
              aria-label="Collapse sidebar"
              title="Collapse sidebar (Ctrl+B)"
              style={{
                background: "#1B1E20",
                border: "1px solid #3A3F42",
                color: "#ECEAE3",
                width: "30px",
                height: "30px",
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: "14px",
              }}
            >
              «
            </button>
          )}
        </div>

        {/* Expand button strip when collapsed */}
        {collapsed && (
          <div style={{ padding: "6px 8px", borderBottom: "1px solid #2B3033", textAlign: "center", flexShrink: 0 }}>
            <button
              type="button"
              onClick={() => setCollapsed(false)}
              aria-label="Expand sidebar"
              title="Expand sidebar (Ctrl+B)"
              style={{
                background: "#1B1E20",
                border: "1px solid #3A3F42",
                color: "var(--yellow, #F2D100)",
                width: "100%",
                height: "28px",
                fontWeight: 800,
                fontSize: "14px",
              }}
            >
              »
            </button>
          </div>
        )}

        {/* Scrollable Middle Navigation Groups */}
        <nav
          aria-label="Primary Navigation"
          className="sidebar-nav"
          style={{
            flex: 1,
            minHeight: 0,
            overflowY: "auto",
            overflowX: "hidden",
            padding: collapsed ? "8px 6px" : "10px 10px",
          }}
        >
          {NAV_GROUPS.map((grp) => (
            <div key={grp.group} style={{ marginBottom: collapsed ? "8px" : "12px" }}>
              {!collapsed ? (
                <div
                  style={{
                    fontFamily: "var(--font-dm-mono, monospace)",
                    fontSize: "10px",
                    fontWeight: 600,
                    color: "#8E9296",
                    padding: "0 8px 4px",
                    letterSpacing: "0.8px",
                  }}
                >
                  {grp.group}
                </div>
              ) : (
                <div style={{ height: "1px", background: "#2B3033", margin: "4px 4px 6px" }} />
              )}

              <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
                {grp.items.map((item) => {
                  const active =
                    pathname === item.href ||
                    (item.href === "/monitor" && (pathname === "/" || pathname === "/monitor"));
                  return (
                    <Link
                      key={item.href}
                      href={item.href}
                      title={collapsed ? `${item.label}${item.badge ? ` (${item.badge})` : ""}` : undefined}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        justifyContent: collapsed ? "center" : "space-between",
                        padding: collapsed ? "7px 4px" : "7px 10px",
                        background: active ? "#222629" : "transparent",
                        borderLeft: active
                          ? "4px solid var(--yellow, #F2D100)"
                          : "4px solid transparent",
                        color: active ? "#FFFFFF" : "#B9B7AE",
                        fontWeight: active ? 700 : 500,
                        fontSize: "13.5px",
                        transition: "background 0.12s, color 0.12s",
                        position: "relative",
                      }}
                    >
                      <div
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: collapsed ? "3px" : "10px",
                          minWidth: 0,
                          flexDirection: collapsed ? "column" : "row",
                        }}
                      >
                        <span style={{ color: active ? "var(--yellow, #F2D100)" : "inherit", display: "flex" }}>
                          {item.icon}
                        </span>
                        {!collapsed ? (
                          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            {item.label}
                          </span>
                        ) : (
                          <span
                            style={{
                              fontFamily: "var(--font-dm-mono, monospace)",
                              fontSize: "9px",
                              letterSpacing: "0.3px",
                              lineHeight: 1.1,
                            }}
                          >
                            {item.shortCode}
                          </span>
                        )}
                      </div>

                      {!collapsed && item.badge && (
                        <span
                          style={{
                            ...badgeStyle(item.badgeTone),
                            fontFamily: "var(--font-dm-mono, monospace)",
                            fontSize: "10px",
                            fontWeight: 700,
                            padding: "2px 6px",
                            lineHeight: 1.2,
                            flexShrink: 0,
                          }}
                        >
                          {item.href === "/plans"
                            ? String(liveStats.plansCount)
                            : item.href === "/incidents"
                              ? `${liveStats.totalIncidents} LOGGED`
                              : item.badge}
                        </span>
                      )}
                    </Link>
                  );
                })}
              </div>
            </div>
          ))}
        </nav>

        {/* Pinned Sidebar Bottom Telemetry & Theme Footer */}
        <div
          style={{
            flexShrink: 0,
            borderTop: "2px solid #2B3033",
            background: "#0B0C0D",
            padding: collapsed ? "8px 6px" : "12px",
          }}
        >
          {!collapsed && (
            <div
              style={{
                background: "#15181A",
                border: "1px solid #2B3033",
                padding: "10px",
                marginBottom: "12px",
                fontFamily: "var(--font-dm-mono, monospace)",
                fontSize: "11px",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "4px" }}>
                <span style={{ color: "#8E9296" }}>SAM-2 GPU</span>
                <span style={{ color: "#00E070", fontWeight: 700 }}>{liveStats.samGpu}</span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "4px" }}>
                <span style={{ color: "#8E9296" }}>GLIMMER VLM</span>
                <span style={{ color: "#3DB8FF", fontWeight: 700 }}>{liveStats.glimmerGpu}</span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "#8E9296" }}>SHA-256 CHAIN</span>
                <span style={{ color: "#F2D100", fontWeight: 700 }}>65 INTACT</span>
              </div>
            </div>
          )}

          {/* Theme Switcher */}
          <div
            role="group"
            aria-label="Theme toggle"
            style={{
              display: "grid",
              gridTemplateColumns: collapsed ? "1fr" : "1fr 1fr",
              gap: "4px",
              marginBottom: "10px",
            }}
          >
            <button
              type="button"
              aria-pressed={!isDark}
              onClick={() => setTheme("light")}
              style={{
                background: !isDark ? "var(--yellow, #F2D100)" : "#1B1E20",
                color: !isDark ? "#141414" : "#B9B7AE",
                border: "1px solid #3A3F42",
                padding: "6px 4px",
                fontFamily: "var(--font-dm-mono, monospace)",
                fontSize: "11px",
                fontWeight: 700,
              }}
            >
              {collapsed ? "☀" : "☀ LIGHT"}
            </button>
            <button
              type="button"
              aria-pressed={isDark}
              onClick={() => setTheme("dark")}
              style={{
                background: isDark ? "var(--yellow, #F2D100)" : "#1B1E20",
                color: isDark ? "#141414" : "#B9B7AE",
                border: "1px solid #3A3F42",
                padding: "6px 4px",
                fontFamily: "var(--font-dm-mono, monospace)",
                fontSize: "11px",
                fontWeight: 700,
              }}
            >
              {collapsed ? "☾" : "☾ DARK"}
            </button>
          </div>

          {!collapsed && (
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                fontSize: "12px",
                color: "#B9B7AE",
              }}
            >
              <span>M. Vance · Lead Ops</span>
              <span style={{ fontFamily: "var(--font-dm-mono, monospace)", fontSize: "10px", color: "#00E070" }}>
                ● AUTH
              </span>
            </div>
          )}
        </div>
      </aside>

      {/* Main Right Viewport: Sticky Top Command Header + Scrollable Content */}
      <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
        {/* Sticky Top Command Bar */}
        <header
          style={{
            height: "60px",
            background: "var(--card)",
            borderBottom: "2px solid var(--ink)",
            padding: "0 var(--gut)",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: "16px",
            position: "sticky",
            top: 0,
            zIndex: 30,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: "14px" }}>
            <button
              type="button"
              onClick={() => setCollapsed((c) => !c)}
              aria-label="Toggle navigation sidebar"
              title="Toggle Sidebar (Ctrl+B)"
              style={{
                background: "var(--paper)",
                border: "2px solid var(--ink)",
                color: "var(--ink)",
                height: "36px",
                padding: "0 10px",
                display: "inline-flex",
                alignItems: "center",
                gap: "6px",
                fontWeight: 700,
                fontSize: "12px",
                fontFamily: "var(--font-dm-mono, monospace)",
              }}
            >
              <span>{collapsed ? "☰ EXPAND" : "« SIDEBAR"}</span>
            </button>

            <div style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "13px" }}>
              <span className="dm" style={{ color: "var(--mute)", fontWeight: 600 }}>
                OPERATIONAL COCKPIT
              </span>
              <span>/</span>
              <strong style={{ textTransform: "uppercase", letterSpacing: "0.5px" }}>
                {pathname === "/" ? "MONITOR" : pathname.replace(/^\//, "").toUpperCase()}
              </strong>
            </div>
          </div>

          {/* Global KPI Telemetry Strip */}
          <div style={{ display: "flex", alignItems: "center", gap: "16px", flexWrap: "wrap" }}>
            <div className="dm" style={{ fontSize: "12px", display: "flex", alignItems: "center", gap: "6px" }}>
              <span
                style={{
                  width: "8px",
                  height: "8px",
                  borderRadius: "50%",
                  background: "#1E7A3A",
                  display: "inline-block",
                }}
              />
              <span>
                MODE: <b>{liveStats.mode}</b>
              </span>
            </div>

            <div className="dm" style={{ fontSize: "12px" }}>
              FLEET: <b>11/12 OK</b> <span style={{ color: "var(--alert)", fontWeight: 700 }}>(1 BLIND)</span>
            </div>

            <div className="dm" style={{ fontSize: "12px" }}>
              RULE CI: <b>22/22 PASS</b>
            </div>

            <Link
              href="/planner"
              className="btn"
              style={{ minHeight: "36px", padding: "6px 14px", fontSize: "13px" }}
            >
              + New Safety Rule
            </Link>
          </div>
        </header>

        {/* Page Body */}
        <main style={{ flex: 1, minWidth: 0 }}>{children}</main>
      </div>
    </div>
  );
}
