import { readFileSync } from "node:fs";
import { resolve } from "node:path";

// Reads the real CSS tokens so the check cannot drift from the stylesheet.
const css = readFileSync(resolve(__dirname, "../src/index.css"), "utf8");

function block(selector: string): Record<string, [number, number, number]> {
  const start = css.indexOf(selector);
  const body = css.slice(css.indexOf("{", start) + 1, css.indexOf("}", start));
  const out: Record<string, [number, number, number]> = {};
  for (const m of body.matchAll(/--([\w-]+):\s*(\d+) (\d+) (\d+);/g)) {
    out[m[1]] = [Number(m[2]), Number(m[3]), Number(m[4])];
  }
  return out;
}

function luminance([r, g, b]: [number, number, number]): number {
  const f = (c: number) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}

export function ratio(a: [number, number, number], b: [number, number, number]): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

const themes = { light: block(":root {"), dark: block(':root[data-theme="dark"]') };

const PAIRS: [string, string][] = [
  ["fg", "bg"],
  ["muted", "bg"],
  ["muted", "surface"],
  ["muted", "raised"],
  ["accent", "bg"],
  ["accent-fg", "accent"],
  ["accent", "accent-soft"],
  ["safe", "safe-soft"],
  ["changed", "changed-soft"],
  ["broken", "broken-soft"],
  ["unverified", "unverified-soft"],
  ["safe", "bg"],
  ["changed", "bg"],
  ["broken", "bg"],
];

describe.each(Object.entries(themes))("%s theme tokens meet WCAG AA (4.5:1)", (_name, t) => {
  test.each(PAIRS)("%s on %s", (fg, bg) => {
    expect(t[fg], `missing token ${fg}`).toBeDefined();
    expect(t[bg], `missing token ${bg}`).toBeDefined();
    expect(ratio(t[fg], t[bg])).toBeGreaterThanOrEqual(4.5);
  });
});
