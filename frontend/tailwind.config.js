/** @type {import('tailwindcss').Config} */
const v = (name) => `rgb(var(--${name}) / <alpha-value>)`;

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: ["selector", '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        bg: v("bg"),
        surface: v("surface"),
        raised: v("raised"),
        line: v("line"),
        fg: v("fg"),
        muted: v("muted"),
        accent: { DEFAULT: v("accent"), fg: v("accent-fg"), soft: v("accent-soft") },
        safe: { DEFAULT: v("safe"), soft: v("safe-soft") },
        changed: { DEFAULT: v("changed"), soft: v("changed-soft") },
        broken: { DEFAULT: v("broken"), soft: v("broken-soft") },
        unverified: { DEFAULT: v("unverified"), soft: v("unverified-soft") },
      },
      fontFamily: {
        sans: ['"Inter Variable"', "Inter", "system-ui", "sans-serif"],
        mono: ['"JetBrains Mono Variable"', '"JetBrains Mono"', "ui-monospace", "monospace"],
      },
      fontSize: {
        xs: ["0.75rem", { lineHeight: "1.125rem" }],
        sm: ["0.8125rem", { lineHeight: "1.25rem" }],
        base: ["0.9375rem", { lineHeight: "1.5rem" }],
        lg: ["1.0625rem", { lineHeight: "1.625rem" }],
        xl: ["1.25rem", { lineHeight: "1.75rem" }],
        "2xl": ["1.5rem", { lineHeight: "2rem" }],
      },
      borderRadius: { DEFAULT: "6px", sm: "4px", md: "6px", lg: "8px" },
      spacing: { 4.5: "1.125rem" },
    },
  },
  plugins: [],
};
