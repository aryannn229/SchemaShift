"""Minimal counterexamples: the smallest data and operation that show a CHANGED/BROKEN verdict.

For each non-SAFE verdict we know how to demonstrate (foreign-key actions, restrict, orphan inserts,
key updates) the harness builds the smallest dataset that exercises it (one row per table that matters,
plus the NOT NULL ancestors the foreign keys force, and two rows for a self-reference), loads it into a
fresh sandbox, runs the operation on both databases and records what each did. Verdicts that are
properties of the representation rather than of an operation sequence get a documented reason instead
of a counterexample, so nothing is silently missing.
"""

from __future__ import annotations

from typing import Any

from schemashift.codegen import generate_code
from schemashift.codegen.layout import build_layouts
from schemashift.ir.nodes import CascadingDelete
from schemashift.models.evidence import Counterexample, CounterexampleGap, CounterexampleStep
from schemashift.models.schema import Schema
from schemashift.models.verdict import Verdict
from schemashift.pipeline import CompileOptions, CompileResult
from schemashift.semantic.graph import Relationship
from schemashift.verification.ddl import ddl_statements
from schemashift.verification.probes import PROBES
from schemashift.verification.render import render_insert
from schemashift.verification.runner import _insertion_order, load_env
from schemashift.verification.sandbox import Sandbox, SandboxConfig
from schemashift.verification.seed import SeedData, SeedError, generate_seed

NO_GENERATOR = "no automatic counterexample generator exists for this guarantee yet"

# Why some verdicts have no operation-sequence counterexample (shown instead of a counterexample).
GAP_REASONS: dict[tuple[str, str], str] = {
    ("EQ-ATOMIC", "TRANSACTION"): (
        "the outcome is the same with transactions; what changes is cost and limits (60 s default, replica set "
        "required), which a data example cannot show"
    ),
    ("EQ-ATOMIC", "NO_TRANSACTION"): (
        "the harness runs a replica set where transactions work; the partial-failure behaviour only exists on a "
        "deployment without transactions"
    ),
    (
        "EQ-AGG",
        "AGG_CHANGED",
    ): "a representation difference (NUMERIC vs double, NULL vs 0): any verified query with the aggregate shows it",
    (
        "EQ-AUTOINC",
        "OBJECTID",
    ): "the generated ids differ by design (ObjectId vs sequence); no operation yields different data",
    (
        "EQ-AUTOINC",
        "COUNTER",
    ): "counter gaps appear only when a write fails after the counter was incremented, which needs fault injection",
    (
        "EQ-DEFAULT",
        "CONSTANT",
    ): "defaults are filled by PostgreSQL at insert time and by generated helper code in MongoDB: same stored data when the helper is used",
    (
        "EQ-DEFAULT",
        "NOW",
    ): "defaults are filled by PostgreSQL at insert time and by generated helper code in MongoDB: same stored data when the helper is used",
    (
        "EQ-DEFAULT",
        "EXPRESSION",
    ): "defaults are filled by PostgreSQL at insert time and by generated helper code in MongoDB: same stored data when the helper is used",
    (
        "EQ-TYPE",
        "LOSSY",
    ): "a representation property (offset, scale, padding, time part): see the type map",
    (
        "EQ-JOIN",
        "LEFT_NULLS",
    ): "a result-shape difference (NULL vs absent field), shown by verifying the query",
    (
        "EQ-CHECK",
        "UNTRANSLATABLE",
    ): "the predicate cannot be expressed as a validator, so a violating row depends on the unsupported construct",
    ("EQ-CROSS-UNIQ", "SPLIT"): (
        "the folded layout stores each pair once, in the host document's array, where uniqueItems rejects a "
        "duplicate, so a single-writer example shows no difference; the loss appears with concurrent writers"
    ),
    (
        "EQ-CROSS-UNIQ",
        "EMBEDDED",
    ): "uniqueness inside one document's array is not enforced by an index; needs a generator for array duplicates",
    (
        "EQ-ENTITY-UNIQ",
        "PK_FOLDED",
    ): "uniqueness inside one document's array is not enforced by an index; needs a generator for array duplicates",
    (
        "EQ-ENTITY-UNIQ",
        "PK_EMBEDDED",
    ): "uniqueness inside one document's array is not enforced by an index; needs a generator for array duplicates",
    (
        "EQ-VALUE-UNIQ",
        "EMBEDDED",
    ): "uniqueness inside one document's array is not enforced by an index; needs a generator for array duplicates",
    (
        "EQ-VALUE-UNIQ",
        "NULLABLE",
    ): "handled by a partial unique index; PostgreSQL and MongoDB agree on the data, the difference is the mechanism",
    (
        "EQ-VALUE-UNIQ",
        "PARTIAL_UNTRANSLATABLE",
    ): "the index predicate cannot be expressed in MongoDB, so no equivalent index exists to compare against",
}


def _closure(result: CompileResult, rel: Relationship, cascade_chain: bool) -> set[str]:
    """Tables a minimal example needs: the relationship, NOT NULL ancestors and, for a cascading
    delete, the cascade chain below the child."""
    schema = result.schema_
    tables = {rel.child, rel.parent}
    frontier = [rel.child] if cascade_chain else []
    while frontier:  # cascade chain below the child
        t = frontier.pop()
        for r in result.graph.children_of(t):
            if r.fk.on_delete == "CASCADE" and r.child not in tables:
                tables.add(r.child)
                frontier.append(r.child)
    changed = True
    while changed:  # ancestors forced by NOT NULL foreign keys
        changed = False
        for name in list(tables):
            for fk in schema.tables[name].foreign_keys:
                cols = [schema.tables[name].column(c) for c in fk.columns]
                if fk.ref_table not in tables and all(
                    c is not None and not c.nullable for c in cols
                ):
                    tables.add(fk.ref_table)
                    changed = True
    return tables


