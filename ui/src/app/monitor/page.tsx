import type { Metadata } from "next";
import { MonitorSignage } from "@/components/signage/MonitorSignage";

export const metadata: Metadata = {
  title: "Monitor",
  description: "The operations monitor: cameras, rules, alerts and model status in one view.",
};

export default function MonitorPage() {
  return <MonitorSignage />;
}
