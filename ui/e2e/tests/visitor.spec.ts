import { expect, OPTIONAL_PAGES, PAGES, settle, test } from "./fixtures";
import { asVisitor } from "./visitor";

// What a judge on the public site sees: the API answers only its public routes and refuses the
// rest with 403 (see visitor.ts). Works against a local UI that its own API treats as the owner.

for (const path of [...PAGES, ...OPTIONAL_PAGES]) {
  test(`a visitor on ${path} sees a clear page`, async ({ page, request }) => {
    const missing = OPTIONAL_PAGES.includes(path) && (await request.get(path)).status() === 404;
    test.skip(missing, `${path} is not in this build`);
    await asVisitor(page);
    await page.goto(path);
    await settle(page);
    const main = page.locator("body");
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
