import type { Metadata } from "next";
import { WorkerSignage } from "@/components/signage/WorkerSignage";

export const metadata: Metadata = {
  title: "Worker page",
  description: "The worker QR page: what a camera watches for, in plain words.",
};

export default function WorkerPage() {
  return <WorkerSignage />;
}
