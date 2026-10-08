"""Synthetic seed data that respects types, NOT NULL, UNIQUE, simple CHECKs and foreign keys."""

from __future__ import annotations

import datetime as dt
import random
import uuid
from decimal import Decimal
from typing import Any

import sqlglot
from faker import Faker

from schemashift.codegen.predicate import (
    PAnd,
    PCmp,
    PIn,
    Untranslatable,
    literal_value,
    parse_predicate,
)
from schemashift.models.base import FrozenModel
from schemashift.models.query import Query, TransactionBlock
from schemashift.models.schema import Column, ForeignKey, NormalizedType, Schema, Table
from schemashift.verification.sqleval import passes_check

MAX_ROWS_PER_TABLE = 500
DEFAULT_ROWS_PER_TABLE = 50
NULL_RATE = 0.2
OMIT_DEFAULT_RATE = 0.3
EPOCH = dt.datetime(2020, 1, 1, tzinfo=dt.UTC)
SPAN_SECONDS = 6 * 365 * 24 * 3600


class SeedError(Exception):
    """Seed data cannot be generated for this schema."""


class SeedData(FrozenModel):
    seed: int
    rows: dict[str, list[dict[str, Any]]]
    omitted: dict[str, list[list[str]]] = {}  # table -> per-row columns left to the DB default
    warnings: tuple[str, ...] = ()

    def count(self) -> int:
        return sum(len(r) for r in self.rows.values())


# ----------------------------------------------------------------------- ordering
def insertion_order(schema: Schema) -> tuple[list[str], list[tuple[str, ForeignKey]]]:
    """Tables in dependency order plus the nullable FKs that were deferred to break cycles."""
    deps: dict[str, set[str]] = {
        t.name: {
            fk.ref_table
            for fk in t.foreign_keys
            if fk.ref_table in schema.tables and fk.ref_table != t.name
        }
        for t in schema.tables.values()
    }
    deferred: list[tuple[str, ForeignKey]] = []
    order: list[str] = []
    remaining = dict(deps)
    while remaining:
        ready = sorted(t for t, d in remaining.items() if not d)
        if not ready:
            # break a cycle at a nullable foreign key
            broken = False
            for name in sorted(remaining):
                for fk in schema.tables[name].foreign_keys:
                    cols = [schema.tables[name].column(c) for c in fk.columns]
                    if fk.ref_table in remaining[name] and all(
                        c is not None and c.nullable for c in cols
                    ):
                        remaining[name].discard(fk.ref_table)
                        deferred.append((name, fk))
                        broken = True
                        break
                if broken:
                    break
            if not broken:
                raise SeedError(
                    "circular NOT NULL foreign keys cannot be seeded: "
                    + ", ".join(sorted(remaining))
                )
            continue
        for name in ready:
            order.append(name)
            del remaining[name]
        for d in remaining.values():
            d.difference_update(ready)
    return order, deferred


# ------------------------------------------------------------------------ values
def _text_for(faker: Faker, name: str, idx: int) -> str:
    n = name.lower()
    if "email" in n:
        return f"{faker.user_name()}{idx}@{faker.domain_name()}"
    if "phone" in n:
        return faker.phone_number()
    if "name" in n:
        return faker.name()
    if "city" in n:
        return faker.city()
    if "street" in n or "address" in n or n.startswith("line"):
        return faker.street_address()
    if "title" in n:
        return faker.sentence(nb_words=3).rstrip(".")
    if "body" in n or "desc" in n or "note" in n or "memo" in n or "bio" in n:
        return faker.sentence()
    if "code" in n or "sku" in n or "number" in n:
        return faker.bothify("??-####").upper()
    return faker.word()


def _terms(pred: Any) -> list[Any]:
    return [t for p in pred.items for t in _terms(p)] if isinstance(pred, PAnd) else [pred]


def derive_hints(table: Table) -> dict[str, dict[str, Any]]:
    """Value constraints implied by simple CHECKs: ``enum`` lists and numeric ``min``/``max``."""
    from sqlglot import exp

    hints: dict[str, dict[str, Any]] = {}
    for check in table.checks:
        try:
            pred = parse_predicate(sqlglot.parse_one(check.expression_sql, dialect="postgres"))
        except Untranslatable:
            continue
        for term in _terms(pred):
            col_node = term.left if isinstance(term, (PIn, PCmp)) else None
            if (
                not isinstance(col_node, exp.Column)
                or table.column(str(col_node.name).lower()) is None
            ):
                continue
            name = str(col_node.name).lower()
            try:
                if isinstance(term, PIn) and not term.neg:
                    hints.setdefault(name, {})["enum"] = [
                        literal_value(i, None) for i in term.items
                    ]
                elif isinstance(term, PCmp):
                    value = literal_value(term.right, None)
                    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                        continue
                    h = hints.setdefault(name, {})
                    if term.op == "eq":
                        h["enum"] = [value]
                    elif isinstance(value, (int, float)) and term.op in ("gt", "gte"):
                        h["min"] = value + (1 if term.op == "gt" and isinstance(value, int) else 0)
                    elif isinstance(value, (int, float)) and term.op in ("lt", "lte"):
                        h["max"] = value - (1 if term.op == "lt" and isinstance(value, int) else 0)
            except Untranslatable:
                continue
    return hints


