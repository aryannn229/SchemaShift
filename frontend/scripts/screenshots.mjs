// Takes the screenshots used in the project presentation from a running stack:
//   BASE=http://localhost:5190 node scripts/screenshots.mjs
// The API behind BASE needs the sandbox databases for the verification screenshot.
import { mkdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

import { chromium } from "@playwright/test";

const base = process.env.BASE ?? "http://localhost:5190";
const out = resolve(process.env.OUT ?? "../docs/screenshots");
mkdirSync(out, { recursive: true });

const browser = await chromium.launch();
const context = await browser.newContext({
  viewport: { width: 1600, height: 900 },
  colorScheme: "light",
  deviceScaleFactor: 1.25,
});
const page = await context.newPage();
const shot = async (name) => {
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${out}/${name}.png` });
  console.log("wrote", name);
};
const compile = async (sample) => {
  await page.goto(base + "/");
  await page.getByLabel("Load sample").selectOption(sample);
  await page.getByRole("button", { name: "Compile", exact: true }).click();
  await page.getByTestId("overall-verdict").waitFor({ timeout: 30000 });
  await page.locator(".monaco-editor").first().waitFor({ timeout: 60000 }).catch(() => undefined);
};

// 1. Workspace: editors on the left, the Summary tab (verdict, risk, Z3) on the right
await compile("banking");
await shot("01-workspace-summary");
await page.getByTestId("smt-card").scrollIntoViewIfNeeded();
await shot("01b-summary-z3");

// 2. Verdicts with the evidence column and an expanded trace
await page.getByRole("tab", { name: "Verdicts" }).click();
await page.getByLabel("Search verdicts").fill("cascade_delete:transactions");
await page.getByRole("button", { name: /Expand trace for cascade_delete:transactions/ }).click();
await shot("02-verdicts-evidence");

// 3. Relationships graph with the decision simulator
await page.getByRole("tab", { name: "Relationships" }).click();
await page.locator(".react-flow").first().waitFor({ timeout: 30000 });
await shot("03a-relationships-graph");
const select = page.getByRole("combobox", { name: /^Placement of / }).first();
await select.selectOption({ index: 1 });
await page.getByTestId("sim-result").waitFor({ timeout: 30000 });
await page.getByTestId("sim-result").scrollIntoViewIfNeeded();
await shot("03-relationships-simulator");

// 4. Generated code: a query whose pipeline the optimizer rewrote (before / after)
await page.getByRole("tab", { name: "Generated Code" }).click();
await page.getByRole("button", { name: "Queries" }).click();
await shot("04-generated-code-optimizer");

// 5. Verification against real databases
await page.goto(base + "/");
await page.getByLabel("Load sample").selectOption("ecommerce");
await page.getByRole("button", { name: "Run verification" }).click();
await page.getByRole("tab", { name: "Verification" }).click();
await page.getByText("MATCH").first().waitFor({ timeout: 90000 });
await shot("05-verification");

// 6. Compare versions
await page.goto(base + "/compare");
await page.getByRole("button", { name: /^Compare$/ }).click();
await page.getByTestId("diff-result").waitFor({ timeout: 30000 });
await shot("06-compare-changes");
const crop = async (name) => {
  await page.waitForTimeout(400);
  await page.getByTestId("diff-result").screenshot({ path: `${out}/${name}.png` });
  console.log("wrote", name);
};
await crop("06c-compare-changes-crop");
await page.getByRole("tab", { name: /Migration plan/ }).click();
await page.getByText("backfill customers.nickname").first().click().catch(() => undefined);
await shot("07-compare-plan");
await crop("07c-compare-plan-crop");
await page.getByRole("tab", { name: /Guarantee diff/ }).click();
await shot("08-compare-guarantees");
await crop("08c-compare-guarantees-crop");

// 7. Benchmarks viewer with a recorded results file
await page.goto(base + "/benchmarks");
const results = process.env.BENCH ?? resolve("../docs/bench/blog-100k/results.json");
await page.locator('input[type="file"]').setInputFiles(results);
await page.getByTestId("bench-view").waitFor();
await shot("09-benchmarks");

// 8. About: pipeline and measured results
await page.goto(base + "/about");
await page.waitForLoadState("networkidle");
await shot("10-about");

await browser.close();
void readFileSync;
