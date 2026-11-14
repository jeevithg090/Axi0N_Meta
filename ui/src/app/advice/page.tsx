import type { Metadata } from "next";
import { AdviceSignage } from "@/components/signage/AdviceSignage";

export const metadata: Metadata = {
  title: "Advice",
  description: "Advice cards from what the cameras saw, each with the numbers behind it.",
};

export default function AdvicePage() {
  return <AdviceSignage />;
}
