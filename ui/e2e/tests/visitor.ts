import type { Page } from "@playwright/test";

/**
 * The API's public routes (the allowlist in tests/e2e/surface.py). `asVisitor` lets these through
 * and refuses everything else with 403, as the deployed API does for anyone who is not the owner,
 * so a local UI (whose own API treats it as the loopback owner) shows what a judge sees.
 */
export const PUBLIC_API = [
  /^\/health$/,
  /^\/mode$/,
  /^\/metrics(\/perception)?$/,
  /^\/runtime\/metrics$/,
  /^\/cameras$/,
  /^\/auth\/(login|logout)$/,
  /^\/evidence\/(days|[\d-]+\/(hashes|root|verify))$/,
  /^\/advice(\/seals|\/[^/]+)?$/,
  /^\/pair\//,
  /^\/worker\//,
  /^\/verify\//,
  /^\/proof\/shared\//,
];

export async function asVisitor(page: Page): Promise<void> {
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
