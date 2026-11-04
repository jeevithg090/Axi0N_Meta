import { expect, settle, test } from "./fixtures";
import { asVisitor } from "./visitor";

// The staff phone app (p1/staff-app): onboarding, then the feed. A visitor (no owner session) must
// not be told "Nothing needs you right now" when the API refused the feed: that reads as "all
// clear" on a safety app.

test.beforeEach(async ({ request }) => {
  test.skip((await request.get("/staff")).status() === 404, "/staff is not in this build");
});

async function startShift(page: import("@playwright/test").Page): Promise<void> {
  await page.goto("/staff");
  await settle(page);
  await page.getByPlaceholder(/other role/i).fill("steward");
  await page.getByRole("button", { name: /start|begin|continue|go|shift/i }).first().click();
  await page.waitForTimeout(4_000);
}

test("the owner reaches the staff feed", async ({ page }) => {
  await startShift(page);
  await expect(page.getByText(/steward/i).first()).toBeVisible();
});

test("a refused feed is not shown as all clear", async ({ page }) => {
  await asVisitor(page);
  await startShift(page);
  const text = await page.locator("body").innerText();
  const allClear = /nothing needs you right now/i.test(text);
  const explained = /log in|owner|not allowed|cannot reach|could not|offline|sign in/i.test(text);
  expect(allClear && !explained, "an all-clear while the API refused the feed").toBe(false);
});
