import type { Metadata } from "next";
import { SystemSignage } from "@/components/signage/SystemSignage";

export const metadata: Metadata = {
  title: "System and models",
  description: "System health and the Meta models in use.",
};

export default function SystemPage() {
  return <SystemSignage />;
}
