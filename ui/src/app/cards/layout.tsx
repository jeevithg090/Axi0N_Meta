import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Cards",
  description: "The card gallery: alerts, reports and evidence as cards.",
};

/** Gives the Cards screen its own page title; the screen itself is a client component. */
export default function Layout({ children }: { children: ReactNode }) {
  return children;
}
