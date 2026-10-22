import { expect, test } from "@playwright/test";

test.skip(!process.env.E2E_VISUAL, "visual snapshots run with E2E_VISUAL=1");

const mask = (page: import("@playwright/test").Page) => [
  page.locator(".monaco-editor"),
  page.locator("time"),
];

for (const scheme of ["light", "dark"] as const) {
  test.describe(`visual, ${scheme}`, () => {
    test.use({ colorScheme: scheme, viewport: { width: 1440, height: 900 } });

    test("workspace pages", async ({ page }) => {
      await page.goto("/");
      await expect(page).toHaveScreenshot(`home-${scheme}.png`, { mask: mask(page) });
      await page.getByLabel("Load sample").selectOption("banking");
      await page.getByRole("button", { name: "Compile", exact: true }).click();
      await expect(page.getByTestId("overall-verdict")).toBeVisible();
      await expect(page).toHaveScreenshot(`summary-${scheme}.png`, { mask: mask(page) });
      for (const tab of ["Verdicts", "Relationships", "Generated Code"]) {
        await page.getByRole("tab", { name: tab }).click();
        await page.waitForTimeout(400);
        await expect(page).toHaveScreenshot(`${tab.toLowerCase().replace(" ", "-")}-${scheme}.png`, {
          mask: mask(page),
        });
      }
    });

    test("static pages", async ({ page }) => {
      for (const path of ["about", "history", "benchmarks"]) {
        await page.goto(`/${path}`);
        await page.waitForLoadState("networkidle");
        await expect(page).toHaveScreenshot(`${path}-${scheme}.png`);
      }
    });
  });
}
