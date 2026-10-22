# Phase F: frontend redesign

## What changed
- **Design system:** tokens in `frontend/src/index.css` (RGB variables) mapped in `tailwind.config.js`; light, dark and
  system themes (toggle cycles system, light, dark; no flash on load); Inter + JetBrains Mono bundled locally; 6px radius,
  borders instead of shadows; tabular numerals in tables and stat cards.
- **Components** (`src/components/ui`): Button, Card, Badge (+VerdictBadge, EvidenceBadge), Tabs (arrow-key navigation),
  Table (sortable, sticky header, aria-sort), Tooltip, Dialog (Escape, focus restore), Toast, Skeleton, EmptyState,
  ErrorState, CodeBlock (copy), StatCard. One icon set: lucide-react.
- **Layout:** top bar with wordmark, Compile/History/Benchmarks/About, GitHub link and theme toggle; skip link;
  resizable, collapsible editor/results split (stacks below 1024px); Compile primary, Run verification secondary.
- **Tabs:** Summary (StatCards, top findings, diagnostics), Verdicts (sort, status filter, search, expandable Why? trace),
  Relationships (layered layout, legend, EMBED solid vs REFERENCE dashed, minimap, zoom), IR, Generated Code (file tree +
  CodeBlock + download all), Verification (status timeline, per-query diffs, probes).
- **Pages:** Run (shareable; title and Open Graph tags set from the run), History (table), About (pipeline + measured
  results from `/api/v1/metrics`, labelled self-labelled), Benchmarks (upload a results JSON; charts arrive in Phase 20).
- Favicon, page titles per route, Open Graph meta, loading skeletons, empty and error states everywhere.
- Monaco: custom AA themes (the stock SQL token colours failed contrast).

## Not done on purpose
No risk-score card and no evidence badges are shown yet: the backend has neither (Phases 16 and 24). The components are
built and tested so those phases only wire data.

## Verification (commands run on 2026-10-22)
- `npx tsc --noEmit`: clean. `npm run lint`: clean. `npm run build`: no warnings (vendor split into chunks).
- `npx vitest run`: 4 files, 43 tests passed (includes 28 WCAG AA contrast checks read from the real stylesheet, both themes).
- `E2E_VERIFY=1 E2E_VISUAL=1 npx playwright test` against `vite` on :5190 and the docker backend: 14 passed:
  axe (wcag2a + wcag2aa) on the workspace, every result tab, About, History and Benchmarks in light and dark with 0
  violations; keyboard skip link and tab arrows; theme toggle; no horizontal overflow at 1280, 1024 and 820px; compile,
  verify and demo flows; visual snapshots for 8 views x 2 themes.
- Backend untouched by this phase. Python suite last run: 891 passed (baseline).
- Screenshots: `docs/progress/frontend/before` and `docs/progress/frontend/after`. In the "after" images the editor shows
  "Loading…" or a magenta mask: Monaco loads from a CDN and the snapshot masks the editor area.

## Known limitations
- Monaco itself still loads from the jsDelivr CDN (default loader); bundling it locally is a possible follow-up.
- Visual snapshots are local-only (see DECISIONS). Playwright axe/keyboard/layout tests run in CI.
- Coverage thresholds apply to backend packages only; none are configured for the frontend.
