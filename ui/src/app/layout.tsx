import type { Metadata, Viewport } from "next";
import { Atkinson_Hyperlegible_Mono, Host_Grotesk } from "next/font/google";
import type { ReactNode } from "react";

import { DashboardShell } from "@/components/DashboardShell";
import { AuthProvider } from "@/lib/auth";
import { StreamProvider } from "@/lib/stream";
import { THEME_BOOT_SCRIPT, ThemeProvider } from "@/lib/theme";

import "./globals.css";

// Host Grotesk for what a person reads; Atkinson Hyperlegible Mono for what an instrument
// measured (a distance, a score, a hash, a time). Two families, three weights.
const host = Host_Grotesk({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-host",
  display: "swap",
});

const mono = Atkinson_Hyperlegible_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-atkinson-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Rulecam: camera rules in plain words, verified by Meta models",
  description:
    "Describe a safety rule in your own words; Rulecam plans it, proves it with Rule CI, and has Muse Glimmer check every alert.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f8f6f1" },
    { media: "(prefers-color-scheme: dark)", color: "#16191c" },
  ],
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={`${host.variable} ${mono.variable}`} suppressHydrationWarning>
      <head>
        {/* Sets the saved light or dark choice before first paint, so the page never flashes. */}
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT_SCRIPT }} />
      </head>
      <body>
        <ThemeProvider>
          <StreamProvider>
            <AuthProvider>
              <DashboardShell>{children}</DashboardShell>
            </AuthProvider>
          </StreamProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
