import type { Metadata } from "next";
import { CiSignage } from "@/components/signage/CiSignage";

export const metadata: Metadata = {
  title: "Rule CI",
  description: "Rule CI: every compiled rule is replayed against labelled clips before it runs.",
};

export default function CiPage() {
  return <CiSignage />;
}
