# Optional packages

Separately packaged editor support for SchemaShift. Neither is needed to use the compiler, the API or the CI check.

- `lsp/`: `schemashift-lsp`, a language server (pygls). `pip install ./packages/lsp` then `python -m schemashift_lsp`.
  It publishes the parser and semantic diagnostics (SEM001 ...) and a hint on the statement behind every CHANGED or BROKEN guarantee.
- `vscode/`: a small VS Code client that starts that server for `.sql` files (`npm install` then `vsce package`).
  Set `schemashift.pythonPath` to the interpreter that has `schemashift-lsp` installed.

The server is tested over real stdio JSON-RPC in `backend/tests/unit/ci/test_lsp.py` (initialize, didOpen, publishDiagnostics, shutdown).
The VS Code client is a thin launcher; it is not exercised by an automated test here.
