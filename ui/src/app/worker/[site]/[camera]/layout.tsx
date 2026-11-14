import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "What this camera checks",
  description: "What this camera watches for, in plain words, and how to question an alert.",
};

/** Gives the What this camera checks screen its own page title; the screen itself is a client component. */
export default function Layout({ children }: { children: ReactNode }) {
  return children;
}
