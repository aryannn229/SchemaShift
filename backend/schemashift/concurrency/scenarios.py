"""Concurrency scenarios: the interleavings a guarantee must survive, as pure data.

A scenario is not part of the guarantee IR (so the labeled corpus is unaffected); it is derived from
it. Each family has its own node type so the rule registry can hold one rule per family:

* ``FkRaceScenario``       a child insert races a delete of its parent (referential integrity);
* ``DuplicateInsertScenario`` two inserts of the same key race (uniqueness);
* ``ArrayWriteScenario``   concurrent writes to one embedded array;
* ``TransactionScenario``  a multi-collection transaction meets write conflicts and retries;
* ``CounterScenario``      concurrent id generation.
"""

from __future__ import annotations

from typing import Literal

from schemashift.equivalence.context import RuleContext
from schemashift.ir.nodes import (
    AutoIncrement,
    EntityUniqueness,
    IRNode,
    IRProgram,
    MultiEntityAtomicity,
    ValueUniqueness,
)
from schemashift.semantic.graph import SchemaGraph


class FkRaceScenario(IRNode):
    node_type: Literal["conc_fk_race"] = "conc_fk_race"
    child: str
    parent: str
    fk_ref: str


class DuplicateInsertScenario(IRNode):
    node_type: Literal["conc_duplicate_insert"] = "conc_duplicate_insert"
    table: str
    columns: tuple[str, ...]
    primary: bool


class ArrayWriteScenario(IRNode):
    node_type: Literal["conc_array_write"] = "conc_array_write"
    table: str
    host: str


class TransactionScenario(IRNode):
    node_type: Literal["conc_transaction"] = "conc_transaction"
    tables: tuple[str, ...]
    txn_id: str


class CounterScenario(IRNode):
    node_type: Literal["conc_counter"] = "conc_counter"
    table: str
    column: str


Scenario = (
    FkRaceScenario
    | DuplicateInsertScenario
    | ArrayWriteScenario
    | TransactionScenario
    | CounterScenario
)


def build_scenarios(
    graph: SchemaGraph, program: IRProgram, ctx: RuleContext
) -> tuple[Scenario, ...]:
    """Every concurrency scenario the schema, its guarantees and the placement plan give rise to."""
    out: list[Scenario] = []
    for rel in sorted(graph.relationships.values(), key=lambda r: r.id):
        out.append(
            FkRaceScenario(
                id=f"conc:fk_race:{rel.id}",
                origin="foreign key",
                child=rel.child,
                parent=rel.parent,
                fk_ref=rel.id,
            )
        )
    for node in program.nodes:
        if isinstance(node, (EntityUniqueness, ValueUniqueness)):
            cols = tuple(node.columns)
            out.append(
                DuplicateInsertScenario(
                    id=f"conc:dup:{node.table}.{','.join(cols)}",
                    origin="primary key"
                    if isinstance(node, EntityUniqueness)
                    else "unique constraint",
                    table=node.table,
                    columns=cols,
                    primary=isinstance(node, EntityUniqueness),
                )
            )
        elif isinstance(node, MultiEntityAtomicity):
            out.append(
                TransactionScenario(
                    id=f"conc:txn:{node.txn_id}",
                    origin="BEGIN ... COMMIT",
                    tables=node.tables,
                    txn_id=node.txn_id,
                )
            )
        elif isinstance(node, AutoIncrement):
            out.append(
                CounterScenario(
                    id=f"conc:counter:{node.table}.{node.column}",
                    origin="SERIAL / IDENTITY",
                    table=node.table,
                    column=node.column,
                )
            )
    for table in sorted(graph.schema.tables):
        emb = ctx.embedding_relationship(table)
        if emb is not None:
            out.append(
                ArrayWriteScenario(
                    id=f"conc:array:{table}", origin="embedding", table=table, host=emb.parent
                )
            )
    return tuple(out)
