import type { Metadata } from "next";
import { IncidentsSignage } from "@/components/signage/IncidentsSignage";

export const metadata: Metadata = {
  title: "Incidents",
  description: "Incidents raised by the rules, each with its clip and evidence.",
};

export default function IncidentsPage() {
  return <IncidentsSignage />;
}
