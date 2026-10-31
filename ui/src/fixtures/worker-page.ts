// A worker page payload (P2-11) for tests.

import type { WorkerPage } from "@/lib/worker";

export const workerPage = (overrides: Partial<WorkerPage> = {}): WorkerPage => ({
  plan_id: "main-st-kitchen",
  site_name: "Main St",
  camera_id: "cam1",
  lang: "es",
  languages: ["es", "en"],
  privacy_mode: "anonymous",
  retention_days: 14,
  never_list: ["faces", "names", "speed_scores", "emotion", "break_rooms"],
  rules: [
    { rule_id: "strap", plain_language: "Abroche el arnés." },
    { rule_id: "sink", plain_language: null },
  ],
  events: [
    { code: "1A2B3C4D", rule_id: "strap", bucket_start: "2026-11-02T10:15:00Z", dispute: null },
    { code: "5E6F7A8B", rule_id: "gone", bucket_start: "2026-11-02T09:00:00Z", dispute: "upheld" },
  ],
  counts: { strap: { alerts: 3, disputes: 1, upheld: 1 } },
  ...overrides,
});
