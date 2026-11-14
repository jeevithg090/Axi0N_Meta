import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Surprise test",
  description: "A surprise test: an unseen clip checked against the running rules.",
};

/** Gives the Surprise test screen its own page title; the screen itself is a client component. */
export default function Layout({ children }: { children: ReactNode }) {
  return children;
}
