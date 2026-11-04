import type { Page } from "@playwright/test";

import {
  badgeText,
  clippedElements,
  expect,
  horizontalOverflow,
  NOT_FOUND,
  OPTIONAL_PAGES,
  PAGES,
  settle,
  test,
} from "./fixtures";

// Every page a judge can reach: it loads, settles (no endless spinner), shows the mode badge,
// fits the viewport, and a visitor sees a clear owner-only state rather than a raw API error.

async function checkPage(page: Page, path: string, needsBadge: boolean): Promise<void> {
  const response = await page.goto(path);
  expect(response?.status(), `HTTP status of ${path}`).toBeLessThan(400);
  await expect(page.locator("body")).not.toHaveText(NOT_FOUND);
  await settle(page);

  if (needsBadge || (await page.getByTestId("mode-badge").count()) > 0) {
    const badge = await badgeText(page);
    expect(["LIVE", "REPLAY", "OFFLINE"], `badge on ${path}`).toContain(badge);
    if (path.includes("demo=1")) expect(badge, "a replay is never shown as LIVE").toBe("REPLAY");
  }

  expect(await horizontalOverflow(page), `horizontal overflow on ${path}`).toBeLessThanOrEqual(1);
  expect.soft(await clippedElements(page), `content cut off at the right on ${path}`).toEqual([]);

  // QA-B6: the API's refusal text is not a page; visitors get the owner-only notice.
  await expect(page.locator("body")).not.toContainText(/owner login required/i);
}

for (const path of PAGES) {
  test(`page ${path} loads cleanly`, async ({ page }) => {
    await checkPage(page, path, true);
  });
}

for (const path of OPTIONAL_PAGES) {
  test(`page ${path} (when built) loads cleanly`, async ({ page, request }) => {
    test.skip((await request.get(path)).status() === 404, `${path} is not in this build`);
    await checkPage(page, path, false);
  });
}

test("a replay page never flips to LIVE while it is open", async ({ page }) => {
  await page.goto("/demo?demo=1");
  for (let i = 0; i < 4; i += 1) {
    expect(await badgeText(page)).toBe("REPLAY");
    await page.waitForTimeout(1_500);
  }
});

test("the 404 page is a real 404 with a way home", async ({ page }) => {
  const response = await page.goto("/no-such-page-qa");
  expect(response?.status()).toBe(404);
  await expect(page.locator("body")).toHaveText(NOT_FOUND);
});
