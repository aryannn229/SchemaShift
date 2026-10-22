import { expect, test } from "@playwright/test";

test("flipping a relationship updates verdicts, indexes and the risk score; reset restores the plan", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByLabel("Load sample").selectOption("ecommerce");
  await page.getByRole("button", { name: "Compile", exact: true }).click();
  await expect(page.getByTestId("risk-overall")).toBeVisible();
  const risk = Number(await page.getByTestId("risk-overall").innerText());
  expect(risk).toBeGreaterThanOrEqual(0);

  await page.getByRole("tab", { name: "Relationships" }).click();
  const selects = page.getByRole("combobox", { name: /^Placement of / });
  const first = selects.first();
  await expect(first).toHaveValue("optimizer");
  const label = await first.locator("option").first().innerText(); // "optimizer: EMBED" or "...: REFERENCE"
  await first.selectOption(label.includes("EMBED") ? "REFERENCE" : "EMBED");

  await expect(page.getByTestId("sim-result")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText("simulated plan").first()).toBeVisible();
  await expect(page.getByTestId("sim-risk")).toContainText(" to ");
  await expect(page.getByTestId("sim-indexes")).toBeVisible();
  await expect(page.getByTestId("sim-verdicts")).toContainText("changed");

  await page.getByRole("button", { name: "Reset to optimizer plan" }).click();
  await expect(page.getByTestId("sim-result")).toHaveCount(0);
  await expect(first).toHaveValue("optimizer");
});

test("a constraint toggle is simulated through ALTER and shown as a removed guarantee", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByLabel("Load sample").selectOption("ecommerce");
  await page.getByRole("button", { name: "Compile", exact: true }).click();
  await page.getByRole("tab", { name: "Relationships" }).click();
  await page.getByRole("checkbox", { name: /Drop UNIQUE on/ }).first().check();
  await expect(page.getByTestId("sim-verdicts")).toContainText("none", { timeout: 20_000 });
});
