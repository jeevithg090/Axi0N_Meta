import type { Metadata } from "next";
import { PlansSignage } from "@/components/signage/PlansSignage";

export const metadata: Metadata = {
  title: "Plans",
  description: "Every plan the planner wrote, with its version history.",
};

export default function PlansPage() {
  return <PlansSignage />;
}
