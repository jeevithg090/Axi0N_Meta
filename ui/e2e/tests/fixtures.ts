import { expect, test as base, type Page } from "@playwright/test";

/**
 * Console noise that is a filed bug (docs/qa/bugs.md), tolerated so the suite stays useful while
 * it is open. Remove an entry when its bug is fixed; never add one without a bug id.
 */
export const KNOWN_ISSUES: { id: string; pattern: RegExp }[] = [
  // QA-B8: every page opens /ws; a visitor's socket is refused, so the browser logs an error.
  { id: "QA-B8", pattern: /WebSocket connection to '.*\/ws' failed/ },
];

/** Public pages a judge can reach from the navigation (owner pages render a visitor state). */
export const PAGES = [
  "/",
  "/demo?demo=1",
  "/planner",
  "/plans",
  "/monitor",
  "/alerts",
  "/ask",
  "/incidents",
  "/ci",
  "/cards",
  "/advice",
  "/surprise",
  "/system",
  "/site",
  "/streams",
  "/audit",
  "/extensions",
  "/login",
  "/worker",
];

/** Text that must not still be on screen once a page has settled. */
export const STUCK = /Loading…|Loading\.\.\.|Checking who you are…/;

export interface PageProblems {
  console: string[];
  assets: string[];
  pageErrors: string[];
}

function isAsset(url: string, base: string): boolean {
  if (!url.startsWith(base)) return false;
  const path = new URL(url).pathname;
  return path.startsWith("/_next/") || /\.(js|css|png|jpe?g|svg|webp|ico|woff2?|ttf|json|mp4|webm|html)$/.test(path);
}

/** Collects console errors, uncaught errors and failed same-origin assets for one page. */
export function watch(page: Page, base: string): PageProblems {
  const problems: PageProblems = { console: [], assets: [], pageErrors: [] };
  page.on("console", (message) => {
    if (message.type() !== "error") return;
    const text = message.text();
    if (KNOWN_ISSUES.some((issue) => issue.pattern.test(text))) return;
    // HTTP failures are judged by the response listener below (assets) and by each test (API
    // refusals a visitor is meant to get); the browser's generic log line adds nothing.
    if (text.startsWith("Failed to load resource")) return;
    problems.console.push(text);
  });
  page.on("pageerror", (error) => problems.pageErrors.push(String(error)));
  page.on("response", (response) => {
    const url = response.url();
    if (response.status() >= 400 && isAsset(url, base) && !url.includes("/rulecam-api/")) {
      problems.assets.push(`${response.status()} ${url}`);
    }
  });
  page.on("requestfailed", (request) => {
    const url = request.url();
    const reason = request.failure()?.errorText ?? "";
    if (isAsset(url, base) && !reason.includes("ERR_ABORTED")) problems.assets.push(`failed ${url} ${reason}`);
  });
  return problems;
}

export const test = base.extend<{ problems: PageProblems }>({
  problems: [
    async ({ page, baseURL }, use) => {
      const problems = watch(page, baseURL ?? "");
      await use(problems);
      expect.soft(problems.pageErrors, "uncaught page errors").toEqual([]);
      expect.soft(problems.console, "console errors").toEqual([]);
      expect.soft(problems.assets, "broken assets").toEqual([]);
    },
    { auto: true },
  ],
});

export { expect };

/** Waits for the page to settle: network quiet (bounded) and no stuck loading text. */
export async function settle(page: Page): Promise<void> {
  await page.waitForLoadState("domcontentloaded");
  await page.waitForLoadState("networkidle", { timeout: 8_000 }).catch(() => undefined);
  await expect(page.locator("body")).not.toHaveText(STUCK, { timeout: 12_000 });
}

/** Width the document overflows the viewport by (0 = no horizontal scroll). */
export async function horizontalOverflow(page: Page): Promise<number> {
  return page.evaluate(() => {
    const doc = document.scrollingElement ?? document.documentElement;
    return Math.max(0, doc.scrollWidth - window.innerWidth);
  });
}

/**
 * Visible elements in `main` that stick out past the right edge of the viewport and are cut off
 * (no horizontal scroller around them): the page does not scroll, but content is clipped.
 */
export async function clippedElements(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const scrolls = (el: Element | null): boolean => {
      // A scroller inside main (a wide table, a code block) is fine; main itself scrolling
      // sideways is the overflow this check is for.
      for (let node = el; node && node.tagName !== "MAIN" && node !== document.body; node = node.parentElement) {
        const x = getComputedStyle(node).overflowX;
        if ((x === "auto" || x === "scroll") && node.scrollWidth > node.clientWidth) return true;
      }
      return false;
    };
    return [...document.querySelectorAll("main *")]
      .filter((el) => {
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0 && r.right > window.innerWidth + 2 && !scrolls(el.parentElement);
      })
      .filter((el, _, all) => !all.includes(el.parentElement as Element))
      .slice(0, 5)
      .map((el) => {
        const r = el.getBoundingClientRect();
        const text = (el.textContent ?? "").trim().slice(0, 40);
        return `${el.tagName.toLowerCase()} right=${Math.round(r.right)} "${text}"`;
      });
  });
}

/** The mode badge's text once it has left its initial "…" state (or "…" if it never does). */
export async function badgeText(page: Page): Promise<string> {
  const badge = page.getByTestId("mode-badge").first();
  await expect(badge).toBeVisible();
  await expect(badge).not.toHaveText("…", { timeout: 8_000 }).catch(() => undefined);
  return ((await badge.textContent()) ?? "").trim();
}

/** The Next.js "not found" page. */
export const NOT_FOUND = /This page could not be found|404: /;
