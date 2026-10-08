"""Rules for uniqueness guarantees."""

from __future__ import annotations

from sqlglot import exp

from schemashift.equivalence.context import RuleContext
from schemashift.equivalence.predicates import analyze_predicate
from schemashift.equivalence.registry import Outcome, Trace, rule
from schemashift.ir.nodes import CrossEntityUniqueness, EntityUniqueness, ValueUniqueness
from schemashift.semantic.graph import Relationship


def _array_embedding(ctx: RuleContext, table: str, t: Trace) -> Relationship | None:
    """The relationship embedding ``table`` as an *array* (1:N) inside a parent, if any."""
    rel = ctx.embedding_relationship(table)
    t.check(f"{table} is embedded in a parent", rel is not None)
    if rel is None:
        return None
    is_array = t.check(
        f"{table} is embedded as an array (cardinality {rel.cardinality}, not 1:1)",
        rel.cardinality != "1:1",
    )
    return rel if is_array else None


# --------------------------------------------------------------------- EntityUniqueness
@rule(
    node_type=EntityUniqueness,
    rule_id="EQ-ENTITY-UNIQ",
    title="Primary key uniqueness",
    outcomes=(
        Outcome(
            "PK_SINGLE",
            "single-column primary key on a top-level collection",
            "SAFE",
            "The primary key {node.table}({cols}) becomes the document _id, which MongoDB "
            "keeps unique.",
            mongo_feature="_id (unique by definition)",
        ),
        Outcome(
            "PK_COMPOSITE",
            "composite primary key on a top-level collection",
            "SAFE",
            "The composite key {node.table}({cols}) is enforced by a compound unique index "
            "(documents keep a generated ObjectId _id).",
            mongo_feature="compound unique index",
        ),
        Outcome(
            "PK_FOLDED",
            "junction table folded into an array of references",
            "CHANGED",
            "The junction {node.table} is folded into an array of references, so the key "
            "({cols}) is only unique per document's array and no index enforces it.",
            "Add references with $addToSet, or keep the junction as its own collection.",
        ),
        Outcome(
            "PK_EMBEDDED",
            "table is embedded as an array inside a parent document",
            "CHANGED",
            "{node.table} is embedded as an array: a unique index cannot enforce uniqueness of "
            "({cols}) between elements of one document's array.",
            "Check for duplicates in the application before pushing to the array, or use "
            "$addToSet / an arrayFilters-guarded update.",
        ),
    ),
)
def check_entity_uniqueness(
    node: EntityUniqueness, ctx: RuleContext, t: Trace
) -> tuple[str, dict[str, str]]:
    cols = {"cols": ", ".join(node.columns)}
    is_junction = node.table in ctx.graph.junctions
    if t.check(
        "table is a junction folded into an array of references",
        is_junction and ctx.junction_decision(node.table) == "REF_ARRAY",
    ):
        return "PK_FOLDED", cols
    if _array_embedding(ctx, node.table, t) is not None:
        return "PK_EMBEDDED", cols
    if t.check("primary key has a single column", len(node.columns) == 1):
        return "PK_SINGLE", cols
    return "PK_COMPOSITE", cols


