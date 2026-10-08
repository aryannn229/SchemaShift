"""Rules for column-level guarantees: NOT NULL, CHECK, types, defaults, identity, enums."""

from __future__ import annotations

import sqlglot

from schemashift.equivalence.context import RuleContext
from schemashift.equivalence.predicates import analyze_predicate
from schemashift.equivalence.registry import Outcome, Trace, rule
from schemashift.equivalence.typemap import type_info
from schemashift.ir.nodes import (
    AutoIncrement,
    DefaultValue,
    DomainConstraint,
    EnumDomain,
    NotNullGuarantee,
    TypeGuarantee,
)


# ------------------------------------------------------------------------ NotNull
@rule(
    node_type=NotNullGuarantee,
    rule_id="EQ-NOT-NULL",
    title="NOT NULL",
    outcomes=(
        Outcome(
            "VALIDATOR",
            "always",
            "SAFE",
            "{node.table}.{node.column} is listed in $jsonSchema required and its bsonType "
            "excludes null.",
            mongo_feature="$jsonSchema required + bsonType",
        ),
    ),
)
def check_not_null(
    node: NotNullGuarantee, ctx: RuleContext, t: Trace
) -> tuple[str, dict[str, str]]:
    t.check("column is NOT NULL (or part of the primary key)", True)
    return "VALIDATOR", {}


# ------------------------------------------------------------------ DomainConstraint
@rule(
    node_type=DomainConstraint,
    rule_id="EQ-CHECK",
    title="CHECK constraints",
    outcomes=(
        Outcome(
            "SAFE_JSONSCHEMA",
            "translatable predicate on a single column",
            "SAFE",
            "CHECK ({node.expr_sql}) is enforced by $jsonSchema{where}.",
            mongo_feature="$jsonSchema",
        ),
        Outcome(
            "SAFE_EXPR",
            "translatable predicate comparing columns or using arithmetic",
            "SAFE",
            "CHECK ({node.expr_sql}) spans columns of the same row and is enforced by a "
            "$expr validator{where}.",
            mongo_feature="$expr validator",
        ),
        Outcome(
            "UNTRANSLATABLE",
            "predicate uses functions or subqueries MongoDB validators cannot express",
            "CHANGED",
            "CHECK ({node.expr_sql}) cannot be fully translated; untranslated fragment(s): "
            "{fragments}.",
            "Enforce the untranslated fragment(s) in the application layer before writes.",
        ),
    ),
)
def check_domain_constraint(
    node: DomainConstraint, ctx: RuleContext, t: Trace
) -> tuple[str, dict[str, str]]:
    analysis = analyze_predicate(sqlglot.parse_one(node.expr_sql, dialect="postgres"))
    embedded = t.check(
        f"{node.table} is embedded (validator targets the nested path)",
        ctx.is_table_embedded(node.table),
    )
    where = f" on the nested path of the embedded {node.table}" if embedded else ""
    v = {"where": where, "fragments": ", ".join(analysis.fragments)}
    if not t.check("predicate is translatable", analysis.translatable):
        return "UNTRANSLATABLE", v
    if t.check("predicate needs $expr (cross-column or arithmetic)", analysis.needs_expr):
        return "SAFE_EXPR", v
    return "SAFE_JSONSCHEMA", v


# ------------------------------------------------------------------- TypeGuarantee
@rule(
    node_type=TypeGuarantee,
    rule_id="EQ-TYPE",
    title="Column types",
    outcomes=(
        Outcome(
            "EXACT",
            "the BSON type preserves the column's values exactly",
            "SAFE",
            "{node.table}.{node.column} ({sqltype}) maps to BSON {bson}: {note}.",
            mongo_feature="bsonType",
        ),
        Outcome(
            "LOSSY",
            "the BSON type loses information (offset, scale, padding, time component)",
            "CHANGED",
            "{node.table}.{node.column} ({sqltype}) maps to BSON {bson}: {note}.",
            "{fix}.",
            "bsonType",
        ),
    ),
)
def check_type(node: TypeGuarantee, ctx: RuleContext, t: Trace) -> tuple[str, dict[str, str]]:
    info = type_info(node.type)
    base = node.type.base
    sqltype = base + ("[]" if node.type.is_array else "")
    if node.type.scale:
        sqltype = f"{base}({node.type.precision},{node.type.scale})"
    v = {"bson": info.bson, "note": info.note, "fix": info.fix, "sqltype": sqltype}
    if t.check("mapping loses information", info.lossy):
        return "LOSSY", v
    return "EXACT", v


