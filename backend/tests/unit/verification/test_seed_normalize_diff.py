import datetime as dt
import uuid
from decimal import Decimal

import pytest
import sqlglot
from bson import Binary, Decimal128, Int64, ObjectId
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from schemashift.parser import parse
from schemashift.verification.diff import compare
from schemashift.verification.normalize import (
    duration_text,
    normalize_row,
    normalize_value,
    rows_equal,
    sort_key,
    values_equal,
)
from schemashift.verification.seed import (
    MAX_ROWS_PER_TABLE,
    SeedError,
    derive_hints,
    generate_seed,
    insertion_order,
    seed_from_inserts,
)
from schemashift.verification.sqleval import passes_check
from tests.unit.codegen.helpers import SAMPLE_NAMES, sample

SHOP = """
CREATE TYPE tier AS ENUM ('gold', 'silver');
CREATE TABLE customers (id SERIAL PRIMARY KEY, email TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
  tier tier, age INT CHECK (age BETWEEN 18 AND 99), score NUMERIC(5,2) CHECK (score >= 0 AND score <= 10),
  status TEXT NOT NULL CHECK (status IN ('new', 'old')), joined TIMESTAMPTZ DEFAULT now(), tags TEXT[]);
CREATE TABLE orders (id INT PRIMARY KEY, customer_id INT NOT NULL REFERENCES customers(id), total NUMERIC(8,2) NOT NULL,
  note VARCHAR(8), flag BOOLEAN, j JSONB, d DATE, u UUID, raw BYTEA);
CREATE TABLE profiles (customer_id INT PRIMARY KEY REFERENCES customers(id), bio TEXT);
CREATE TABLE tags (id INT PRIMARY KEY, label VARCHAR(10) NOT NULL UNIQUE);
CREATE TABLE ctags (customer_id INT REFERENCES customers(id), tag_id INT REFERENCES tags(id), PRIMARY KEY (customer_id, tag_id));
CREATE TABLE emp (id INT PRIMARY KEY, boss INT REFERENCES emp(id));
CREATE TABLE emp2 (id INT PRIMARY KEY, boss INT NOT NULL REFERENCES emp2(id));
"""


def seed_for(sql: str, **kw):  # type: ignore[no-untyped-def]
    return parse(sql).schema_, generate_seed(parse(sql).schema_, **kw)


# ----------------------------------------------------------------------- ordering
def test_insertion_order_puts_parents_first() -> None:
    schema = parse(SHOP).schema_
    order, deferred = insertion_order(schema)
    assert order.index("customers") < order.index("orders") < len(order)
    assert order.index("customers") < order.index("ctags") and order.index("tags") < order.index(
        "ctags"
    )
    assert deferred == []


def test_cycle_is_broken_at_a_nullable_foreign_key() -> None:
    sql = "CREATE TABLE a (id INT PRIMARY KEY, b INT); CREATE TABLE b (id INT PRIMARY KEY, a INT NOT NULL REFERENCES a(id)); ALTER TABLE a ADD FOREIGN KEY (b) REFERENCES b(id);"
    order, deferred = insertion_order(parse(sql).schema_)
    assert order == ["a", "b"] and [(t, fk.columns) for t, fk in deferred] == [("a", ("b",))]


def test_cycle_of_not_null_keys_cannot_be_seeded() -> None:
    sql = "CREATE TABLE a (id INT PRIMARY KEY, b INT NOT NULL); CREATE TABLE b (id INT PRIMARY KEY, a INT NOT NULL REFERENCES a(id)); ALTER TABLE a ADD FOREIGN KEY (b) REFERENCES b(id);"
    with pytest.raises(SeedError):
        generate_seed(parse(sql).schema_)


# -------------------------------------------------------------------- invariants
def check_invariants(schema, seed) -> None:  # type: ignore[no-untyped-def]
    for table in schema.tables.values():
        rows = seed.rows[table.name]
        omitted = seed.omitted[table.name]
        assert len(rows) == len(omitted) and rows
        keys = [u.columns for u in table.uniques] + (
            [table.primary_key.columns] if table.primary_key else []
        )
        for k in keys:
            vals = [tuple(r[c] for c in k) for r in rows if all(r.get(c) is not None for c in k)]
            assert len(vals) == len(set(vals)), f"{table.name}{k} not unique"
        checks = [sqlglot.parse_one(c.expression_sql, dialect="postgres") for c in table.checks]
        for row, skip in zip(rows, omitted, strict=True):
            for col in table.columns:
                if col.name in skip:
                    continue
                if not col.nullable:
                    assert row.get(col.name) is not None, f"{table.name}.{col.name} NULL"
            explicit = {k: v for k, v in row.items() if k not in skip}
            assert all(passes_check(c, explicit) for c in checks)
        for fk in table.foreign_keys:
            parents = {tuple(r[c] for c in fk.ref_columns) for r in seed.rows[fk.ref_table]}
            for row in rows:
                vals = tuple(row.get(c) for c in fk.columns)
                if any(v is None for v in vals):
                    continue
                assert vals in parents, f"{table.name}.{fk.columns}={vals} has no parent"


