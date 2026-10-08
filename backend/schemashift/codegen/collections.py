"""Collections and ``$jsonSchema`` validators.

SQL NULL is represented by an *absent* field: nullable columns are simply not ``required`` and
their ``bsonType`` excludes ``null``. CHECK constraints become ``$jsonSchema`` keywords when they
constrain one column against literals, otherwise null-tolerant ``$expr`` validators.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp

from schemashift.codegen.layout import TableLayout, children_of, roots
from schemashift.codegen.predicate import (
    ColRef,
    PAnd,
    PCmp,
    PIn,
    PLike,
    Pred,
    Untranslatable,
    like_to_regex,
    literal_value,
    parse_predicate,
    to_expr,
)
from schemashift.models.base import FrozenModel
from schemashift.models.schema import CheckConstraint, Column, NormalizedType, Schema, Table

SMALLINT_RANGE = (-32768, 32767)


class CollectionSpec(FrozenModel):
    name: str
    table: str
    validator: dict[str, Any]
    validation_level: str = "strict"
    validation_action: str = "error"
    id_description: str = ""


@dataclass
class UntranslatedCheck:
    table: str
    name: str | None
    sql: str
    fragment: str


@dataclass
class CollectionsResult:
    collections: list[CollectionSpec] = field(default_factory=list)
    untranslated: list[UntranslatedCheck] = field(default_factory=list)


# ---------------------------------------------------------------------- column types
def bson_schema(t: NormalizedType, schema: Schema, uuid_as: str = "binary") -> dict[str, Any]:
    """``$jsonSchema`` fragment for a SQL type (never allows null; absence means NULL)."""
    base = _scalar_schema(t, schema, uuid_as)
    if t.is_array:
        return {"bsonType": "array", "items": base}
    return base


def _scalar_schema(t: NormalizedType, schema: Schema, uuid_as: str) -> dict[str, Any]:
    b = t.base
    if b == "INTEGER":
        return {"bsonType": "int"}
    if b == "SMALLINT":
        return {"bsonType": "int", "minimum": SMALLINT_RANGE[0], "maximum": SMALLINT_RANGE[1]}
    if b == "BIGINT":
        return {"bsonType": ["int", "long"]}
    if b == "DECIMAL":
        return {"bsonType": "decimal"}
    if b in ("FLOAT", "DOUBLE"):
        return {"bsonType": "number"}
    if b == "BOOLEAN":
        return {"bsonType": "bool"}
    if b in ("TEXT", "TIME", "INTERVAL"):
        return {"bsonType": "string"}
    if b in ("VARCHAR", "CHAR"):
        out: dict[str, Any] = {"bsonType": "string"}
        if t.length:
            out["maxLength"] = t.length
        return out
    if b == "UUID":
        return {"bsonType": "string"} if uuid_as == "string" else {"bsonType": "binData"}
    if b in ("DATE", "TIMESTAMP", "TIMESTAMPTZ"):
        return {"bsonType": "date"}
    if b == "BYTEA":
        return {"bsonType": "binData"}
    if b == "ENUM":
        values = schema.enums[t.enum_name].values if t.enum_name in schema.enums else ()
        return {"bsonType": "string", "enum": list(values)}
    return {}  # JSON / JSONB: any value


# --------------------------------------------------------------------- check mapping
@dataclass
class _Keywords:
    """JSON-schema keywords collected for one column, plus predicates needing $expr."""

    per_column: dict[str, dict[str, Any]] = field(default_factory=dict)
    exprs: list[Pred] = field(default_factory=list)


def _jsonschema_keywords(
    pred: Pred, table: Table, schema: Schema
) -> tuple[str, dict[str, Any]] | None:
    """Map one AND-term to ``(column, keywords)`` or None when it needs ``$expr``."""
    col: exp.Column | None = None
    if isinstance(pred, PCmp):
        left, right, op = pred.left, pred.right, pred.op
        length = isinstance(left, exp.Length)
        inner = left.this if isinstance(left, exp.Length) else left
        if isinstance(inner, exp.Column) and isinstance(right, (exp.Literal, exp.Neg, exp.Paren)):
            col = inner
        else:
            return None
        column = table.column(
            str(col.name).lower() if not col.this.args.get("quoted") else str(col.name)
        )
        if column is None:
            return None
        try:
            value = literal_value(right, column.sql_type)
        except Untranslatable:
            return None
        if length:
            if column.sql_type.base not in ("TEXT", "VARCHAR", "CHAR") or not isinstance(
                value, int
            ):
                return None
            return column.name, _bound(op, value, "minLength", "maxLength", int_adjust=True)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            if op == "eq" and isinstance(value, (str, bool)):
                return column.name, {"enum": [value]}
            return None
        if column.sql_type.base in ("DATE", "TIMESTAMP", "TIMESTAMPTZ", "BOOLEAN"):
            return None
        return column.name, _bound(op, value, "minimum", "maximum")
    if isinstance(pred, PIn) and not pred.neg:
        inner = pred.left
        if not isinstance(inner, exp.Column):
            return None
        column = table.column(str(inner.name).lower())
        if column is None:
            return None
        try:
            values = [literal_value(i, column.sql_type) for i in pred.items]
        except Untranslatable:
            return None
        if any(v is None or not isinstance(v, (str, int, float, bool)) for v in values):
            return None
        return column.name, {"enum": values}
    if isinstance(pred, PLike) and not pred.neg and not pred.ci:
        inner = pred.left
        if isinstance(inner, exp.Column) and isinstance(pred.pattern, exp.Literal):
            column = table.column(str(inner.name).lower())
            if column is not None and column.sql_type.base in ("TEXT", "VARCHAR", "CHAR"):
                return column.name, {"pattern": like_to_regex(str(pred.pattern.this))}
    return None


def _bound(op: str, value: Any, lo: str, hi: str, int_adjust: bool = False) -> dict[str, Any]:
    if op == "eq":
        return {lo: value, hi: value}
    if op in ("gte", "gt"):
        if int_adjust:
            return {lo: value + 1 if op == "gt" else value}
        return {lo: value} if op == "gte" else {lo: value, "exclusiveMinimum": True}
    if op in ("lte", "lt"):
        if int_adjust:
            return {hi: value - 1 if op == "lt" else value}
        return {hi: value} if op == "lte" else {hi: value, "exclusiveMaximum": True}
    return {}


def _split_and(pred: Pred) -> list[Pred]:
    if isinstance(pred, PAnd):
        out: list[Pred] = []
        for item in pred.items:
            out.extend(_split_and(item))
        return out
    return [pred]


def _merge_keywords(target: dict[str, Any], new: dict[str, Any]) -> None:
    for key, value in new.items():
        if key in ("minimum", "minLength") and key in target:
            target[key] = max(target[key], value)
        elif key in ("maximum", "maxLength") and key in target:
            target[key] = min(target[key], value)
        elif key == "enum" and key in target:
            target[key] = [v for v in target[key] if v in value]
        else:
            target.setdefault(key, value)


# ----------------------------------------------------------------------- build
def build_collections(
    schema: Schema, layouts: dict[str, TableLayout], uuid_as: str = "binary"
) -> CollectionsResult:
    result = CollectionsResult()
    for root in roots(layouts):
        spec, untranslated = _collection(schema, layouts, root, uuid_as)
        result.collections.append(spec)
        result.untranslated.extend(untranslated)
    return result


def _collection(
    schema: Schema, layouts: dict[str, TableLayout], root: TableLayout, uuid_as: str
) -> tuple[CollectionSpec, list[UntranslatedCheck]]:
    table = schema.tables[root.table]
    untranslated: list[UntranslatedCheck] = []
    expr_checks: list[Any] = []
    body = _document_schema(schema, layouts, root, uuid_as, untranslated, expr_checks, chain=[root])
    props: dict[str, Any] = body["properties"]
    if root.id_column is not None:
        id_col = table.column(root.id_column)
        assert id_col is not None
        props = {"_id": bson_schema(id_col.sql_type, schema, uuid_as), **props}
        id_desc = f"_id = {root.table}.{root.id_column}"
    else:
        props = {"_id": {"bsonType": "objectId"}, **props}
        key = ", ".join(table.primary_key.columns) if table.primary_key else "none"
        id_desc = f"_id = generated ObjectId (SQL primary key: {key})"
    body["properties"] = props
    body["additionalProperties"] = False
    validator: dict[str, Any] = {"$jsonSchema": body}
    if expr_checks:
        validator = {"$and": [validator, *({"$expr": e} for e in expr_checks)]}
    return (
        CollectionSpec(
            name=root.collection, table=root.table, validator=validator, id_description=id_desc
        ),
        untranslated,
    )


def _document_schema(
    schema: Schema,
    layouts: dict[str, TableLayout],
    layout: TableLayout,
    uuid_as: str,
    untranslated: list[UntranslatedCheck],
    expr_checks: list[Any],
    chain: list[TableLayout],
) -> dict[str, Any]:
    """``$jsonSchema`` object schema for one table's document (root or sub-document)."""
    table = schema.tables[layout.table]
    props: dict[str, Any] = {}
    required: list[str] = []
    for col in table.columns:
        if col.name not in layout.fields:
            continue
        name = layout.fields[col.name]
        if name == "_id":
            continue
        props[name] = bson_schema(col.sql_type, schema, uuid_as)
        if not col.nullable:
            required.append(name)
    _apply_checks(schema, layouts, layout, table, props, expr_checks, untranslated, chain)
    for child in children_of(layouts, layout.table):
        props[child.path[-1]] = _embedded_schema(
            schema, layouts, child, uuid_as, untranslated, expr_checks, [*chain, child]
        )
    body: dict[str, Any] = {"bsonType": "object"}
    if required:
        body["required"] = required
    body["properties"] = props
    return body


