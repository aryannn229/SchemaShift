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
  await page.getByRole("button", { name: "Run verification" }).click();
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
    await expect(page.getByRole("tabpanel").getByRole("alert")).toContainText("verification is not configured");
  }
});

test("a verified run shows evidence, a minimal counterexample and can replay it", async ({
  page,
}) => {
  test.skip(!process.env.E2E_VERIFY, "needs a backend with sandbox databases");
  await page.goto("/");
  await page.getByLabel("Load sample").selectOption("banking");
  await page.getByRole("button", { name: "Run verification" }).click();
  await page.getByRole("tab", { name: "Verification" }).click();
  await expect(page.getByText("MATCH").first()).toBeVisible({ timeout: 45_000 });
  await page.getByRole("tab", { name: "Verdicts" }).click();
  await page.getByLabel("Search verdicts").fill("cascade_delete:transactions");
  await page
    .getByRole("button", { name: /Expand trace for cascade_delete:transactions/ })
    .click();
  const panel = page.getByTestId("evidence-panel");
  await expect(panel).toContainText("Proven");
  await expect(panel).toContainText("EQ-CASCADE-DEL/REFERENCED");
  const example = page.getByTestId("counterexample");
  await expect(example).toBeVisible();
  await expect(example).toContainText("PostgreSQL");
  await expect(example).toContainText("MongoDB");
  await expect(example).toContainText("no cascade");
  await example.getByRole("button", { name: "Replay probe" }).click();
  await expect(example.getByRole("button", { name: "Replay probe" })).toBeEnabled({
    timeout: 45_000,
  });
  await expect(example).toContainText("Resulting difference");
});

test("a verified run races the helpers and shows the concurrency evidence", async ({ page }) => {
  test.skip(!process.env.E2E_VERIFY, "needs a backend with sandbox databases");
  await page.goto("/");
  await page.getByLabel("Load sample").selectOption("banking");
  await page.getByRole("button", { name: "Run verification" }).click();
  await page.getByRole("tab", { name: "Verification" }).click();
  await expect(page.getByText("MATCH").first()).toBeVisible({ timeout: 45_000 });
  await page.getByRole("tab", { name: "Verdicts" }).click();
  await page.getByRole("tab", { name: /^Concurrency \(/ }).click();
  await page.getByLabel("Search verdicts").fill("conc:fk_race:fk:transactions");
  await page.getByRole("button", { name: /Expand trace for conc:fk_race:fk:transactions/ }).click();
  const evidence = page.getByTestId("concurrency-evidence");
  await expect(evidence).toBeVisible({ timeout: 45_000 });
  await expect(evidence).toContainText("generated");
  await expect(evidence).toContainText("unguarded");
  await expect(evidence).toContainText("orphan child");
  await expect(evidence).toContainText("test evidence, not proof");
});
