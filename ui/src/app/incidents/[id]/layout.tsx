import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Incident",
  description: "One incident: its clip, the second model's verdict and its evidence record.",
};

/** Gives the Incident screen its own page title; the screen itself is a client component. */
export default function Layout({ children }: { children: ReactNode }) {
  return children;
}
