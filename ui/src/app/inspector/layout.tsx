import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Proof portal",
  description: "The proof portal: share a verifiable evidence pack with an inspector.",
};

/** Gives the Proof portal screen its own page title; the screen itself is a client component. */
export default function Layout({ children }: { children: ReactNode }) {
  return children;
}
