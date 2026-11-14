import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Connect cameras",
  description: "Add a camera from a phone, an RTSP or HLS stream, or a recorded clip.",
};

/** Gives the Connect cameras screen its own page title; the screen itself is a client component. */
export default function Layout({ children }: { children: ReactNode }) {
  return children;
}
