"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTheme } from "@/lib/theme";

const NAV_ITEMS = [
  { href: "/monitor", label: "Monitor" },
  { href: "/streams", label: "Streams" },
  { href: "/incidents", label: "Incidents" },
  { href: "/planner", label: "Planner" },
  { href: "/plans", label: "Plans" },
  { href: "/ci", label: "Rule CI" },
  { href: "/audit", label: "Audit" },
  { href: "/worker", label: "Worker" },
  { href: "/system", label: "System Sheet" },
];

export function Topbar() {
  const pathname = usePathname();
  const { isDark, setTheme } = useTheme();

  return (
    <header className="topbar">
      <div className="in">
        <div style={{ display: "flex", alignItems: "center", gap: "32px", flexWrap: "wrap" }}>
          <Link className="brand" href="/monitor">
            <span className="mark" aria-hidden="true" />
            <span className="word">RULECAM</span>
          </Link>
          <nav aria-label="Primary Navigation">
            {NAV_ITEMS.map((item) => {
              const active =
                pathname === item.href ||
                (item.href === "/monitor" && (pathname === "/" || pathname === "/monitor"));
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={active ? "on" : ""}
                >
                  {item.label}
                </Link>
              );
            })}
          </nav>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "24px", flexWrap: "wrap" }}>
          <span className="who">M. Vance · Lead Ops</span>
          <div className="mode" role="group" aria-label="Theme toggle">
            <button
              type="button"
              aria-pressed={!isDark}
              onClick={() => setTheme("light")}
            >
              Light
            </button>
            <button
              type="button"
              aria-pressed={isDark}
              onClick={() => setTheme("dark")}
            >
              Dark
            </button>
          </div>
        </div>
      </div>
    </header>
  );
}
