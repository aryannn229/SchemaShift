# Phase 25: CI/CD integration and developer tooling

## What changed
- `schemashift check FILE... --baseline FILE | --baseline-ref REF [--policy schemashift.policy.yaml] [--format text|json|sarif|markdown]`: policy rules `SS-REGRESSION`,
  `SS-REMOVED-CONSTRAINT`, `SS-RISK`, `SS-IRREVERSIBLE`, `SS-OVERALL`; exit code 1 on error-level violations. SARIF 2.1.0 with source regions.
- `.github/actions/schemashift-check` (composite): changed `.sql` files, SARIF upload to code scanning, one updating PR comment, failure per policy. Sample workflow and policy in `docs/examples/`.
- `packages/lsp` (pygls language server, SEM diagnostics and verdict hints) and `packages/vscode` (thin client). See `docs/CI.md`.

## Results
- `tests/unit/ci/test_check.py` (11 tests): removed UNIQUE reported with table, rule and verdict (`UNIQUE removed on users.email -> ValueUniqueness SAFE->(none), rule EQ-VALUE-UNIQ`),
  policy downgrades, risk limits, irreversible steps, the policy file round trip (the documented example equals the defaults), SARIF validated against the required parts of the 2.1.0 schema,
  the CLI against a temporary git repository (baseline ref, new files, exit codes), and the structure of the Action and the workflow.
- `tests/unit/ci/test_lsp.py` (3 tests): the server over **real stdio JSON-RPC** (initialize, didOpen, publishDiagnostics, didChange, shutdown).
- `pytest` unit: 1,251 passed. `pytest -m integration` (PostgreSQL 16 + MongoDB 7 replica set): 107 passed. ruff, `ruff format`, `mypy --strict` clean.
- Frontend: tsc, eslint, Vitest 60 tests, vite build; Playwright with the databases: 16 passed (4 visual specs skipped).

## Known limitations
- The GitHub Action and workflow were validated structurally and through the CLI they call; they were not run on GitHub. The VS Code client is tested only for consistency.
- A removed UNIQUE is reported as "guarantee removed" rather than SAFE to BROKEN, because the guarantee no longer exists in the SQL (DECISIONS.md).