def _embedded_schema(
    schema: Schema,
    layouts: dict[str, TableLayout],
    child: TableLayout,
    uuid_as: str,
    untranslated: list[UntranslatedCheck],
    expr_checks: list[Any],
    chain: list[TableLayout],
) -> dict[str, Any]:
    if child.kind == "ref_scalars":
        col = schema.tables[child.table].column(child.scalar_column or "")
        assert col is not None
        return {
            "bsonType": "array",
            "items": bson_schema(col.sql_type, schema, uuid_as),
            "uniqueItems": True,
        }
    doc = _document_schema(schema, layouts, child, uuid_as, untranslated, expr_checks, chain)
    doc["additionalProperties"] = False
    if child.kind == "object":
        return doc
    return {"bsonType": "array", "items": doc}


def _apply_checks(
    schema: Schema,
    layouts: dict[str, TableLayout],
    layout: TableLayout,
    table: Table,
    props: dict[str, Any],
    expr_checks: list[Any],
    untranslated: list[UntranslatedCheck],
    chain: list[TableLayout],
) -> None:
    for check in table.checks:
        node = sqlglot.parse_one(check.expression_sql, dialect="postgres")
        try:
            pred = parse_predicate(node)
            terms = _split_and(pred)
        except Untranslatable as exc:
            untranslated.append(
                UntranslatedCheck(table.name, check.name, check.expression_sql, exc.fragment)
            )
            continue
        remaining: list[Pred] = []
        for term in terms:
            mapped = _jsonschema_keywords(term, table, schema)
            if (
                mapped is not None
                and mapped[0] in layout.fields
                and layout.fields[mapped[0]] in props
            ):
                _merge_keywords(props[layout.fields[mapped[0]]], mapped[1])
            else:
                remaining.append(term)
        if not remaining:
            continue
        try:
            expr_checks.append(_expr_validator(remaining, table, layout, chain, layouts))
        except Untranslatable as exc:
            untranslated.append(
                UntranslatedCheck(table.name, check.name, check.expression_sql, exc.fragment)
            )