class Generator:
    def __init__(self, schema: Schema, seed: int, rows_per_table: int) -> None:
        self.schema = schema
        self.rng = random.Random(seed)
        self.faker = Faker()
        self.faker.seed_instance(seed)
        self.rows_per_table = max(1, min(rows_per_table, MAX_ROWS_PER_TABLE))
        self.rows: dict[str, list[dict[str, Any]]] = {}
        self.omitted: dict[str, list[list[str]]] = {}
        self.warnings: list[str] = []
        self.hints = {t.name: derive_hints(t) for t in schema.tables.values()}
        self.checks: dict[str, list[Any]] = {
            t.name: [sqlglot.parse_one(c.expression_sql, dialect="postgres") for c in t.checks]
            for t in schema.tables.values()
        }

    # -- scalar values --------------------------------------------------------
    def scalar(
        self, col: Column, idx: int, t: NormalizedType | None = None, table: str = ""
    ) -> Any:
        t = t or col.sql_type
        b, rng = t.base, self.rng
        hint = self.hints.get(table, {}).get(col.name, {}) if not t.is_array else {}
        if hint.get("enum"):
            return rng.choice(hint["enum"])
        lo, hi = hint.get("min"), hint.get("max")
        if (lo is not None or hi is not None) and b in ("INTEGER", "BIGINT", "SMALLINT"):
            low = int(lo if lo is not None else 0)
            return rng.randint(low, int(hi if hi is not None else low + 1000))
        if (lo is not None or hi is not None) and b in ("DECIMAL", "FLOAT", "DOUBLE"):
            low_f = float(lo if lo is not None else 0)
            high_f = float(hi if hi is not None else low_f + 1000)
            places = t.scale if (b == "DECIMAL" and t.scale) else 2
            number = round(rng.uniform(low_f, max(low_f, high_f)), places)
            return Decimal(str(number)) if b == "DECIMAL" else number
        if b in ("INTEGER", "BIGINT", "SMALLINT"):
            top = 30_000 if b == "SMALLINT" else 1_000
            return rng.randint(0, top)
        if b == "DECIMAL":
            p = t.precision or 10
            s = t.scale or 0
            digits = max(1, min(p - s, 6))
            whole = rng.randint(0, 10**digits - 1)
            if s == 0:
                return Decimal(whole)
            frac = rng.randint(0, 10**s - 1)
            return Decimal(f"{whole}.{str(frac).zfill(s)}")
        if b in ("FLOAT", "DOUBLE"):
            return round(rng.uniform(0, 1000), 2)
        if b == "BOOLEAN":
            return rng.random() < 0.5
        if b in ("TEXT", "VARCHAR", "CHAR"):
            text = _text_for(self.faker, col.name, idx)
            if t.length:
                text = (text[: t.length]).ljust(t.length, "x") if b == "CHAR" else text[: t.length]
            return text
        if b in ("TIMESTAMP", "TIMESTAMPTZ"):
            return (EPOCH + dt.timedelta(seconds=rng.randint(0, SPAN_SECONDS))).replace(
                microsecond=0
            )
        if b == "DATE":
            return (EPOCH + dt.timedelta(days=rng.randint(0, 2190))).date()
        if b == "TIME":
            return dt.time(rng.randint(0, 23), rng.randint(0, 59), rng.randint(0, 59))
        if b == "INTERVAL":
            return dt.timedelta(hours=rng.randint(1, 200))
        if b == "UUID":
            return uuid.UUID(int=rng.getrandbits(128), version=4)
        if b in ("JSON", "JSONB"):
            return {"k": rng.randint(0, 100), "tag": self.faker.word()}
        if b == "BYTEA":
            return bytes(rng.getrandbits(8) for _ in range(4))
        if b == "ENUM":
            enum = self.schema.enums.get(t.enum_name or "")
            if enum is None or not enum.values:
                raise SeedError(f"unknown enum {t.enum_name}")
            return rng.choice(enum.values)
        raise SeedError(f"cannot generate values of type {b}")

    def value(self, col: Column, idx: int, table: str = "") -> Any:
        t = col.sql_type
        if t.is_array:
            return [
                self.scalar(
                    col,
                    idx + i,
                    NormalizedType(
                        base=t.base,
                        length=t.length,
                        precision=t.precision,
                        scale=t.scale,
                        enum_name=t.enum_name,
                    ),
                )
                for i in range(self.rng.randint(0, 3))
            ]
        return self.scalar(col, idx, None, table)

    # -- rows -----------------------------------------------------------------
    def unique_keys(self, table: Table) -> list[tuple[str, ...]]:
        keys = [u.columns for u in table.uniques]
        keys += [i.columns for i in table.indexes if i.unique and i.where_sql is None]
        if table.primary_key:
            keys.append(table.primary_key.columns)
        return keys

    def generate(self, deferred: set[tuple[str, tuple[str, ...]]], order: list[str]) -> None:
        for name in order:
            self.rows[name] = []
            self.omitted[name] = []
            self.fill_table(self.schema.tables[name], deferred)

    def fill_table(self, table: Table, deferred: set[tuple[str, tuple[str, ...]]]) -> None:
        n = self.rows_per_table
        keys = self.unique_keys(table)
        seen: dict[tuple[str, ...], set[tuple[Any, ...]]] = {k: set() for k in keys}
        fk_cols = {c for fk in table.foreign_keys for c in fk.columns}
        pk = set(table.primary_key.columns) if table.primary_key else set()
        one_to_one = self.exclusive_parent_choices(table)
        attempts_budget = 60
        produced = 0
        idx = 0
        while produced < n:
            idx += 1
            row = self.try_row(
                table, idx, produced, keys, seen, fk_cols, pk, deferred, one_to_one, attempts_budget
            )
            if row is None:
                break  # parents exhausted (e.g. unique FK) or uniqueness cannot be satisfied
            values, omit = row
            self.rows[table.name].append(values)
            self.omitted[table.name].append(omit)
            for k in keys:
                if all(values.get(c) is not None for c in k):
                    seen[k].add(tuple(values[c] for c in k))
            produced += 1
        if produced == 0:
            raise SeedError(f"could not generate any row for table {table.name}")
        if produced < n:
            self.warnings.append(
                f"{table.name}: generated {produced} of {n} rows (unique keys / parents exhausted)"
            )

    def exclusive_parent_choices(self, table: Table) -> dict[tuple[str, ...], list[int]]:
        """For FKs whose columns are unique, parent rows still available (each used once)."""
        out: dict[tuple[str, ...], list[int]] = {}
        unique_sets = {frozenset(k) for k in self.unique_keys(table)}
        for fk in table.foreign_keys:
            if frozenset(fk.columns) in unique_sets and fk.ref_table in self.rows:
                available = list(range(len(self.rows[fk.ref_table])))
                self.rng.shuffle(available)
                out[fk.columns] = available
        return out

    def try_row(
        self,
        table: Table,
        idx: int,
        produced: int,
        keys: list[tuple[str, ...]],
        seen: dict[tuple[str, ...], set[tuple[Any, ...]]],
        fk_cols: set[str],
        pk: set[str],
        deferred: set[tuple[str, tuple[str, ...]]],
        exclusive: dict[tuple[str, ...], list[int]],
        budget: int,
    ) -> tuple[dict[str, Any], list[str]] | None:
        for attempt in range(budget):
            row: dict[str, Any] = {}
            omit: list[str] = []
            for col in table.columns:
                if col.name in fk_cols:
                    continue
                if col.is_identity or (
                    col.name in pk and col.sql_type.base in ("INTEGER", "BIGINT", "SMALLINT")
                ):
                    row[col.name] = produced + 1
                    continue
                if (
                    col.default is not None
                    and col.default.kind != "expression"
                    and self.rng.random() < OMIT_DEFAULT_RATE
                ):
                    omit.append(col.name)
                    row[col.name] = None  # filled in from the database default by the runner
                    continue
                if col.nullable and self.rng.random() < NULL_RATE:
                    row[col.name] = None
                    continue
                row[col.name] = self.value(col, produced * 7 + attempt, table.name)
            if not self.assign_foreign_keys(table, row, deferred, exclusive):
                return None
            # defaults are evaluated by PostgreSQL; checks only see explicit values
            if not all(
                passes_check(chk, {k: v for k, v in row.items() if k not in omit})
                for chk in self.checks[table.name]
            ):
                continue
            ok = True
            for k in keys:
                if all(row.get(c) is not None for c in k) and tuple(row[c] for c in k) in seen[k]:
                    ok = False
                    break
            if ok:
                return row, omit
        return None

    def assign_foreign_keys(
        self,
        table: Table,
        row: dict[str, Any],
        deferred: set[tuple[str, tuple[str, ...]]],
        exclusive: dict[tuple[str, ...], list[int]],
    ) -> bool:
        for fk in table.foreign_keys:
            cols = [table.column(c) for c in fk.columns]
            nullable = all(c is not None and c.nullable for c in cols)
            parents = self.rows.get(fk.ref_table, [])
            is_deferred = (table.name, fk.columns) in deferred
            if is_deferred or (not parents and fk.ref_table == table.name):
                if not nullable and not parents:
                    # self reference, no row yet: the first row references itself
                    for c, rc in zip(fk.columns, fk.ref_columns, strict=True):
                        row[c] = row.get(rc)
                    continue
                for c in fk.columns:
                    row[c] = None
                continue
            if nullable and (not parents or self.rng.random() < NULL_RATE):
                for c in fk.columns:
                    row[c] = None
                continue
            if not parents:
                return False
            if fk.columns in exclusive:
                if not exclusive[fk.columns]:
                    return False
                parent = parents[exclusive[fk.columns].pop()]
            else:
                parent = self.rng.choice(parents)
            for c, rc in zip(fk.columns, fk.ref_columns, strict=True):
                row[c] = parent.get(rc)
        return True


