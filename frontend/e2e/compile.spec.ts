import { expect, test } from "@playwright/test";

test("load the banking sample, compile, and see a verdict", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Load sample").selectOption("banking");
  await page.getByRole("button", { name: "Compile", exact: true }).click();
  await expect(page.getByTestId("overall-verdict")).toBeVisible();
  await page.getByRole("tab", { name: "Verdicts" }).click();
  await expect(page.getByRole("table")).toBeVisible();
  await page.getByRole("tab", { name: "Generated Code" }).click();
  await expect(page.getByRole("button", { name: "Validators" })).toBeVisible();
});

test("compile and verify shows MATCH results", async ({ page }) => {
  test.skip(!process.env.E2E_VERIFY, "needs a backend with sandbox databases");
  await page.goto("/");
  await page.getByLabel("Load sample").selectOption("ecommerce");
  await page.getByRole("button", { name: "Compile & Verify" }).click();
  await page.getByRole("tab", { name: "Verification" }).click();
  await expect(page.getByText("MATCH").first()).toBeVisible({ timeout: 45_000 });
});

test("demo button loads banking and compiles", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Demo" }).click();
  await expect(page.getByTestId("overall-verdict")).toBeVisible();
  await page.getByRole("tab", { name: "Verification" }).click();
  if (process.env.E2E_VERIFY) {
    await expect(page.getByText("MATCH").first()).toBeVisible({ timeout: 45_000 });
  } else {
    await expect(page.getByRole("alert")).toContainText("verification is not configured");
  }
});