def minimal_seed(
    result: CompileResult, rel: Relationship, seed: int, cascade_chain: bool = False
) -> SeedData:
    """One row per needed table (two for a self-reference), consistent with every foreign key."""
    schema: Schema = result.schema_
    full = generate_seed(schema, seed, 2 if rel.is_self_reference else 1)
    keep = _closure(result, rel, cascade_chain)
    rows: dict[str, list[dict[str, Any]]] = {}
    for name in _insertion_order(schema):
        if name not in keep or name not in full.rows:
            continue
        table = schema.tables[name]
        fixed: list[dict[str, Any]] = []
        for row in full.rows[name]:
            row = dict(row)
            for fk in table.foreign_keys:
                if fk.ref_table not in keep:
                    for c in fk.columns:
                        row[c] = None  # nullable by construction of the closure
            fixed.append(row)
        rows[name] = fixed
    _link(rows, rel)  # the child row must really reference the parent row
    omitted = {k: full.omitted.get(k, [[] for _ in v]) for k, v in rows.items()}
    return SeedData(seed=seed, rows=rows, omitted=omitted)


def _link(rows: dict[str, list[dict[str, Any]]], rel: Relationship) -> None:
    """Point the (first) child row at the (first) parent row: nullable keys may have been NULL."""
    children, parents = rows.get(rel.child), rows.get(rel.parent)
    if not children or not parents:
        return
    parent_row = parents[0] if rel.child != rel.parent else parents[0]
    child_row = children[0] if rel.child != rel.parent else children[-1]
    for cc, pc in zip(rel.fk.columns, rel.fk.ref_columns, strict=True):
        child_row[cc] = parent_row.get(pc)


def _difference(outcome: Any) -> str:
    return (
        outcome.mongo.split("resulting difference: ", 1)[1]
        if "resulting difference: " in outcome.mongo
        else "no resulting difference"
    )


def counterexample_for(
    result: CompileResult,
    options: CompileOptions,
    config: SandboxConfig,
    verdict: Verdict,
    seed: int = 0,
) -> Counterexample | CounterexampleGap:
    """A minimal counterexample for one verdict, or the documented reason there is none."""
    node = result.ir.by_id(verdict.node_id)
    probe = PROBES.get(type(node))
    if probe is None:
        reason = GAP_REASONS.get((verdict.rule_id, verdict.outcome), NO_GENERATOR)
        return CounterexampleGap(verdict_id=verdict.node_id, reason=reason)
    fk_ref = getattr(node, "fk_ref", None)
    rel = result.graph.relationships.get(fk_ref) if isinstance(fk_ref, str) else None
    if rel is None:
        return CounterexampleGap(
            verdict_id=verdict.node_id,
            reason="the verdict has no foreign key to build an example around",
        )
    try:
        data = minimal_seed(result, rel, seed, cascade_chain=isinstance(node, CascadingDelete))
    except SeedError as exc:
        return CounterexampleGap(
            verdict_id=verdict.node_id, reason=f"could not build a minimal dataset: {exc}"
        )
    codegen = generate_code(result, options, run_id="counterexample")
    layouts = build_layouts(result.graph, result.plan)
    statements, _ = ddl_statements(result.schema_)
    with Sandbox(config) as sb:
        env, _, _ = load_env(result, options, sb, codegen, layouts, statements, data)
        outcome = probe(env, verdict, node)
    if outcome is None:
        return CounterexampleGap(
            verdict_id=verdict.node_id,
            reason="the operation could not be applied to the minimal dataset: the layout stores the "
            "child inside another document, or PostgreSQL itself rejects the operation because another "
            "foreign key (needed to build the example) blocks it",
        )
    if not outcome.demonstrates:
        return CounterexampleGap(
            verdict_id=verdict.node_id,
            reason="the minimal dataset does not exhibit a difference: " + outcome.mongo,
        )
    setup = tuple(
        render_insert(name, row)
        for name in _insertion_order(result.schema_)
        for row in data.rows.get(name, [])
    )
    return Counterexample(
        verdict_id=verdict.node_id,
        description=outcome.description,
        setup=setup,
        steps=(
            CounterexampleStep(
                description=outcome.description,
                sql=outcome.operation or None,
                postgres=outcome.postgres,
                mongo=outcome.mongo.split("; resulting difference", 1)[0],
            ),
        ),
        difference=_difference(outcome),
        tables_differing=tuple(outcome.tables_differing),
        rows_in_setup=len(setup),
        minimal=True,
        reproducible=True,
        seed=seed,
    )


def generate_counterexamples(
    result: CompileResult,
    options: CompileOptions,
    config: SandboxConfig,
    seed: int = 0,
    limit: int = 40,
) -> tuple[list[Counterexample], list[CounterexampleGap]]:
    """Counterexamples (or documented gaps) for every CHANGED / BROKEN verdict."""
    found: list[Counterexample] = []
    gaps: list[CounterexampleGap] = []
    for verdict in result.equivalence.final.verdicts:
        if verdict.status == "SAFE":
            continue
        if len(found) + len(gaps) >= limit:
            break
        out = counterexample_for(result, options, config, verdict, seed)
        (found if isinstance(out, Counterexample) else gaps).append(out)  # type: ignore[arg-type]
    return found, gaps


__all__ = [
    "GAP_REASONS",
    "counterexample_for",
    "generate_counterexamples",
    "minimal_seed",
]
