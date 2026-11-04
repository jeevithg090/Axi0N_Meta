import type { Page } from "@playwright/test";

import { badgeText, clippedElements, expect, NOT_FOUND, settle, test } from "./fixtures";

// The judge's path: landing → each use-case page → the golden replay → the browser verify page →
// ask → incidents. Every internal link on the way must lead somewhere real.

async function internalLinks(page: Page): Promise<string[]> {
  const hrefs = await page
    .locator("a[href]")
    .evaluateAll((anchors) => anchors.map((a) => (a as HTMLAnchorElement).getAttribute("href") ?? ""));
  return [...new Set(hrefs)]
    .filter((href) => href.startsWith("/") && !href.startsWith("//"))
    .filter((href) => !href.startsWith("/rulecam-api/")); // API downloads (posters, reports)
}

test("every internal link on the landing page resolves", async ({ page, request }) => {
  await page.goto("/");
  await settle(page);
  const links = await internalLinks(page);
  expect(links.length, "the landing page links somewhere").toBeGreaterThan(0);
  const dead: string[] = [];
  for (const href of links) {
    const reply = await request.get(href);
    if (reply.status() >= 400) dead.push(`${reply.status()} ${href}`);
  }
  expect(dead).toEqual([]);
});

test("each use-case page opens from the landing page", async ({ page }) => {
  await page.goto("/");
  await settle(page);
  const useCases = (await internalLinks(page)).filter((href) => href.startsWith("/usecases"));
  test.skip(useCases.length === 0, "no /usecases links on this build yet");
  for (const href of useCases) {
    const response = await page.goto(href);
    expect(response?.status(), href).toBeLessThan(400);
    await expect(page.locator("body")).not.toHaveText(NOT_FOUND);
    await settle(page);
    await expect(page.locator("h1, h2").first(), `${href} has a heading`).toBeVisible();
  }
});

// The three use-case pages, checked directly (a branch may not link them from its landing yet).
for (const useCase of ["/usecases/warehouse", "/usecases/crowd", "/usecases/supermarket"]) {
  test(`use case ${useCase} settles with real content`, async ({ page, request }) => {
    test.skip((await request.get(useCase)).status() === 404, `${useCase} is not in this build`);
    await page.goto(useCase);
    await settle(page);
    await expect(page.locator("main h1, main h2").first()).toBeVisible();
    // The shared use-case frame: the world's promise and a still from the footage it ran on.
    await expect(page.getByTestId("usecase-frame")).toBeVisible();
    await expect(page.locator('[data-testid^="still-"] img').first()).toBeVisible();
    // QA-B23: an API route missing from the deployed API must not leave a raw "Not Found" or a
    // loader that never ends.
    await expect(page.locator("main").first()).not.toContainText(/^Not Found$|Loading the story…/m, {
      timeout: 15_000,
    });
    expect.soft(await clippedElements(page), `content cut off on ${useCase}`).toEqual([]);
  });
}

test("the golden replay tells its story and its evidence verifies in the browser", async ({ page }) => {
  await page.goto("/demo?demo=1");
  await settle(page);
  expect(await badgeText(page)).toBe("REPLAY");
  for (const section of [/request and the planner/i, /Rule CI/i, /evidence chain/i]) {
    await expect(page.getByText(section).first()).toBeVisible();
  }

  const verify = page.getByRole("link", { name: /verify it in your browser/i });
  await expect(verify).toBeVisible();
  const href = (await verify.getAttribute("href")) ?? "";
  const expected = new URL(href, "http://x").searchParams;
  await verify.click();
  await expect(page).toHaveURL(/\/verify\//);

  // The page recomputes every SHA-256 itself: the verdict, the count and the root must agree.
  await expect(page.getByText(/chain intact/i)).toBeVisible({ timeout: 20_000 });
  const n = expected.get("n");
  if (n) await expect(page.getByText(new RegExp(`${n} records`))).toBeVisible();
  const root = expected.get("root");
  if (root) await expect(page.getByText(root)).toBeVisible();
  await expect(page.getByText(/matches the committed daily root/i)).toBeVisible();
});

test("the demo's replay links stay in replay", async ({ page }) => {
  await page.goto("/demo?demo=1");
  await settle(page);
  const replayLinks = (await internalLinks(page)).filter((href) => href.includes("demo=1"));
  for (const href of replayLinks.slice(0, 4)) {
    await page.goto(href);
    await settle(page);
    expect(await badgeText(page), href).toBe("REPLAY");
  }
});

test("ask shows a visitor what it is and how to get in", async ({ page }) => {
  await page.goto("/ask");
  await settle(page);
  await expect(page.locator("main").first()).toContainText(/ask/i);
  const ownerOnly = page.getByTestId("owner-only");
  if (await ownerOnly.count()) await expect(ownerOnly.getByRole("link", { name: /log in/i })).toBeVisible();
});

test("incidents shows a visitor a clear state", async ({ page }) => {
  await page.goto("/incidents");
  await settle(page);
  await expect(page.locator("main").first()).toContainText(/incident/i);
  await expect(page.locator("main").first()).not.toContainText(/owner login required/i);
});
