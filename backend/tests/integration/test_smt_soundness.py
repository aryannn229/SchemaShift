"""Phase 26 soundness: Z3's answers are confirmed by real PostgreSQL 16 and real MongoDB 7.

For random single-table schemas drawn from the supported fragment:
* every counterexample row is inserted into both databases and must behave exactly as claimed
  (PostgreSQL rejects / MongoDB accepts, or the reverse);
* when Z3 proves the table equivalent, random rows must never be accepted by one database and rejected
  by the other.
"""

from __future__ import annotations

import random
import uuid
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from bson import Decimal128
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st
from pymongo.errors import PyMongoError

from schemashift.codegen.collections import build_collections
from schemashift.codegen.layout import build_layouts
from schemashift.codegen.values import materialize
from schemashift.equivalence.smt import run_smt
from schemashift.pipeline import CompileOptions, compile_sql

pytestmark = pytest.mark.integration

TYPES = {
    "INT": ("int", None),
    "SMALLINT": ("int", None),
    "BIGINT": ("int", None),
    "NUMERIC(6,2)": ("num", None),
    "NUMERIC(10,0)": ("num", None),
    "DOUBLE PRECISION": ("float", None),
    "VARCHAR(6)": ("str", 6),
    "TEXT": ("str", None),
    "BOOLEAN": ("bool", None),
}
NUMERIC = [t for t, (k, _) in TYPES.items() if k in ("int", "num", "float")]


@st.composite
def tables(draw: Any) -> dict[str, Any]:
    n = draw(st.integers(1, 4))
    cols: list[dict[str, Any]] = []
    for i in range(n):
        t = draw(st.sampled_from(list(TYPES)))
        cols.append({"name": f"c{i}", "type": t, "null": draw(st.booleans()), "kind": TYPES[t][0]})
    nums = [c["name"] for c in cols if c["type"] in NUMERIC]
    strs = [c["name"] for c in cols if c["kind"] == "str"]
    checks: list[str] = []
    for _ in range(draw(st.integers(0, 3))):
        k = draw(st.integers(-5, 40))
        options: list[str] = []
        if nums:
            c = draw(st.sampled_from(nums))
            options += [
                f"{c} > {k}",
                f"{c} <= {k}",
                f"{c} <> {k}",
                f"{c} BETWEEN {k} AND {k + 7}",
                f"{c} IN ({k}, {k + 3})",
                f"NOT ({c} < {k})",
                f"({c} > {k} OR {c} IS NULL)",
                f"{c} * 2 >= {k}",
                f"{c} IS NOT NULL",
            ]
            if len(nums) > 1:
                d = draw(st.sampled_from(nums))
                options += [f"{c} < {d}", f"{c} + {d} > {k}", f"({c} <= {d} OR {d} IS NULL)"]
        if strs:
            s = draw(st.sampled_from(strs))
            options += [
                f"LENGTH({s}) >= {abs(k) % 4}",
                f"({s} = 'ab' OR {s} <> 'cd')",
                f"{s} <> 'zz'",
            ]
        if options:
            checks.append(draw(st.sampled_from(options)))
    return {"cols": cols, "checks": checks}


def ddl(name: str, spec: dict[str, Any]) -> str:
    parts = ["id INT PRIMARY KEY"]
    for c in spec["cols"]:
        parts.append(f"{c['name']} {c['type']}{'' if c['null'] else ' NOT NULL'}")
    parts += [f"CHECK ({e})" for e in spec["checks"]]
    return f"CREATE TABLE {name} ({', '.join(parts)});"


def pg_value(kind: str, text: str) -> Any:
    if kind == "int":
        return int(text)
    if kind == "num":
        return Decimal(text)
    if kind == "float":
        return float(Decimal(text))
    if kind == "bool":
        return text in ("True", "true")
    return text


def mongo_value(kind: str, type_: str, text: str) -> Any:
    if kind == "int":
        return int(text)
    if kind == "num":
        return Decimal128(text)
    if kind == "float":
        return float(Decimal(text))
    if kind == "bool":
        return text in ("True", "true")
    return text


def try_pg(conn: Any, name: str, spec: dict[str, Any], row: dict[str, str | None]) -> bool:
    cols = ["id"] + [c["name"] for c in spec["cols"]]
    values: list[Any] = [int(row["id"] or 0)]
    for c in spec["cols"]:
        raw = row.get(c["name"])
        values.append(None if raw is None else pg_value(c["kind"], raw))
    sql = f"INSERT INTO {name} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})"
    try:
        with conn.transaction():
            conn.execute(sql, values)
            conn.execute(f"DELETE FROM {name}")
        return True
    except psycopg.Error:
        return False


