import type { Metadata } from "next";
import { PlannerSignage } from "@/components/signage/PlannerSignage";

export const metadata: Metadata = {
  title: "Planner",
  description:
    "Describe a safety rule in plain words; the planner turns it into a rule the cameras can check.",
};

export default function PlannerPage() {
  return <PlannerSignage />;
}