def test_generated_data_satisfies_constraints() -> None:
    schema, seed = seed_for(SHOP, seed=3, rows_per_table=40)
    check_invariants(schema, seed)
    assert len(seed.rows["customers"]) == 40
    assert len(seed.rows["profiles"]) == 40  # one per customer (unique foreign key)
    assert {r["status"] for r in seed.rows["customers"]} <= {"new", "old"}
    assert all(18 <= r["age"] <= 99 for r in seed.rows["customers"] if r["age"] is not None)
    assert all(
        Decimal(0) <= r["score"] <= Decimal(10)
        for r in seed.rows["customers"]
        if r["score"] is not None
    )


def test_self_references_and_cycles() -> None:
    schema, seed = seed_for(SHOP, seed=1, rows_per_table=10)
    emp = seed.rows["emp"]
    assert emp[0]["boss"] is None or emp[0]["boss"] in {r["id"] for r in emp}
    emp2 = seed.rows["emp2"]
    assert (
        emp2[0]["boss"] == emp2[0]["id"]
    )  # NOT NULL self reference: the first row points at itself


def test_junction_pairs_are_unique() -> None:
    _, seed = seed_for(SHOP, seed=2, rows_per_table=30)
    pairs = [(r["customer_id"], r["tag_id"]) for r in seed.rows["ctags"]]
    assert len(pairs) == len(set(pairs)) and pairs


def test_same_seed_same_data_different_seed_different_data() -> None:
    a = generate_seed(parse(SHOP).schema_, seed=5, rows_per_table=15)
    b = generate_seed(parse(SHOP).schema_, seed=5, rows_per_table=15)
    c = generate_seed(parse(SHOP).schema_, seed=6, rows_per_table=15)
    assert a.rows == b.rows and a.omitted == b.omitted
    assert a.rows != c.rows


def test_row_count_is_clamped() -> None:
    schema = parse("CREATE TABLE t (id INT PRIMARY KEY, v INT);").schema_
    assert len(generate_seed(schema, rows_per_table=10_000).rows["t"]) == MAX_ROWS_PER_TABLE
    assert len(generate_seed(schema, rows_per_table=0).rows["t"]) == 1


def test_defaults_are_sometimes_left_to_the_database() -> None:
    schema = parse(
        "CREATE TABLE t (id INT PRIMARY KEY, c TEXT NOT NULL DEFAULT 'x', e INT DEFAULT 5);"
    ).schema_
    seed = generate_seed(schema, seed=0, rows_per_table=100)
    flat = [c for row in seed.omitted["t"] for c in row]
    assert "c" in flat and "e" in flat and len(flat) < 200


def test_null_rate_for_nullable_columns() -> None:
    schema = parse("CREATE TABLE t (id INT PRIMARY KEY, v TEXT);").schema_
    values = [r["v"] for r in generate_seed(schema, rows_per_table=200).rows["t"]]
    assert 10 < sum(v is None for v in values) < 80


def test_all_types_are_generated() -> None:
    sql = (
        "CREATE TABLE t (id INT PRIMARY KEY, a SMALLINT, b BIGINT, c NUMERIC(6,3), d REAL, e DOUBLE PRECISION, f BOOLEAN,"
        " g CHAR(4), h VARCHAR(5), i DATE, j TIMESTAMP, k TIMESTAMPTZ, l TIME, m INTERVAL, n UUID, o JSON, p BYTEA, q INT[]);"
    )
    seed = generate_seed(parse(sql).schema_, seed=4, rows_per_table=30)

    def sample_of(col: str):  # type: ignore[no-untyped-def]
        return next(r[col] for r in seed.rows["t"] if r[col] is not None)

    assert isinstance(sample_of("c"), Decimal) and isinstance(sample_of("f"), bool)
    assert len(sample_of("g")) == 4 and len(sample_of("h")) <= 5
    assert isinstance(sample_of("i"), dt.date) and isinstance(sample_of("k"), dt.datetime)
    assert isinstance(sample_of("l"), dt.time) and isinstance(sample_of("m"), dt.timedelta)
    assert isinstance(sample_of("n"), uuid.UUID) and isinstance(sample_of("q"), list)
    assert isinstance(sample_of("o"), dict) and isinstance(sample_of("p"), bytes)
    assert all(abs(r["a"]) <= 30_000 for r in seed.rows["t"] if r["a"] is not None)