def _expr_validator(
    preds: list[Pred],
    table: Table,
    layout: TableLayout,
    chain: list[TableLayout],
    layouts: dict[str, TableLayout],
) -> Any:
    """Null-tolerant ``$expr`` evaluated per root document, iterating through array levels."""

    def build(level: int, var: str, prefix: str, parent: tuple[str, str]) -> Any:
        if level == len(chain) - 1:

            def resolve(col: exp.Column) -> ColRef:
                name = str(col.name) if col.this.args.get("quoted") else str(col.name).lower()
                column = table.column(name)
                if column is None:
                    raise Untranslatable(col.sql(dialect="postgres"))
                if name in layout.dropped_columns and layout.host:
                    host = layouts[layout.host]
                    hcol = layout.host_columns[layout.dropped_columns.index(name)]
                    return ColRef(
                        path=parent[1] + host.fields[hcol], type=column.sql_type, var=parent[0]
                    )
                if name not in layout.fields:
                    raise Untranslatable(col.sql(dialect="postgres"))
                return ColRef(path=prefix + layout.fields[name], type=column.sql_type, var=var)

            parts = [to_expr(p, resolve, "check") for p in preds]
            return parts[0] if len(parts) == 1 else {"$and": parts}
        nxt = chain[level + 1]
        field_name = nxt.path[-1]
        if nxt.kind == "object":
            return build(level + 1, var, f"{prefix}{field_name}.", (var, prefix))
        elem = f"$$e{level + 1}."
        inner = build(level + 1, elem, "", (var, prefix))
        return {
            "$allElementsTrue": [
                {
                    "$map": {
                        "input": {"$ifNull": [f"{var}{prefix}{field_name}", []]},
                        "as": f"e{level + 1}",
                        "in": inner,
                    }
                }
            ]
        }

    return build(0, "$", "", ("$", ""))


def check_label(check: CheckConstraint) -> str:
    return check.name or check.expression_sql


def column_of(table: Table, name: str) -> Column | None:
    return table.column(name)
