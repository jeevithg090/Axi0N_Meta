import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Supermarket",
  description: "The supermarket story: ask, advise, watch, alert staff and close tasks with proof.",
};

/** Gives the Supermarket screen its own page title; the screen itself is a client component. */
export default function Layout({ children }: { children: ReactNode }) {
  return children;
}
