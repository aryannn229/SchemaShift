# CI/CD integration (Phase 25)

## `schemashift check`
```
schemashift check db/schema.sql --baseline-ref origin/main          # baseline from git
schemashift check new.sql --baseline old.sql --format sarif -o out.sarif
```
Compares each file with its baseline (a file without a baseline is new and is checked against nothing), applies `schemashift.policy.yaml`
and exits 1 when an error-level rule is violated (`--no-fail` always exits 0). Formats: `text`, `json`, `sarif` (2.1.0, with source regions),
`markdown` (the pull request comment).

## Policy
`docs/examples/schemashift.policy.yaml` shows every key; the shown values are the defaults and a test asserts that.

| Policy rule | Fires when | Default |
|---|---|---|
| `SS-REGRESSION` | a guarantee verdict gets worse, `SAFE->BROKEN` ... | error for `SAFE->BROKEN`, `CHANGED->BROKEN`; warning for `SAFE->CHANGED` |
| `SS-REMOVED-CONSTRAINT` | a UNIQUE, PRIMARY KEY, FOREIGN KEY, NOT NULL or CHECK of the baseline is gone (or a table was dropped) | error for the five constraint kinds, warning for a dropped table |
| `SS-RISK` | the migration risk score rises by more than `max_increase` (10) or exceeds `max_score` | error |
| `SS-IRREVERSIBLE` | the change needs a step that cannot be undone automatically (Phase 22) | warning |
| `SS-OVERALL` | the overall verdict reaches `overall_verdict` | never |

A violation names the table, the guarantee and the equivalence rule behind it, for example
`UNIQUE removed on users.email -> ValueUniqueness SAFE->(none), rule EQ-VALUE-UNIQ`. When the SQL removes a constraint, the guarantee no longer
exists, so its verdict goes from `SAFE` to *removed* rather than to `BROKEN`; `SS-REMOVED-CONSTRAINT` covers that case.

## GitHub Action
`.github/actions/schemashift-check` (composite): finds the changed `.sql` files against the pull request base, runs the check three times
(SARIF, Markdown, text), uploads the SARIF to code scanning (`github/codeql-action/upload-sarif`), creates or updates one pull request comment
(marker `<!-- schemashift-check -->`) and fails the job when the policy found errors. Sample workflow: `docs/examples/schemashift-pr.yml`
(needs `pull-requests: write` and `security-events: write`). The Action's files are validated by a unit test, and the CLI behaviour it relies on
(git baseline, SARIF, exit codes) is tested in a temporary git repository; the workflow itself has not been run on GitHub from this repository.

## Editor support (optional packages)
`packages/lsp` is a pygls language server publishing SEM diagnostics and a hint for every CHANGED or BROKEN guarantee; it is tested over real
stdio JSON-RPC. `packages/vscode` is a thin VS Code client for it. See `packages/README.md`.