# ---------------------------------------------------------------------- ValueUniqueness
@rule(
    node_type=ValueUniqueness,
    rule_id="EQ-VALUE-UNIQ",
    title="UNIQUE constraints and unique indexes",
    outcomes=(
        Outcome(
            "EMBEDDED",
            "table is embedded as an array inside a parent document",
            "CHANGED",
            "{node.table}({cols}) lives in an embedded array: a unique index does not enforce "
            "uniqueness within one document's array.",
            "Check for duplicates in the application before inserting into the array.",
        ),
        Outcome(
            "PARTIAL_OK",
            "partial unique index whose predicate MongoDB can express",
            "SAFE",
            "The partial unique index on {node.table}({cols}) maps to a unique index with a "
            "partialFilterExpression.",
            mongo_feature="partialFilterExpression",
        ),
        Outcome(
            "PARTIAL_UNTRANSLATABLE",
            "partial unique index whose predicate MongoDB cannot express",
            "CHANGED",
            "The predicate of the partial unique index on {node.table}({cols}) uses "
            "{fragments}, which a partialFilterExpression cannot express.",
            "Enforce uniqueness for the filtered rows in the application layer.",
        ),
        Outcome(
            "NULLABLE",
            "key column is nullable",
            "CHANGED",
            "{node.table}({cols}) is nullable: PostgreSQL allows many NULLs but a MongoDB "
            "unique index treats a missing/null value as one value, so a second NULL is "
            "rejected.",
            "Use a partial unique index: partialFilterExpression {{field: {{$exists: true}}}} "
            "(generated by SchemaShift).",
            "partialFilterExpression",
        ),
        Outcome(
            "UNIQUE_INDEX",
            "top-level collection with non-null key columns",
            "SAFE",
            "{node.table}({cols}) is enforced by a unique index.",
            mongo_feature="unique index",
        ),
    ),
)
def check_value_uniqueness(
    node: ValueUniqueness, ctx: RuleContext, t: Trace
) -> tuple[str, dict[str, str]]:
    v = {"cols": ", ".join(node.columns), "fragments": ""}
    if _array_embedding(ctx, node.table, t) is not None:
        return "EMBEDDED", v
    if t.check("index has a partial predicate", node.partial_where is not None):
        assert node.partial_where is not None
        analysis = analyze_predicate(_parse(node.partial_where))
        if t.check("partial predicate is translatable", analysis.translatable):
            return "PARTIAL_OK", v
        v["fragments"] = ", ".join(analysis.fragments)
        return "PARTIAL_UNTRANSLATABLE", v
    if t.check("some key column is nullable", node.nullable):
        return "NULLABLE", v
    return "UNIQUE_INDEX", v


def _parse(sql: str) -> exp.Expr:
    import sqlglot

    return sqlglot.parse_one(sql, dialect="postgres")


# ----------------------------------------------------------------- CrossEntityUniqueness
@rule(
    node_type=CrossEntityUniqueness,
    rule_id="EQ-CROSS-UNIQ",
    title="Uniqueness spanning related entities (junction key)",
    outcomes=(
        Outcome(
            "OWN_COLLECTION",
            "junction keeps its own collection (collapses to one collection)",
            "SAFE",
            "The pair ({cols}) of {tables} lives in the single collection {node.junction}, so "
            "it is enforced as a ValueUniqueness: a compound unique index.",
            mongo_feature="compound unique index",
        ),
        Outcome(
            "EMBEDDED",
            "junction rows are embedded inside one side",
            "CHANGED",
            "The junction {node.junction} is embedded as an array, so the uniqueness of "
            "({cols}) only holds per document's array and is not enforced by an index.",
            "Use $addToSet or check for the pair before pushing to the array.",
        ),
        Outcome(
            "SPLIT",
            "junction folded into an array of references: the pair spans 2 collections",
            "BROKEN",
            "Uniqueness of ({cols}) spans the collections {tables}; no index can enforce a "
            "constraint across two collections.",
            "Keep the junction as its own collection with a compound unique index, or check "
            "the pair in the application inside a transaction.",
            "multi-document transactions",
        ),
    ),
)
def check_cross_entity_uniqueness(
    node: CrossEntityUniqueness, ctx: RuleContext, t: Trace
) -> tuple[str, dict[str, str]]:
    v = {"cols": ", ".join(node.columns), "tables": " and ".join(node.tables)}
    folded = t.check(
        "junction is folded into an array of references (REF_ARRAY)",
        ctx.junction_decision(node.junction) == "REF_ARRAY",
    )
    embedded = t.check(
        "junction rows are embedded in one of the related entities",
        ctx.is_table_embedded(node.junction),
    )
    if folded:
        return "SPLIT", v
    if embedded:
        return "EMBEDDED", v
    return "OWN_COLLECTION", v
