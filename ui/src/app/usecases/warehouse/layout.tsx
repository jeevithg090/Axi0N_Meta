import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Warehouse",
  description: "The warehouse story: forklift near misses, incident reports and evidence.",
};

/** Gives the Warehouse screen its own page title; the screen itself is a client component. */
export default function Layout({ children }: { children: ReactNode }) {
  return children;
}
