import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Glasses mode",
  description: "Glasses mode: a hands-free view for smart glasses or a phone held at eye level.",
};

/** Gives the Glasses mode screen its own page title; the screen itself is a client component. */
export default function Layout({ children }: { children: ReactNode }) {
  return children;
}
