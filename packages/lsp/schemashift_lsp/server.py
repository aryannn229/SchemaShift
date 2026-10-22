"""A minimal language server for SQL files: SchemaShift diagnostics and verdict hints in the editor.

For every open ``.sql`` document it parses the DDL, runs the semantic checks and the equivalence rules and
publishes: the parser and semantic diagnostics (SEM001 ...) as errors and warnings, and a hint (information)
on the statement behind every CHANGED or BROKEN guarantee, naming the rule and the evidence level.
Nothing is sent anywhere and no SQL is executed.
"""

from __future__ import annotations

from lsprotocol import types as lsp
from pygls.lsp.server import LanguageServer

from schemashift.models.source import SourceSpan
from schemashift.pipeline import compile_sql

SEVERITY = {
    "error": lsp.DiagnosticSeverity.Error,
    "warning": lsp.DiagnosticSeverity.Warning,
    "info": lsp.DiagnosticSeverity.Information,
}
VERDICT_SEVERITY = {"BROKEN": lsp.DiagnosticSeverity.Warning, "CHANGED": lsp.DiagnosticSeverity.Hint}
SOURCE = "schemashift"


def _range(span: SourceSpan | None) -> lsp.Range:
    """SchemaShift spans are 1-based with an exclusive end column; LSP is 0-based."""
    if span is None:
        return lsp.Range(lsp.Position(0, 0), lsp.Position(0, 1))
    return lsp.Range(
        lsp.Position(max(0, span.line_start - 1), max(0, span.col_start - 1)),
        lsp.Position(max(0, span.line_end - 1), max(0, span.col_end - 1)),
    )


def diagnostics_for(text: str) -> list[lsp.Diagnostic]:
    """Diagnostics and verdict hints for one SQL document (pure; used by the server and the tests)."""
    result = compile_sql(text)
    out: list[lsp.Diagnostic] = []
    for d in result.diagnostics:
        out.append(
            lsp.Diagnostic(
                range=_range(d.location),
                message=d.message,
                severity=SEVERITY.get(d.severity, lsp.DiagnosticSeverity.Information),
                code=d.code,
                source=SOURCE,
            )
        )
    for v in result.equivalence.final.verdicts:
        if v.status == "SAFE":
            continue
        out.append(
            lsp.Diagnostic(
                range=_range(v.source_span),
                message=f"[{v.status}, {v.evidence.lower()}] {v.reason}"
                + (f" Mitigation: {v.mitigation}" if v.mitigation else ""),
                severity=VERDICT_SEVERITY[v.status],
                code=v.rule_id,
                source=SOURCE,
            )
        )
    return out


server = LanguageServer("schemashift-lsp", "0.1.0")


def _publish(ls: LanguageServer, uri: str) -> None:
    doc = ls.workspace.get_text_document(uri)
    try:
        diagnostics = diagnostics_for(doc.source)
    except Exception as exc:  # noqa: BLE001 - an editor must never be taken down by a bad document
        diagnostics = [
            lsp.Diagnostic(
                range=_range(None),
                message=f"SchemaShift could not analyse this file: {exc}",
                severity=lsp.DiagnosticSeverity.Information,
                source=SOURCE,
            )
        ]
    ls.text_document_publish_diagnostics(
        lsp.PublishDiagnosticsParams(uri=uri, diagnostics=diagnostics)
    )


@server.feature(lsp.TEXT_DOCUMENT_DID_OPEN)
def did_open(ls: LanguageServer, params: lsp.DidOpenTextDocumentParams) -> None:
    _publish(ls, params.text_document.uri)


@server.feature(lsp.TEXT_DOCUMENT_DID_CHANGE)
def did_change(ls: LanguageServer, params: lsp.DidChangeTextDocumentParams) -> None:
    _publish(ls, params.text_document.uri)


@server.feature(lsp.TEXT_DOCUMENT_DID_SAVE)
def did_save(ls: LanguageServer, params: lsp.DidSaveTextDocumentParams) -> None:
    _publish(ls, params.text_document.uri)


def main() -> None:
    server.start_io()


if __name__ == "__main__":
    main()
