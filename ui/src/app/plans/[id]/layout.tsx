import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Plan",
  description: "One plan: its steps, the compiled rule, its acceptance trace and its versions.",
};

/** Gives the Plan screen its own page title; the screen itself is a client component. */
export default function Layout({ children }: { children: ReactNode }) {
  return children;
}
