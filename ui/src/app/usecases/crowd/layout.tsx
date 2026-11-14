import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Crowd",
  description: "The crowd story: zone counts, density alerts and an after-event report.",
};

/** Gives the Crowd screen its own page title; the screen itself is a client component. */
export default function Layout({ children }: { children: ReactNode }) {
  return children;
}
