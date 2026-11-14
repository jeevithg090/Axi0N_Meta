import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Site tools",
  description: "Site tools: the site's context, shelf photos and setup.",
};

/** Gives the Site tools screen its own page title; the screen itself is a client component. */
export default function Layout({ children }: { children: ReactNode }) {
  return children;
}
