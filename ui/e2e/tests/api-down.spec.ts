import { badgeText, expect, PAGES, settle, test } from "./fixtures";

// The API is down (every /rulecam-api call answers 503, the socket is refused): no page may hang
// on a spinner, show nothing, or claim LIVE. The replay must keep working with no server at all.

test.beforeEach(async ({ page }) => {
  await page.route("**/rulecam-api/**", (route) =>
    route.fulfill({ status: 503, json: { detail: "API down (QA e2e)" } }),
  );
  await page.routeWebSocket(/\/ws$/, (socket) => socket.close({ code: 1011, reason: "QA e2e" }));
});

for (const path of PAGES) {
  test(`with the API down, ${path} degrades gracefully`, async ({ page }) => {
    await page.goto(path);
    await settle(page);
    const badge = await badgeText(page);
    expect(badge, "never LIVE without a server").not.toBe("LIVE");
    if (path.includes("demo=1")) expect(badge).toBe("REPLAY");
    const text = (await page.locator("main").first().innerText()).trim();
    expect(text.length, `${path} shows something`).toBeGreaterThan(0);
  });
}

test("the golden replay and its verify page need no API", async ({ page }) => {
  await page.goto("/demo?demo=1");
  await settle(page);
  await expect(page.getByText(/chain intact/i).first()).toBeVisible();
  await page.getByRole("link", { name: /verify it in your browser/i }).click();
  await expect(page.getByText(/chain intact/i)).toBeVisible({ timeout: 20_000 });
});
