import type { Metadata, Viewport } from "next";
import { Barlow, Barlow_Condensed, DM_Mono } from "next/font/google";
import type { ReactNode } from "react";

import { Topbar } from "@/components/Topbar";
import { ThemeProvider } from "@/lib/theme";
import { AuthProvider } from "@/lib/auth";
import { StreamProvider } from "@/lib/stream";

import "./globals.css";

const barlow = Barlow({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-barlow",
  display: "swap",
});

const barlowCondensed = Barlow_Condensed({
  subsets: ["latin"],
  weight: ["500", "600", "700", "800"],
  variable: "--font-barlow-condensed",
  display: "swap",
});

const dmMono = DM_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-dm-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Rulecam · Operational Dashboard",
  description: "Safety operations command dashboard. Plain words in, formal proofs out.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#ECEAE3",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html
      lang="en"
      className={`${barlow.variable} ${barlowCondensed.variable} ${dmMono.variable}`}
      data-theme="light"
    >
      <body>
        <ThemeProvider>
          <StreamProvider>
            <AuthProvider>
              <div className="root">
                <Topbar />
                <main>{children}</main>
              </div>
            </AuthProvider>
          </StreamProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