def try_mongo(coll: Any, spec: dict[str, Any], row: dict[str, str | None]) -> bool:
    doc: dict[str, Any] = {"_id": int(row["id"] or 0)}
    for c in spec["cols"]:
        raw = row.get(c["name"])
        if raw is not None:
            doc[c["name"]] = mongo_value(c["kind"], c["type"], raw)
    try:
        coll.insert_one(doc)
        coll.delete_many({})
        return True
    except PyMongoError:
        return False


INTERESTING = {
    "int": ["0", "1", "-1", "5", "7", "40", "-2147483648", "2147483647", "32768", "2147483648"],
    "num": ["0", "1", "-1", "2.5", "9999.99", "9999.995", "100000", "-100000", "0.005", "7"],
    "float": ["0", "1", "-1.5", "3", "1000000", "-5"],
    "str": ["", "a", "ab", "cd", "zz", "abcdef", "abcdefg", "xyz"],
    "bool": ["true", "false"],
}


def random_row(spec: dict[str, Any], rng: random.Random) -> dict[str, str | None]:
    row: dict[str, str | None] = {"id": str(rng.randint(1, 3))}
    for c in spec["cols"]:
        row[c["name"]] = None if rng.random() < 0.25 else rng.choice(INTERESTING[c["kind"]])
    return row


STATS = {
    "timeouts": 0,
    "tables": 0,
    "unsupported": 0,
    "counterexamples_confirmed": 0,
    "proofs": 0,
    "rows_agreeing": 0,
}


@pytest.fixture
def stores(pg_schema: Any, mongo_client: Any) -> Any:
    conn, _ = pg_schema
    db = mongo_client[f"t_{uuid.uuid4().hex[:12]}"]
    yield conn, db
    mongo_client.drop_database(db.name)


@settings(
    max_examples=int(__import__("os").environ.get("SMT_EXAMPLES", "40")),
    deadline=None,
    suppress_health_check=list(HealthCheck),
    derandomize=True,
)
@given(spec=tables())
def test_z3_counterexamples_and_proofs_are_confirmed_by_the_real_databases(
    spec: dict[str, Any], stores: Any
) -> None:
    conn, db = stores
    name = f"t{uuid.uuid4().hex[:8]}"
    sql = ddl(name, spec)
    result = compile_sql(sql, "", "", CompileOptions(smt=False))
    assert not result.has_errors, (sql, [d.message for d in result.diagnostics])
    report = run_smt(result)
    (table,) = [t for t in report.tables if t.table == name]
    conn.execute(sql)
    layouts = build_layouts(result.graph, result.plan)
    (cspec,) = build_collections(result.schema_, layouts).collections
    db.create_collection(
        cspec.name,
        validator=materialize(cspec.validator),
        validationLevel=cspec.validation_level,
        validationAction=cspec.validation_action,
    )
    coll = db[cspec.name]
    STATS["tables"] += 1
    if table.status == "UNSUPPORTED":
        STATS["unsupported"] += 1
        assume(False)  # outside the documented fragment: nothing to confirm
    if table.status == "TIMEOUT":  # an honest "don't know": counted, and the rate is bounded below
        STATS["timeouts"] += 1
        assume(False)
    for f in table.findings:
        STATS["counterexamples_confirmed"] += 1
        pg_ok, mongo_ok = try_pg(conn, name, spec, f.row), try_mongo(coll, spec, f.row)
        if f.direction == "mongo_accepts_sql_rejects":
            assert (pg_ok, mongo_ok) == (False, True), (sql, f.row, f.explanation)
        else:
            assert (pg_ok, mongo_ok) == (True, False), (sql, f.row, f.explanation)
    if table.status == "EQUIVALENT":
        STATS["proofs"] += 1
        rng = random.Random(7)
        for _ in range(60):
            row = random_row(spec, rng)
            assert try_pg(conn, name, spec, row) == try_mongo(coll, spec, row), (sql, row)
            STATS["rows_agreeing"] += 1


def test_the_random_schemas_exercised_both_outcomes() -> None:
    """Runs after the property test (file order): it must have confirmed counterexamples and proofs."""
    print("smt soundness:", STATS)
    assert STATS["tables"] >= 20
    assert STATS["counterexamples_confirmed"] >= 3, STATS
    assert STATS["proofs"] >= 5 and STATS["rows_agreeing"] >= 300, STATS
    assert STATS["timeouts"] <= 0.1 * STATS["tables"], STATS  # UNVERIFIED must stay rare
