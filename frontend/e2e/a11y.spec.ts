import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

async function violations(page: Page) {
  // Colour transitions would make axe sample intermediate colours.
  await page.addStyleTag({ content: "*, *::before, *::after { transition: none !important; }" });
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  return r.violations.map(
    (v) => `${v.id}: ${v.help} (${v.nodes.length}) e.g. ${v.nodes[0]?.html.slice(0, 120)} :: ${v.nodes[0]?.any[0]?.message}`,
  );
}

for (const scheme of ["light", "dark"] as const) {
  test.describe(`axe, ${scheme}`, () => {
    test.use({ colorScheme: scheme });

    test("workspace, compiled results and every tab", async ({ page }) => {
      await page.goto("/");
      expect(await violations(page)).toEqual([]);
      await page.getByLabel("Load sample").selectOption("banking");
      await page.getByRole("button", { name: "Compile", exact: true }).click();
      await expect(page.getByTestId("overall-verdict")).toBeVisible();
      for (const tab of ["Summary", "Verdicts", "Relationships", "IR", "Generated Code"]) {
        await page.getByRole("tab", { name: tab }).click();
        expect(await violations(page), tab).toEqual([]);
      }
    });

    test("compare versions with its results, and the simulator", async ({ page }) => {
      await page.goto("/compare");
      await page.getByRole("button", { name: /^Compare$/ }).click();
      await expect(page.getByTestId("diff-result")).toBeVisible({ timeout: 20_000 });
      for (const tab of ["Changes", "Migration plan", "Guarantee diff", "Script"]) {
        await page.getByRole("tab", { name: new RegExp(tab) }).click();
        expect(await violations(page), tab).toEqual([]);
      }
      await page.goto("/");
      await page.getByLabel("Load sample").selectOption("ecommerce");
      await page.getByRole("button", { name: "Compile", exact: true }).click();
      await expect(page.getByTestId("overall-verdict")).toBeVisible();
      await page.getByRole("tab", { name: "Relationships" }).click();
      await page.getByRole("combobox", { name: /^Placement of / }).first().selectOption({ index: 1 });
      await expect(page.getByTestId("sim-result")).toBeVisible({ timeout: 20_000 });
      expect(await violations(page), "simulator").toEqual([]);
    });

    test("about, history and benchmarks", async ({ page }) => {
      for (const path of ["/about", "/history", "/benchmarks"]) {
        await page.goto(path);
        await page.waitForLoadState("networkidle");
        expect(await violations(page), path).toEqual([]);
      }
    });
  });
}

test("keyboard: skip link and tab arrows", async ({ page }) => {
  await page.goto("/");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to content" })).toBeFocused();
  await page.getByRole("tab", { name: "Summary" }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "Verdicts" })).toHaveAttribute("aria-selected", "true");
});

test("theme toggle cycles system, light, dark", async ({ page }) => {
  await page.goto("/");
  const html = page.locator("html");
  await page.getByRole("button", { name: /^Theme:/ }).click(); // system -> light
  await expect(html).toHaveAttribute("data-theme", "light");
  await page.getByRole("button", { name: /^Theme:/ }).click(); // light -> dark
  await expect(html).toHaveAttribute("data-theme", "dark");
});

test("layout holds from 1280px down to tablet width", async ({ page }) => {
  for (const width of [1280, 1024, 820]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/");
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow, `horizontal overflow at ${width}px`).toBeLessThanOrEqual(0);
  }
});
