import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Alerts",
  description: "Every alert the runtime raised, with the second model's verdict.",
};

/** Gives the Alerts screen its own page title; the screen itself is a client component. */
export default function Layout({ children }: { children: ReactNode }) {
  return children;
}
