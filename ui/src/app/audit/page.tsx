import type { Metadata } from "next";
import { AuditSignage } from "@/components/signage/AuditSignage";

export const metadata: Metadata = {
  title: "Audit",
  description: "The append-only evidence log, its hash chain and the day's Merkle root.",
};

export default function AuditPage() {
  return <AuditSignage />;
}
