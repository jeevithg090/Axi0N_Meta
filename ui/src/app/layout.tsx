import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

import { Shell } from "@/components/Shell";
import { StreamProvider } from "@/lib/stream";

import "./globals.css";

export const metadata: Metadata = {
  title: "Rulecam",
  description: "A camera agent that plans its own job and proves its work.",
};

export const viewport: Viewport = { width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <StreamProvider>
          <Shell>{children}</Shell>
        </StreamProvider>
      </body>
    </html>
  );
}
