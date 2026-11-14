import type { Metadata } from "next";
import { StreamsSignage } from "@/components/signage/StreamsSignage";

export const metadata: Metadata = {
  title: "Camera fleet",
  description: "The camera fleet: every stream, its state and its rules.",
};

export default function StreamsPage() {
  return <StreamsSignage />;
}
