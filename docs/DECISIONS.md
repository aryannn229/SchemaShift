# Decision Log

| Date | Decision | Reason |
|---|---|---|
| 2026-10-08 | Use `pip` + `venv` instead of `uv` | `uv` not installed on the dev machine; spec allows fallback. `pyproject.toml` stays uv-compatible. |
| 2026-10-08 | Use `npm` instead of `pnpm` | `pnpm` not installed; simplest default. |
| 2026-10-08 | `just` plus `scripts/dev.ps1` as `make` replacement | Dev machine is Windows/PowerShell; `just` is not installed so a PowerShell script offers the same targets. |
| 2026-10-08 | Repo root is the project root (no nested `schemashift/` dir) | Repo already exists at github.com/aryannn229/SchemaShift. |
| 2026-10-08 | Proceed through phases without waiting for approval; each phase still ends with tests + push | Explicit user instruction overriding Working Rule 2's "wait for continue". |
| 2026-10-08 | `.gitattributes` forces LF | Windows dev, Linux containers/CI. |
