import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

import { Shell } from "@/components/Shell";
import { AuthProvider } from "@/lib/auth";
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
          <AuthProvider>
            <Shell>{children}</Shell>
          </AuthProvider>
        </StreamProvider>
      </body>
    </html>
  );
}
