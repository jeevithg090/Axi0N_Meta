import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

import styles from "./staff.module.css";

export const metadata: Metadata = {
  title: "Rulecam Staff",
  description: "Camera alerts and tasks for the people on the floor: act, escalate, prove it.",
  manifest: "/staff-manifest.webmanifest",
  appleWebApp: { capable: true, title: "Rulecam Staff", statusBarStyle: "black-translucent" },
  icons: { icon: "/staff-icon-192.png", apple: "/staff-icon-192.png" },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: "#16191c",
};

/** The staff app fills the screen over the cockpit's shell: it is a phone app, not a page. */
export default function StaffLayout({ children }: { children: ReactNode }) {
  return <div className={styles.app}>{children}</div>;
}
