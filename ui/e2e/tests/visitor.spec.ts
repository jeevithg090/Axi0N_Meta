import type { Page } from "@playwright/test";

import { expect, PAGES, settle, test } from "./fixtures";

// What a judge on the public site sees: the API answers only its public routes (the allowlist in
// tests/e2e/surface.py) and refuses everything else with 403, as the deployed API does for anyone
// who is not the owner. Works against a local UI whose own API treats it as the owner (loopback).

const PUBLIC_API = [
  /^\/health$/,
  /^\/mode$/,
  /^\/metrics(\/perception)?$/,
  /^\/runtime\/metrics$/,
  /^\/cameras$/,
  /^\/auth\/(me|login|logout)$/,
  /^\/evidence\/(days|[\d-]+\/(hashes|root|verify))$/,
  /^\/advice(\/seals|\/[^/]+)?$/,
  /^\/pair\//,
  /^\/worker\//,
  /^\/verify\//,
];

async function asVisitor(page: Page): Promise<void> {
  await page.route("**/rulecam-api/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace(/^\/rulecam-api/, "");
    if (path === "/auth/me")
      return route.fulfill({
        json: { owner: false, via: null, csrf_token: null, expires_at: null, login_enabled: true },
      });
    if (PUBLIC_API.some((allowed) => allowed.test(path))) return route.continue();
    return route.fulfill({ status: 403, json: { detail: "owner login required" } });
  });
  await page.routeWebSocket(/\/ws$/, (socket) => socket.close({ code: 1008, reason: "visitor" }));
}

const USE_CASES = ["/usecases/warehouse", "/usecases/crowd", "/usecases/supermarket"];

for (const path of [...PAGES, ...USE_CASES]) {
  test(`a visitor on ${path} sees a clear page`, async ({ page, request }) => {
    const missing = USE_CASES.includes(path) && (await request.get(path)).status() === 404;
    test.skip(missing, `${path} is not in this build`);
    await asVisitor(page);
    await page.goto(path);
    await settle(page);
    const main = page.locator("main").first();
    await expect(main).not.toContainText(/owner login required/i);
    await expect(main).not.toContainText(/^(Not Found|Forbidden)$/m);
    await expect(main).not.toContainText(/could not reach the rulecam api/i);
    expect((await main.innerText()).trim().length, `${path} shows something`).toBeGreaterThan(0);
  });
}

test("a visitor who asks a question never gets a made-up answer (QA-B18)", async ({ page }) => {
  await asVisitor(page);
  await page.goto("/ask");
  await settle(page);
  const box = page.locator("main textarea:visible, main input[type=text]:visible").first();
  test.skip((await box.count()) === 0, "no question box for visitors");
  await box.fill("How many people came within 2 m of a forklift today?", { timeout: 5_000 });
  const submit = page
    .locator("main")
    .first()
    .getByRole("button", { name: /run|ask|send|submit/i })
    .first();
  if (await submit.count()) await submit.click({ timeout: 5_000 });
  else await box.press("Enter");
  await page.waitForTimeout(5_000);
  const text = await page.locator("main").first().innerText();
  expect(text).not.toMatch(/accepted the answer/i);
  expect(text).not.toMatch(/receipt [0-9a-f]{8,}/i);
});