# -------------------------------------------------------------------- DefaultValue
@rule(
    node_type=DefaultValue,
    rule_id="EQ-DEFAULT",
    title="Column defaults",
    outcomes=(
        Outcome(
            "CONSTANT",
            "constant default",
            "CHANGED",
            "MongoDB has no server-side defaults: DEFAULT {node.expr_sql} on "
            "{node.table}.{node.column} is not applied on insert.",
            "Generated insert helpers fill in {node.expr_sql} when the field is missing.",
        ),
        Outcome(
            "NOW",
            "now() / CURRENT_TIMESTAMP default",
            "CHANGED",
            "MongoDB has no server-side defaults: {node.table}.{node.column} will not be "
            "stamped with the insert time automatically.",
            "Generated insert helpers set {node.column} to the current time when missing.",
        ),
        Outcome(
            "EXPRESSION",
            "any other default expression",
            "CHANGED",
            "MongoDB has no server-side defaults and cannot evaluate {node.expr_sql} for "
            "{node.table}.{node.column}.",
            "Compute {node.expr_sql} in the application and set the field on insert.",
        ),
    ),
)
def check_default(node: DefaultValue, ctx: RuleContext, t: Trace) -> tuple[str, dict[str, str]]:
    if t.check("default is a constant", node.default_kind == "literal"):
        return "CONSTANT", {}
    if t.check("default is now()/CURRENT_TIMESTAMP", node.default_kind == "now"):
        return "NOW", {}
    return "EXPRESSION", {}


# ------------------------------------------------------------------- AutoIncrement
@rule(
    node_type=AutoIncrement,
    rule_id="EQ-AUTOINC",
    title="SERIAL / IDENTITY",
    outcomes=(
        Outcome(
            "OBJECTID",
            "preserve_integer_ids is off",
            "CHANGED",
            "{node.table}.{node.column} was a monotonic integer; it becomes an ObjectId, "
            "which is unique but not gap-free or strictly sequential.",
            "Use the generated ObjectId, or enable preserve_integer_ids.",
            "ObjectId",
        ),
        Outcome(
            "COUNTER",
            "preserve_integer_ids is on",
            "CHANGED",
            "{node.table}.{node.column} is generated from a counter collection "
            "(findOneAndUpdate $inc); ordering is kept but gaps and rollbacks differ from a "
            "PostgreSQL sequence.",
            "Always allocate ids through the generated next_id() helper.",
            "findOneAndUpdate + $inc",
        ),
    ),
)
def check_auto_increment(
    node: AutoIncrement, ctx: RuleContext, t: Trace
) -> tuple[str, dict[str, str]]:
    if t.check("preserve_integer_ids option", ctx.options.preserve_integer_ids):
        return "COUNTER", {}
    return "OBJECTID", {}


# ---------------------------------------------------------------------- EnumDomain
@rule(
    node_type=EnumDomain,
    rule_id="EQ-ENUM",
    title="ENUM types",
    outcomes=(
        Outcome(
            "ENUM_LIST",
            "always",
            "SAFE",
            "{node.table}.{node.column} is restricted to {values} by $jsonSchema enum.",
            mongo_feature="$jsonSchema enum",
        ),
    ),
)
def check_enum(node: EnumDomain, ctx: RuleContext, t: Trace) -> tuple[str, dict[str, str]]:
    t.check("column uses an ENUM type", True)
    return "ENUM_LIST", {"values": ", ".join(node.values)}
