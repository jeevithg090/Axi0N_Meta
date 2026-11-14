import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Golden replay",
  description:
    "A recorded live run, step by step: request, plan, Rule CI, runtime and evidence. No login, no model calls.",
};

/** Gives the Golden replay screen its own page title; the screen itself is a client component. */
export default function Layout({ children }: { children: ReactNode }) {
  return children;
}
