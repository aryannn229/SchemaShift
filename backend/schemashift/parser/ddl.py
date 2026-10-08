"""DDL handling: CREATE TABLE / ALTER TABLE ADD CONSTRAINT / CREATE INDEX / CREATE TYPE."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import cast

from sqlglot import exp

from schemashift.models.schema import (
    CheckConstraint,
    Column,
    DefaultExpr,
    EnumType,
    ForeignKey,
    Index,
    PrimaryKey,
    RefAction,
    Schema,
    Table,
    UniqueConstraint,
)
from schemashift.models.source import Diagnostic, SourceSpan
from schemashift.parser import errors
from schemashift.parser.errors import ParseError, unsupported
from schemashift.parser.splitter import RawStatement, SourceMap, split_body_segments
from schemashift.parser.statement import neutralize, parse_sql
from schemashift.parser.types import UnsupportedTypeError, normalize_type

_ACTION_RE = re.compile(
    r"ON\s+(DELETE|UPDATE)\s+(NO\s+ACTION|RESTRICT|CASCADE|SET\s+NULL|SET\s+DEFAULT)", re.I
)
_DEFERRABLE_RE = re.compile(r"^DEFERRABLE$", re.I)
_INHERITS_RE = re.compile(r"\bINHERITS\b", re.I)
_PARTITION_RE = re.compile(r"\bPARTITION\s+(?:BY|OF)\b", re.I)
_EXCLUDE_RE = re.compile(r"\bEXCLUDE\s+(?:USING\b|\()", re.I)
_ALTER_RE = re.compile(
    r"^ALTER\s+TABLE\s+(?:ONLY\s+)?(?:IF\s+EXISTS\s+)?((?:\"[^\"]*\"|[\w$.])+)\s+", re.I
)
_ADD_CONSTRAINT_RE = re.compile(
    r"^ADD\s+(?=CONSTRAINT\b|PRIMARY\b|FOREIGN\b|UNIQUE\b|CHECK\b)", re.I
)


def ident(node: exp.Identifier) -> str:
    """Postgres identifier normalization: unquoted -> lowercase, quoted -> preserved."""
    name = str(node.name)
    return name if node.args.get("quoted") else name.lower()


def _table_name(node: exp.Expr) -> str:
    table = node if isinstance(node, exp.Table) else node.find(exp.Table)
    if table is None or not isinstance(table.this, exp.Identifier):
        raise ParseError(errors.SYNTAX_ERROR, "Syntax error: expected a table name")
    return ident(table.this)


def _identifiers(nodes: list[exp.Expr]) -> tuple[str, ...]:
    out: list[str] = []
    for n in nodes:
        if isinstance(n, exp.Identifier):
            out.append(ident(n))
        elif isinstance(n, exp.Column) and isinstance(n.this, exp.Identifier):
            out.append(ident(n.this))
        elif isinstance(n, exp.Ordered):
            out.extend(_identifiers([n.this]))
        else:
            raise ParseError(errors.SYNTAX_ERROR, f"Syntax error: unexpected {n.sql()!r}")
    return tuple(out)


def _lower_idents(node: exp.Expr) -> exp.Expr:
    def fix(n: exp.Expr) -> exp.Expr:
        if isinstance(n, exp.Identifier) and not n.args.get("quoted"):
            return exp.to_identifier(str(n.name).lower())
        return n

    return node.copy().transform(fix)


def _default(node: exp.Expr) -> DefaultExpr:
    sql = _lower_idents(node).sql(dialect="postgres", comments=False, normalize_functions=False)
    if isinstance(node, (exp.CurrentTimestamp, exp.CurrentDate, exp.CurrentTime)):
        return DefaultExpr(sql=sql, kind="now")
    if isinstance(node, exp.Anonymous) and str(node.name).lower() in ("now", "clock_timestamp"):
        return DefaultExpr(sql=sql, kind="now")
    if isinstance(node, (exp.Literal, exp.Boolean, exp.Null)) or (
        isinstance(node, exp.Neg) and isinstance(node.this, exp.Literal)
    ):
        return DefaultExpr(sql=sql, kind="literal")
    return DefaultExpr(sql=sql, kind="expression")


def _ref_action(options: list[str], which: str) -> RefAction:
    for opt in options:
        m = _ACTION_RE.fullmatch(opt.strip())
        if m and m.group(1).upper() == which:
            return cast(RefAction, " ".join(m.group(2).upper().split()))
    return "NO ACTION"


@dataclass
class _TableBuilder:
    name: str
    span: SourceSpan
    columns: list[Column] = field(default_factory=list)
    primary_key: PrimaryKey | None = None
    foreign_keys: list[ForeignKey] = field(default_factory=list)
    uniques: list[UniqueConstraint] = field(default_factory=list)
    checks: list[CheckConstraint] = field(default_factory=list)
    indexes: list[Index] = field(default_factory=list)

    def build(self) -> Table:
        pk_cols = set(self.primary_key.columns) if self.primary_key else set()
        cols = tuple(
            c.model_copy(update={"nullable": False}) if c.name in pk_cols else c
            for c in self.columns
        )
        return Table(
            name=self.name,
            columns=cols,
            primary_key=self.primary_key,
            foreign_keys=tuple(self.foreign_keys),
            uniques=tuple(self.uniques),
            checks=tuple(self.checks),
            indexes=tuple(self.indexes),
            span=self.span,
        )


class DDLBuilder:
    """Accumulates DDL statements into a ``Schema``."""

    def __init__(self) -> None:
        self._tables: dict[str, _TableBuilder] = {}
        self._enums: dict[str, EnumType] = {}
        self.diagnostics: list[Diagnostic] = []

    # ---- statement entry points -------------------------------------------------
    def handle(self, kind: str, raw: RawStatement, smap: SourceMap, span: SourceSpan) -> None:
        if kind == "create_table":
            self._create_table(raw, smap, span)
        elif kind == "create_index":
            self._create_index(raw, smap, span)
        elif kind == "create_type":
            self._create_type(raw, smap, span)
        elif kind == "alter_table":
            self._alter_table(raw, smap, span)

    # ---- CREATE TABLE -------------------------------------------------------------
    def _create_table(self, raw: RawStatement, smap: SourceMap, span: SourceSpan) -> None:
        if _INHERITS_RE.search(raw.masked):
            raise unsupported("INHERITANCE", "table inheritance (INHERITS)", span)
        if _PARTITION_RE.search(raw.masked):
            raise unsupported("PARTITIONING", "table partitioning", span)
        if _EXCLUDE_RE.search(raw.masked):
            raise unsupported("EXCLUSION_CONSTRAINT", "exclusion constraints (EXCLUDE)", span)
        ast = parse_sql(neutralize(raw), raw, smap, span)
        if not isinstance(ast, exp.Create) or ast.args.get("kind") != "TABLE":
            raise ParseError(errors.SYNTAX_ERROR, "Syntax error: expected CREATE TABLE", span)
        if ast.args.get("expression") is not None:
            raise unsupported("CREATE_TABLE_AS", "CREATE TABLE ... AS SELECT", span)
        schema_node = ast.this
        if not isinstance(schema_node, exp.Schema):
            raise ParseError(errors.SYNTAX_ERROR, "Syntax error: missing column list", span)
        name = _table_name(schema_node.this)
        if name in self._tables:
            self.diagnostics.append(
                Diagnostic(
                    severity="error",
                    code="SEM006",
                    message=f"Duplicate table '{name}'",
                    location=span,
                )
            )
            return
        builder = _TableBuilder(name=name, span=span)
        segments = split_body_segments(raw.masked, keep_empty=True)
        if any(a == b for a, b in segments):
            raise ParseError(
                errors.SYNTAX_ERROR, "Syntax error: empty element in column list", span
            )
        elements = list(schema_node.expressions)
        spans = (
            [smap.span(raw.start + a, raw.start + b) for a, b in segments]
            if len(segments) == len(elements)
            else [span] * len(elements)
        )
        for element, el_span in zip(elements, spans, strict=True):
            try:
                self._apply_element(builder, element, el_span)
            except ParseError as exc:
                self.diagnostics.append(exc.to_diagnostic())
        self._tables[name] = builder

    def _apply_element(self, tb: _TableBuilder, el: exp.Expr, span: SourceSpan) -> None:
        if isinstance(el, exp.ColumnDef):
            self._column(tb, el, span)
        elif isinstance(el, exp.Constraint):
            name = ident(el.this) if isinstance(el.this, exp.Identifier) else None
            for inner in el.expressions:
                self._table_constraint(tb, inner, span, name)
        else:
            self._table_constraint(tb, el, span, None)

    def _table_constraint(
        self, tb: _TableBuilder, node: exp.Expr, span: SourceSpan, name: str | None
    ) -> None:
        if isinstance(node, exp.PrimaryKey):
            self._set_pk(tb, _identifiers(list(node.expressions)), span)
        elif isinstance(node, exp.ForeignKey):
            ref = node.args.get("reference")
            if not isinstance(ref, exp.Reference):
                raise ParseError(
                    errors.SYNTAX_ERROR, "Syntax error: FOREIGN KEY without REFERENCES", span
                )
            tb.foreign_keys.append(self._fk(name, _identifiers(list(node.expressions)), ref, span))
        elif isinstance(node, exp.UniqueColumnConstraint):
            cols_node = node.this
            cols = (
                _identifiers(list(cols_node.expressions))
                if isinstance(cols_node, exp.Schema)
                else ()
            )
            tb.uniques.append(UniqueConstraint(name=name, columns=cols, span=span))
        elif isinstance(node, exp.CheckColumnConstraint):
            tb.checks.append(self._check(name, node, span))
        elif isinstance(node, exp.ExcludeColumnConstraint):
            raise unsupported("EXCLUSION_CONSTRAINT", "exclusion constraints (EXCLUDE)", span)
        else:
            raise ParseError(
                errors.SYNTAX_ERROR,
                f"Syntax error: unexpected table element {node.sql(dialect='postgres')!r}",
                span,
            )

    def _set_pk(self, tb: _TableBuilder, cols: tuple[str, ...], span: SourceSpan) -> None:
        if tb.primary_key is not None:
            self.diagnostics.append(
                Diagnostic(
                    severity="error",
                    code=errors.DUPLICATE_PK,
                    message=f"Table '{tb.name}' has multiple primary keys",
                    location=span,
                )
            )
            return
        tb.primary_key = PrimaryKey(columns=cols, span=span)

    def _fk(
        self,
        name: str | None,
        columns: tuple[str, ...],
        ref: exp.Reference,
        span: SourceSpan,
    ) -> ForeignKey:
        target = ref.this
        if isinstance(target, exp.Schema):
            ref_table = _table_name(target.this)
            ref_cols = _identifiers(list(target.expressions))
        else:
            ref_table = _table_name(target)
            ref_cols = ()
        options = [str(o) for o in ref.args.get("options") or []]
        return ForeignKey(
            name=name,
            columns=columns,
            ref_table=ref_table,
            ref_columns=ref_cols,
            on_delete=_ref_action(options, "DELETE"),
            on_update=_ref_action(options, "UPDATE"),
            deferrable=any(_DEFERRABLE_RE.match(o.strip()) for o in options),
            span=span,
        )

    def _check(
        self, name: str | None, node: exp.CheckColumnConstraint, span: SourceSpan
    ) -> CheckConstraint:
        return CheckConstraint(
            name=name,
            expression_sql=_lower_idents(node.this).sql(
                dialect="postgres", comments=False, normalize_functions=False
            ),
            span=span,
        )

    def _column(self, tb: _TableBuilder, node: exp.ColumnDef, span: SourceSpan) -> None:
        if not isinstance(node.this, exp.Identifier):
            raise ParseError(errors.SYNTAX_ERROR, "Syntax error: bad column name", span)
        col_name = ident(node.this)
        kind = node.args.get("kind")
        if not isinstance(kind, exp.DataType):
            raise ParseError(
                errors.SYNTAX_ERROR, f"Syntax error: column '{col_name}' has no type", span
            )
        try:
            ntype, serial = normalize_type(kind)
        except UnsupportedTypeError as exc:
            raise ParseError(errors.UNSUPPORTED_TYPE, f"Column '{col_name}': {exc}", span) from exc
        nullable = True
        default: DefaultExpr | None = None
        identity = serial
        for cc in node.args.get("constraints") or []:
            if not isinstance(cc, exp.ColumnConstraint):
                continue
            cname = ident(cc.this) if isinstance(cc.this, exp.Identifier) else None
            k = cc.args.get("kind")
            if isinstance(k, exp.PrimaryKeyColumnConstraint):
                self._set_pk(tb, (col_name,), span)
                nullable = False
            elif isinstance(k, exp.NotNullColumnConstraint):
                nullable = bool(k.args.get("allow_null"))
            elif isinstance(k, exp.UniqueColumnConstraint):
                tb.uniques.append(UniqueConstraint(name=cname, columns=(col_name,), span=span))
            elif isinstance(k, exp.DefaultColumnConstraint):
                default = _default(k.this)
            elif isinstance(k, exp.CheckColumnConstraint):
                tb.checks.append(self._check(cname, k, span))
            elif isinstance(k, exp.Reference):
                tb.foreign_keys.append(self._fk(cname, (col_name,), k, span))
            elif isinstance(k, exp.GeneratedAsIdentityColumnConstraint):
                identity = True
            else:
                self.diagnostics.append(
                    Diagnostic(
                        severity="warning",
                        code="PARSE010",
                        message=f"Ignored constraint on column '{col_name}': "
                        f"{k.sql(dialect='postgres') if k is not None else '?'}",
                        location=span,
                    )
                )
        tb.columns.append(
            Column(
                name=col_name,
                sql_type=ntype,
                nullable=nullable,
                default=default,
                is_identity=identity,
                span=span,
            )
        )

    # ---- ALTER TABLE ------------------------------------------------------------------
    def _alter_table(self, raw: RawStatement, smap: SourceMap, span: SourceSpan) -> None:
        m = _ALTER_RE.match(raw.masked)
        if not m:
            raise ParseError(errors.SYNTAX_ERROR, "Syntax error: malformed ALTER TABLE", span)
        table_text = raw.text[m.start(1) : m.end(1)]
        rest_start = m.end()
        actions: list[tuple[int, int]] = []
        depth, seg = 0, rest_start
        masked = raw.masked
        for i in range(rest_start, len(masked) + 1):
            ch = masked[i] if i < len(masked) else ","
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == "," and depth == 0:
                if masked[seg:i].strip():
                    actions.append((seg, i))
                seg = i + 1
        parts: list[str] = []
        part_spans: list[SourceSpan] = []
        for a, b in actions:
            piece = masked[a:b]
            a2 = a + (len(piece) - len(piece.lstrip()))
            b2 = a2 + len(piece.strip())
            am = _ADD_CONSTRAINT_RE.match(masked[a2:b2])
            if not am:
                self.diagnostics.append(
                    unsupported(
                        "ALTER_ACTION",
                        "only ALTER TABLE ... ADD CONSTRAINT/PRIMARY KEY/FOREIGN KEY/UNIQUE/CHECK "
                        "is supported",
                        smap.span(raw.start + a2, raw.start + b2),
                    ).to_diagnostic()
                )
                continue
            neutral = neutralize(raw)
            parts.append(neutral[a2 + am.end() - am.start() : b2])
            part_spans.append(smap.span(raw.start + a2, raw.start + b2))
        if not parts:
            return
        synthetic = RawStatement(
            f"CREATE TABLE {table_text} ({', '.join(parts)})", "", raw.start, raw.end
        )
        ast = parse_sql(synthetic.text, synthetic, smap, span, positions_valid=False)
        if not isinstance(ast, exp.Create) or not isinstance(ast.this, exp.Schema):
            raise ParseError(errors.SYNTAX_ERROR, "Syntax error in ALTER TABLE", span)
        name = _table_name(ast.this.this)
        tb = self._tables.get(name)
        if tb is None:
            raise ParseError(errors.UNKNOWN_TABLE, f"ALTER TABLE on unknown table '{name}'", span)
        elements = list(ast.this.expressions)
        if len(elements) != len(part_spans):
            part_spans = [span] * len(elements)
        for el, el_span in zip(elements, part_spans, strict=True):
            try:
                self._apply_element(tb, el, el_span)
            except ParseError as exc:
                self.diagnostics.append(exc.to_diagnostic())

    # ---- CREATE INDEX -----------------------------------------------------------------
    def _create_index(self, raw: RawStatement, smap: SourceMap, span: SourceSpan) -> None:
        ast = parse_sql(neutralize(raw), raw, smap, span)
        index = ast.this if isinstance(ast, exp.Create) else None
        if not isinstance(index, exp.Index):
            raise ParseError(errors.SYNTAX_ERROR, "Syntax error: expected CREATE INDEX", span)
        table_node = index.args.get("table")
        if table_node is None:
            raise ParseError(errors.SYNTAX_ERROR, "Syntax error: CREATE INDEX without table", span)
        table = _table_name(table_node)
        params = index.args.get("params")
        cols: list[str] = []
        where_sql: str | None = None
        if isinstance(params, exp.IndexParameters):
            for c in params.args.get("columns") or []:
                inner = c.this if isinstance(c, exp.Ordered) else c
                if not isinstance(inner, exp.Column):
                    raise unsupported(
                        "EXPRESSION_INDEX", "indexes on expressions are not supported", span
                    )
                cols.append(ident(inner.this))
            where = params.args.get("where")
            if where is not None:
                where_sql = _lower_idents(where.this).sql(
                    dialect="postgres", comments=False, normalize_functions=False
                )
        tb = self._tables.get(table)
        if tb is None:
            raise ParseError(errors.UNKNOWN_TABLE, f"CREATE INDEX on unknown table '{table}'", span)
        name = (
            ident(index.this)
            if isinstance(index.this, exp.Identifier)
            else f"{table}_{'_'.join(cols)}_idx"
        )
        tb.indexes.append(
            Index(
                name=name,
                columns=tuple(cols),
                unique=bool(ast.args.get("unique")),
                where_sql=where_sql,
                span=span,
            )
        )

    # ---- CREATE TYPE ------------------------------------------------------------------
    def _create_type(self, raw: RawStatement, smap: SourceMap, span: SourceSpan) -> None:
        ast = parse_sql(neutralize(raw), raw, smap, span)
        body = ast.args.get("expression") if isinstance(ast, exp.Create) else None
        if not isinstance(body, exp.DataType) or str(body.this) != "DType.ENUM":
            raise unsupported("STATEMENT", "only CREATE TYPE ... AS ENUM is supported", span)
        name = _table_name(cast(exp.Expr, ast.this))
        if name in self._enums:
            raise ParseError(errors.DUPLICATE_OBJECT, f"Duplicate type '{name}'", span)
        values = tuple(str(v.this) for v in body.expressions if isinstance(v, exp.Literal))
        self._enums[name] = EnumType(name=name, values=values, span=span)

    # ---- finish --------------------------------------------------------------------------
    def finish(self) -> Schema:
        """Resolve implicit FK targets and validate enum references."""
        tables: dict[str, Table] = {}
        for tname, tb in self._tables.items():
            for i, fk in enumerate(tb.foreign_keys):
                if not fk.ref_columns:
                    parent = self._tables.get(fk.ref_table)
                    if parent is not None and parent.primary_key is not None:
                        tb.foreign_keys[i] = fk.model_copy(
                            update={"ref_columns": parent.primary_key.columns}
                        )
            for col in tb.columns:
                et = col.sql_type.enum_name
                if col.sql_type.base == "ENUM" and et not in self._enums:
                    self.diagnostics.append(
                        Diagnostic(
                            severity="error",
                            code=errors.UNKNOWN_TYPE,
                            message=f"Column '{tname}.{col.name}' uses unknown type '{et}'",
                            location=col.span,
                        )
                    )
            tables[tname] = tb.build()
        return Schema(tables=tables, enums=dict(self._enums))
