import type { Metadata } from "next";
import { AskSignage } from "@/components/signage/AskSignage";

export const metadata: Metadata = {
  title: "Ask",
  description: "Ask a question about the site in plain words and get an answer with its evidence.",
};

export default function AskPage() {
  return <AskSignage />;
}
