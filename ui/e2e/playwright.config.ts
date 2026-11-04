import { defineConfig, devices } from "@playwright/test";

/**
 * Rulecam UI end-to-end checks (QA).
 *
 *   E2E_BASE_URL   the UI to test (default http://localhost:3000; e.g. https://rulecam.vercel.app
 *                  or a Vercel preview URL)
 *   E2E_SERVER     optional command that starts the UI first, e.g. "npm --prefix .. run start"
 *
 * Two projects: a desktop browser and a 375 px phone (the worker page and the demo must work on
 * both). Every test fails on a console error, an uncaught page error or a broken asset; see
 * fixtures.ts for the short list of known, filed issues that are tolerated until fixed.
 */
const baseURL = (process.env.E2E_BASE_URL ?? "http://localhost:3000").replace(/\/$/, "");
const server = process.env.E2E_SERVER;

export default defineConfig({
  testDir: "./tests",
  timeout: 45_000,
  expect: { timeout: 10_000 },
  fullyParallel: true,
  workers: process.env.CI ? 2 : 4,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 800 } } },
    {
      name: "phone",
      use: {
        ...devices["Pixel 5"],
        viewport: { width: 375, height: 812 },
        browserName: "chromium",
      },
    },
  ],
  webServer: server
    ? { command: server, url: baseURL, reuseExistingServer: true, timeout: 180_000 }
    : undefined,
});