def test_hints_from_checks() -> None:
    table = parse(SHOP).schema_.tables["customers"]
    hints = derive_hints(table)
    assert hints["age"] == {"min": 18, "max": 99} and hints["status"]["enum"] == ["new", "old"]
    assert hints["score"]["min"] == 0 and hints["score"]["max"] == 10


@pytest.mark.parametrize("name", SAMPLE_NAMES)
def test_sample_schemas_can_be_seeded(name: str) -> None:
    schema_sql, _, _ = sample(name)
    schema = parse(schema_sql).schema_
    seed = generate_seed(schema, seed=11, rows_per_table=25)
    check_invariants(schema, seed)


# ----------------------------------------------------------- property based
COLS = ["INT", "TEXT", "VARCHAR(12)", "NUMERIC(8,2)", "BOOLEAN", "DATE", "UUID"]


@st.composite
def schemas(draw: st.DrawFn) -> str:
    n = draw(st.integers(1, 4))
    out = []
    for i in range(n):
        cols = ["id INT PRIMARY KEY"]
        for j in range(draw(st.integers(0, 3))):
            extra = draw(st.sampled_from(["", " NOT NULL", " UNIQUE", " NOT NULL UNIQUE"]))
            cols.append(f"c{j} {draw(st.sampled_from(COLS))}{extra}")
        if i > 0 and draw(st.booleans()):
            nullable = draw(st.sampled_from(["", " NOT NULL"]))
            cols.append(f"r INT{nullable} REFERENCES t{draw(st.integers(0, i - 1))}(id)")
        if draw(st.booleans()):
            cols.append("chk INT CHECK (chk BETWEEN 5 AND 15)")
        out.append(f"CREATE TABLE t{i} ({', '.join(cols)});")
    return "\n".join(out)


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(schemas(), st.integers(0, 10_000), st.integers(1, 30))
def test_property_generated_seed_always_satisfies_constraints(
    sql: str, seed: int, rows: int
) -> None:
    schema = parse(sql).schema_
    check_invariants(schema, generate_seed(schema, seed=seed, rows_per_table=rows))


# ------------------------------------------------------------ user supplied seed
def test_seed_from_inserts_uses_the_user_values() -> None:
    schema = parse(SHOP).schema_
    inserts = parse(
        "INSERT INTO tags (id, label) VALUES (1, 'a'), (2, 'b'); INSERT INTO emp (id) VALUES (7);"
        "INSERT INTO orders (id, customer_id, total, d) VALUES (1, 1, 9.5, '2024-02-03');",
        "queries",
    ).queries
    seed = seed_from_inserts(schema, inserts, seed=9)
    assert seed.rows["tags"] == [{"id": 1, "label": "a"}, {"id": 2, "label": "b"}]
    assert seed.rows["emp"] == [{"id": 7}] and seed.rows["orders"][0]["total"] == Decimal("9.5")
    assert seed.rows["orders"][0]["d"] == dt.date(2024, 2, 3)
    assert "customers" not in seed.rows and seed.seed == 9 and seed.count() == 4


def test_seed_from_inserts_ignores_other_statements() -> None:
    schema = parse(SHOP).schema_
    queries = parse(
        "SELECT * FROM tags; UPDATE tags SET label = 'x' WHERE id = 1;", "queries"
    ).queries
    seed = seed_from_inserts(schema, queries)
    assert seed.rows == {} and len(seed.warnings) == 2


def test_seed_from_inserts_unknown_column() -> None:
    schema = parse("CREATE TABLE t (a INT);").schema_
    with pytest.raises(SeedError):
        seed_from_inserts(schema, parse("INSERT INTO t (zz) VALUES (1);", "queries").queries)


# ---------------------------------------------------------------- normalization
def test_normalize_numbers() -> None:
    assert normalize_value(1) == Decimal(1) and normalize_value(Int64(5)) == Decimal(5)
    assert normalize_value(1.5) == Decimal("1.5") and normalize_value(
        Decimal128("2.50")
    ) == Decimal("2.50")
    assert normalize_value(float("nan")) != normalize_value(float("nan"))
    assert normalize_value(True) is True


def test_normalize_dates_and_times() -> None:
    aware = dt.datetime(2024, 1, 1, 12, 0, 0, 999_999, tzinfo=dt.timezone(dt.timedelta(hours=2)))
    assert normalize_value(aware) == dt.datetime(2024, 1, 1, 10, 0, 0, 999_000, tzinfo=dt.UTC)
    assert normalize_value(dt.datetime(2024, 1, 1)) == dt.datetime(2024, 1, 1, tzinfo=dt.UTC)
    assert normalize_value(dt.date(2024, 3, 4)) == dt.datetime(2024, 3, 4, tzinfo=dt.UTC)
    assert normalize_value(dt.time(1, 2, 3)) == "01:02:03"
    assert (
        normalize_value(dt.timedelta(hours=2)) == duration_text(dt.timedelta(hours=2)) == "PT7200S"
    )


