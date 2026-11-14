import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Extensions",
  description: "Extensions: add a detector or a check to the rule engine.",
};

/** Gives the Extensions screen its own page title; the screen itself is a client component. */
export default function Layout({ children }: { children: ReactNode }) {
  return children;
}