def generate_seed(
    schema: Schema, seed: int = 0, rows_per_table: int = DEFAULT_ROWS_PER_TABLE
) -> SeedData:
    """Reproducible synthetic data (same ``seed`` => same rows)."""
    order, deferred_fks = insertion_order(schema)
    gen = Generator(schema, seed, rows_per_table)
    gen.generate({(t, fk.columns) for t, fk in deferred_fks}, order)
    return SeedData(seed=seed, rows=gen.rows, omitted=gen.omitted, warnings=tuple(gen.warnings))


# ---------------------------------------------------------------- user supplied INSERTs
def seed_from_inserts(
    schema: Schema, inserts: tuple[Query | TransactionBlock, ...], seed: int = 0
) -> SeedData:
    """User INSERT ... VALUES statements -> row dicts (bound as parameters, never run raw)."""
    from sqlglot import exp

    from schemashift.codegen.predicate import literal_value
    from schemashift.optimizer.workload import flatten, write_target

    rows: dict[str, list[dict[str, Any]]] = {name: [] for name in schema.tables}
    warnings: list[str] = []
    for q in flatten(inserts):
        if q.kind != "INSERT":
            warnings.append(f"{q.id}: only INSERT statements are used as seed data")
            continue
        name = write_target(q)
        table = schema.tables.get(name or "")
        values = q.ast.args.get("expression")
        if table is None or not isinstance(values, exp.Values):
            warnings.append(f"{q.id}: unsupported INSERT ignored")
            continue
        target = q.ast.this
        cols = (
            [str(i.name).lower() for i in target.expressions if isinstance(i, exp.Identifier)]
            if isinstance(target, exp.Schema) and target.expressions
            else [c.name for c in table.columns]
        )
        for tup in values.expressions:
            items = tup.expressions if isinstance(tup, exp.Tuple) else [tup]
            row = {}
            for cname, node in zip(cols, items, strict=True):
                column = table.column(cname)
                if column is None:
                    raise SeedError(f"unknown column {cname} in seed INSERT")
                row[cname] = _coerce(literal_value(node, column.sql_type), column.sql_type)
            rows[table.name].append(row)
    rows = {k: v for k, v in rows.items() if v}
    return SeedData(
        seed=seed,
        rows=rows,
        omitted={k: [[] for _ in v] for k, v in rows.items()},
        warnings=tuple(warnings),
    )


def _coerce(value: Any, t: NormalizedType) -> Any:
    from schemashift.codegen.values import DateValue, UuidValue

    if isinstance(value, DateValue):
        parsed = dt.datetime.fromisoformat(value.iso)
        return parsed.date() if t.base == "DATE" else parsed
    if isinstance(value, UuidValue):
        return uuid.UUID(value.text)
    if t.base == "DECIMAL" and isinstance(value, (int, float)):
        return Decimal(str(value))
    if t.base == "DATE" and isinstance(value, str):
        return dt.date.fromisoformat(value)
    return value
