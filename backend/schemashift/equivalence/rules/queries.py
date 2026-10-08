"""Rules for query semantics: joins and aggregates."""

from __future__ import annotations

from schemashift.equivalence.context import RuleContext
from schemashift.equivalence.registry import Outcome, Trace, rule
from schemashift.ir.nodes import AggregateSemantics, JoinSemantics

_INTEGER_LIKE = {"SMALLINT", "INTEGER", "BIGINT", "DECIMAL"}


# --------------------------------------------------------------------- JoinSemantics
@rule(
    node_type=JoinSemantics,
    rule_id="EQ-JOIN",
    title="JOIN semantics",
    outcomes=(
        Outcome(
            "INNER_EMBEDDED",
            "INNER join along an embedded relationship",
            "SAFE",
            "{node.query_id}: the INNER JOIN {left} -> {right} reads embedded data and "
            "becomes $unwind.",
            mongo_feature="$unwind",
        ),
        Outcome(
            "INNER_REFERENCED",
            "INNER join along a referenced relationship",
            "SAFE",
            "{node.query_id}: the INNER JOIN {left} -> {right} becomes $lookup + $unwind, "
            "which drops rows without a match like an inner join.",
            mongo_feature="$lookup + $unwind",
        ),
        Outcome(
            "LEFT_CLEAN",
            "LEFT join and no column of the right side is projected",
            "SAFE",
            "{node.query_id}: the LEFT JOIN {left} -> {right} becomes $lookup + $unwind with "
            "preserveNullAndEmptyArrays: true, keeping unmatched rows.",
            mongo_feature="$lookup + $unwind(preserveNullAndEmptyArrays)",
        ),
        Outcome(
            "LEFT_NULLS",
            "LEFT join that projects right-side columns",
            "CHANGED",
            "{node.query_id}: unmatched rows of the LEFT JOIN {left} -> {right} have the "
            "right-side fields missing in MongoDB instead of NULL.",
            "The generated pipeline adds a $project that maps missing fields to null "
            "explicitly; compare with missing == NULL.",
            "$lookup + $unwind(preserveNullAndEmptyArrays)",
        ),
    ),
)
def check_join(node: JoinSemantics, ctx: RuleContext, t: Trace) -> tuple[str, dict[str, str]]:
    v = {"left": node.tables[0], "right": node.tables[1]}
    embedded = t.check(
        "join follows an embedded relationship",
        node.fk_ref is not None and ctx.is_embedded(node.fk_ref),
    )
    if t.check("join is a LEFT join", node.kind == "LEFT"):
        if t.check("right-side columns are projected", node.right_columns_projected):
            return "LEFT_NULLS", v
        return "LEFT_CLEAN", v
    return ("INNER_EMBEDDED" if embedded else "INNER_REFERENCED"), v


# ---------------------------------------------------------------- AggregateSemantics
@rule(
    node_type=AggregateSemantics,
    rule_id="EQ-AGG",
    title="Aggregate / GROUP BY semantics",
    outcomes=(
        Outcome(
            "AGG_SAFE",
            "COUNT(*), COUNT(col), MIN, MAX, SUM over non-empty groups, AVG over floats",
            "SAFE",
            "{node.query_id}: the aggregates map to $group accumulators with identical "
            "results (COUNT(col) skips nulls via $sum/$cond).",
            mongo_feature="$group",
        ),
        Outcome(
            "AGG_CHANGED",
            "AVG over integer/decimal columns, or SUM over a possibly empty set",
            "CHANGED",
            "{node.query_id}: results can differ from PostgreSQL: {issues}.",
            "The harness compares AVG with a 1e-9 tolerance; wrap SUM results with "
            "$cond to return null on an empty set.",
            "$group",
        ),
    ),
)
def check_aggregate(
    node: AggregateSemantics, ctx: RuleContext, t: Trace
) -> tuple[str, dict[str, str]]:
    schema = ctx.graph.schema
    issues: list[str] = []
    t.check(
        "query uses AVG or SUM (the aggregates whose results can differ)",
        any(c.func in ("AVG", "SUM") for c in node.aggregates),
    )
    for call in node.aggregates:
        if call.func == "AVG" and "." in call.argument:
            table, col = call.argument.split(".", 1)
            column = schema.tables[table].column(col) if table in schema.tables else None
            if t.check(
                f"AVG({call.argument}) averages an exact numeric column",
                column is not None and column.sql_type.base in _INTEGER_LIKE,
            ):
                issues.append(
                    f"AVG({call.argument}) is NUMERIC in PostgreSQL but a double in MongoDB"
                )
        if call.func == "SUM":
            table = call.argument.split(".", 1)[0] if "." in call.argument else ""
            global_sum = t.check(f"SUM({call.argument}) has no GROUP BY", not node.group_by)
            outer_sum = t.check(
                f"SUM({call.argument}) sums a LEFT-joined table", table in node.left_joined_tables
            )
            if global_sum or outer_sum:
                issues.append(
                    f"SUM({call.argument}) over an empty set is NULL in PostgreSQL but 0 in MongoDB"
                )
    if issues:
        return "AGG_CHANGED", {"issues": "; ".join(dict.fromkeys(issues))}
    return "AGG_SAFE", {}