def test_normalize_ids_and_containers() -> None:
    u = uuid.UUID(int=5)
    assert normalize_value(Binary.from_uuid(u)) == u and normalize_value(u) == u
    assert normalize_value(Binary(b"ab")) == b"ab" and normalize_value(b"ab") == b"ab"
    oid = ObjectId()
    assert normalize_value(oid) == str(oid)
    assert normalize_value({"a": [1, Decimal128("2")]}) == {"a": [Decimal(1), Decimal(2)]}
    assert normalize_row((1, "x", None)) == (Decimal(1), "x", None)


def test_values_equal_uses_a_relative_tolerance() -> None:
    assert values_equal(Decimal("1.0000000001"), Decimal("1")) and not values_equal(
        Decimal("1.001"), Decimal("1")
    )
    assert values_equal(1e12, 1e12 + 100)
    assert values_equal([1, {"a": Decimal(1)}], [1, {"a": Decimal("1.0")}]) and not values_equal(
        [1], [1, 2]
    )
    assert (
        not values_equal({"a": 1}, {"b": 1})
        and values_equal("x", "x")
        and not values_equal("x", None)
    )
    assert rows_equal((1, "a"), (1, "a")) and not rows_equal((1,), (1, 2))
    assert sort_key((Decimal("1.0000001"), "a")) == sort_key((Decimal("1.0000002"), "a"))


# --------------------------------------------------------------------------- diff
def test_multiset_comparison_ignores_order() -> None:
    d = compare([(1, "a"), (2, "b")], [(2, "b"), (1, "a")], ordered=False)
    assert d.status == "MATCH" and d.pg_rows == d.mongo_rows == 2


def test_numeric_tolerance_and_type_normalisation() -> None:
    d = compare([(Decimal("2.50"), 3)], [(Decimal128("2.5"), Int64(3))], ordered=False)
    assert d.status == "MATCH"
    assert compare([(0.1 + 0.2,)], [(0.3,)], ordered=False).status == "MATCH"


def test_missing_and_extra_rows_are_reported() -> None:
    d = compare([(1,), (2,), (3,)], [(1,), (4,)], ordered=False)
    assert d.status == "MISMATCH"
    assert [r.postgres for r in d.differing if r.side == "postgres"] == [[Decimal(2)], [Decimal(3)]]
    assert [r.mongo for r in d.differing if r.side == "mongo"] == [[Decimal(4)]]
    assert d.hypothesis and "row counts" in d.hypothesis


def test_only_five_differences_are_listed() -> None:
    d = compare([(i,) for i in range(20)], [], ordered=False)
    assert len(d.differing) == 5


def test_ordered_comparison_detects_order_differences() -> None:
    d = compare([(1,), (2,)], [(2,), (1,)], ordered=True)
    assert d.status == "MISMATCH" and d.differing[0].index == 0 and "order" in (d.hypothesis or "")


def test_ties_in_the_sort_key_may_permute() -> None:
    pg = [("a", 1), ("b", 1), ("c", 2)]
    mongo = [("b", 1), ("a", 1), ("c", 2)]
    assert compare(pg, mongo, ordered=True, order_positions=[1]).status == "MATCH"
    assert compare(pg, mongo, ordered=True).status == "MISMATCH"  # unknown keys: strict
    assert (
        compare(pg, [("c", 2), ("a", 1), ("b", 1)], ordered=True, order_positions=[1]).status
        == "MISMATCH"
    )
    assert (
        compare(pg, [("a", 1), ("b", 1), ("c", 3)], ordered=True, order_positions=[1]).status
        == "MISMATCH"
    )


def test_group_count_mismatch_in_ordered_ties() -> None:
    assert (
        compare([(1,), (2,)], [(1,), (1,)], ordered=True, order_positions=[0]).status == "MISMATCH"
    )


def test_hypotheses() -> None:
    d = compare([(None,)], [(0,)], ordered=False)
    assert "SUM over an empty set" in (d.hypothesis or "")
    d = compare([(0,)], [(None,)], ordered=False)
    assert "COUNT/SUM of no rows" in (d.hypothesis or "")
    d = compare([(Decimal("2.5"),)], [(Decimal("2.6"),)], ordered=False)
    assert "numeric precision" in (d.hypothesis or "")
    d = compare([(None,)], [], ordered=False, global_aggregate=True)
    assert "global aggregate" in (d.hypothesis or "")
    d = compare([("a",)], [("b",)], ordered=False)
    assert d.status == "MISMATCH" and d.hypothesis is None
