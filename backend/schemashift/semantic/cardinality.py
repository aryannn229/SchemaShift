"""Cardinality inference: 1:1, 1:N, junction tables (M:N) and self references."""

from __future__ import annotations

from typing import Literal

from schemashift.models.base import FrozenModel
from schemashift.models.schema import ForeignKey, Schema, Table

Cardinality = Literal["1:1", "1:N"]


class JunctionTable(FrozenModel):
    table: str
    left: str
    right: str
    left_fk: ForeignKey
    right_fk: ForeignKey
    payload_columns: tuple[str, ...]


def unique_column_sets(table: Table) -> list[frozenset[str]]:
    """Every column set that is guaranteed unique in ``table``."""
    sets: list[frozenset[str]] = []
    if table.primary_key is not None:
        sets.append(frozenset(table.primary_key.columns))
    sets.extend(frozenset(u.columns) for u in table.uniques)
    sets.extend(frozenset(i.columns) for i in table.indexes if i.unique and i.where_sql is None)
    return sets


def infer_cardinality(child: Table, fk: ForeignKey) -> Cardinality:
    """1:1 when the FK columns are themselves a PK/UNIQUE key of the child."""
    cols = frozenset(fk.columns)
    return "1:1" if cols in unique_column_sets(child) else "1:N"


def is_self_reference(table: Table, fk: ForeignKey) -> bool:
    return fk.ref_table == table.name


def detect_junction(table: Table, schema: Schema) -> JunctionTable | None:
    """Exactly 2 FKs to 2 different tables whose columns form a key, with <= 2 payload columns."""
    fks = [fk for fk in table.foreign_keys if fk.ref_table in schema.tables]
    if len(table.foreign_keys) != 2 or len(fks) != 2:
        return None
    left, right = fks
    if left.ref_table == right.ref_table or table.name in (left.ref_table, right.ref_table):
        return None
    fk_cols = frozenset(left.columns) | frozenset(right.columns)
    if fk_cols not in unique_column_sets(table):
        return None
    pk_cols = set(table.primary_key.columns) if table.primary_key else set()
    payload = tuple(
        c.name for c in table.columns if c.name not in fk_cols and c.name not in pk_cols
    )
    if len(payload) > 2:
        return None
    return JunctionTable(
        table=table.name,
        left=left.ref_table,
        right=right.ref_table,
        left_fk=left,
        right_fk=right,
        payload_columns=payload,
    )
