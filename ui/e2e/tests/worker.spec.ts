import { expect, horizontalOverflow, settle, test } from "./fixtures";

// The worker rules page (QR poster), on a phone. The API is stubbed with a fixed worker page so the
// check needs no live plan; the shape matches `GET /worker/{plan_id}/{camera}`.

const WORKER_PAGE = {
  plan_id: "qa-plan",
  site_name: "QA Warehouse",
  camera_id: "dock",
  lang: "en",
  languages: ["en", "es"],
  privacy_mode: "anonymous",
  retention_days: 14,
  never_list: ["faces", "names", "speed_scores", "emotion", "break_rooms"],
  rules: [
    { rule_id: "forklift-2m", plain_language: "Keep at least 2 metres from a moving forklift." },
    { rule_id: "vest", plain_language: null },
  ],
  events: [
    { code: "1A2B3C4D", rule_id: "forklift-2m", bucket_start: "2026-11-03T10:15:00Z", dispute: null },
    { code: "5E6F7A8B", rule_id: "forklift-2m", bucket_start: "2026-11-03T09:00:00Z", dispute: "upheld" },
  ],
  counts: { "forklift-2m": { alerts: 2, disputes: 1, upheld: 1 } },
};

test.beforeEach(async ({ page }) => {
  await page.route("**/rulecam-api/worker/qa-plan/dock**", (route) =>
    route.request().url().includes("/poster")
      ? route.fallback()
      : route.fulfill({ json: WORKER_PAGE }),
  );
});

test("the worker page reads well on a 375 px phone", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/worker/qa-plan/dock");
  await settle(page);
  await expect(page.getByText("Keep at least 2 metres from a moving forklift.").first()).toBeVisible();
  await expect(page.getByText("1A2B3C4D").first()).toBeVisible();
  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(1);

  // Tap targets: every button a worker can press is at least 40 px tall.
  const small = await page.locator("main button:visible").evaluateAll((buttons) =>
    buttons
      .map((b) => ({ text: (b.textContent ?? "").trim(), h: b.getBoundingClientRect().height }))
      .filter((b) => b.h < 40),
  );
  expect(small).toEqual([]);

  // No identity anywhere on the page.
  await expect(page.locator("main").first()).not.toContainText(/track[_ ]?id|face id|employee id/i);
});

test("the worker page says so when the API is unreachable", async ({ page }) => {
  await page.unroute("**/rulecam-api/worker/qa-plan/dock**");
  await page.route("**/rulecam-api/**", (route) => route.fulfill({ status: 503, json: { detail: "down" } }));
  await page.goto("/worker/qa-plan/dock");
  await settle(page);
  await expect(page.locator("main").first()).not.toBeEmpty();
});
