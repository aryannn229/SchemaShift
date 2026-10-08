"""SQL parser: text -> Schema + Queries + Diagnostics. Pure, never raises on bad SQL."""

from __future__ import annotations

from typing import Literal

from schemashift.models.base import FrozenModel
from schemashift.models.query import Query, TransactionBlock
from schemashift.models.schema import Schema
from schemashift.models.source import Diagnostic, SourceSpan
from schemashift.parser import errors
from schemashift.parser.ddl import DDLBuilder
from schemashift.parser.errors import ParseError, unsupported
from schemashift.parser.queries import build_query
from schemashift.parser.splitter import SourceMap, split_statements
from schemashift.parser.statement import DDL_CLASSES, classify

Mode = Literal["all", "ddl", "queries"]


class ParseResult(FrozenModel):
    schema_: Schema
    queries: tuple[Query | TransactionBlock, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()

    @property
    def has_errors(self) -> bool:
        return any(d.severity == "error" for d in self.diagnostics)

    def all_queries(self) -> tuple[Query, ...]:
        """Flat list of queries including those inside transaction blocks."""
        flat: list[Query] = []
        for q in self.queries:
            if isinstance(q, TransactionBlock):
                flat.extend(q.statements)
            else:
                flat.append(q)
        return tuple(flat)


def _ordered(diags: list[Diagnostic]) -> list[Diagnostic]:
    def key(d: Diagnostic) -> tuple[int, int]:
        loc = d.location
        return (loc.line_start, loc.col_start) if loc else (10**9, 0)

    return sorted(diags, key=key)


def parse(sql: str, mode: Mode = "all") -> ParseResult:
    """Parse a multi-statement script. One bad statement never stops the rest."""
    smap = SourceMap(sql)
    ddl = DDLBuilder()
    diagnostics: list[Diagnostic] = []
    queries: list[Query | TransactionBlock] = []
    open_txn: list[Query] | None = None
    txn_start: SourceSpan | None = None
    qcount = tcount = 0

    for raw in split_statements(sql):
        span = smap.span(raw.start, raw.end)
        try:
            cls = classify(raw, span)
            is_ddl = cls in DDL_CLASSES
            if (mode == "ddl" and not is_ddl) or (mode == "queries" and is_ddl):
                diagnostics.append(
                    Diagnostic(
                        severity="info",
                        code=errors.IGNORED,
                        message=f"{cls} statement ignored in {mode} input",
                        location=span,
                    )
                )
                continue
            if is_ddl:
                ddl.handle(cls, raw, smap, span)
            elif cls == "txn_begin":
                if open_txn is not None:
                    diagnostics.append(
                        Diagnostic(
                            severity="warning",
                            code=errors.TXN_STRUCTURE,
                            message="Nested BEGIN ignored",
                            location=span,
                        )
                    )
                else:
                    open_txn, txn_start = [], span
            elif cls == "txn_end":
                if open_txn is None:
                    diagnostics.append(
                        Diagnostic(
                            severity="warning",
                            code=errors.TXN_STRUCTURE,
                            message="COMMIT without BEGIN ignored",
                            location=span,
                        )
                    )
                else:
                    if open_txn and txn_start is not None:
                        tcount += 1
                        queries.append(
                            TransactionBlock(
                                id=f"t{tcount}",
                                statements=tuple(open_txn),
                                span=SourceSpan(
                                    line_start=txn_start.line_start,
                                    col_start=txn_start.col_start,
                                    line_end=span.line_end,
                                    col_end=span.col_end,
                                ),
                            )
                        )
                    open_txn, txn_start = None, None
            elif cls == "txn_rollback":
                open_txn, txn_start = None, None
                raise unsupported(
                    "ROLLBACK", "ROLLBACK (aborted transactions) is not supported", span
                )
            else:
                qcount += 1
                query = build_query(f"q{qcount}", cls, raw, smap, span)
                if open_txn is not None:
                    open_txn.append(query)
                else:
                    queries.append(query)
        except ParseError as exc:
            diagnostics.append(exc.to_diagnostic())

    if open_txn is not None:
        diagnostics.append(
            Diagnostic(
                severity="error",
                code=errors.TXN_STRUCTURE,
                message="Transaction block was never committed; its statements were dropped",
                location=txn_start,
            )
        )
    schema = ddl.finish()
    return ParseResult(
        schema_=schema,
        queries=tuple(queries),
        diagnostics=tuple(_ordered(ddl.diagnostics + diagnostics)),
    )


def parse_ddl(sql: str) -> ParseResult:
    return parse(sql, "ddl")


def parse_queries(sql: str) -> ParseResult:
    return parse(sql, "queries")
