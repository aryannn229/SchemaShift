import pytest

from schemashift.ir import build_ir
from schemashift.models.query import Query
from schemashift.optimizer import AccessHint, extract_features
from schemashift.optimizer.features import estimate_doc_bytes, value_bytes
from schemashift.optimizer.workload import analyze_workload, flatten, seed_row_counts
from schemashift.parser import parse
from schemashift.semantic import analyze

SCHEMA = """
CREATE TABLE p (id INT PRIMARY KEY, name TEXT);
CREATE TABLE c (id INT PRIMARY KEY, p_id INT NOT NULL REFERENCES p(id) ON DELETE CASCADE, v TEXT);
CREATE TABLE q (id INT PRIMARY KEY);
CREATE TABLE multi (id INT PRIMARY KEY, p_id INT REFERENCES p(id), q_id INT REFERENCES q(id));
CREATE TABLE one (p_id INT PRIMARY KEY REFERENCES p(id));
CREATE TABLE tree (id INT PRIMARY KEY, up INT REFERENCES tree(id));
"""
EDGE = "fk:c.p_id->p.id"


def build(queries: str = "", seed: str = "", hints=None):  # type: ignore[no-untyped-def]
    parsed = parse(SCHEMA + queries)
    analysis = analyze(parsed.schema_, parsed.queries)
    program = build_ir(analysis.graph, parsed.queries)
    seed_q = parse(seed, "queries").queries if seed else ()
    return extract_features(analysis.graph, program, parsed.queries, seed_q, hints), analysis.graph


def test_defaults_without_queries() -> None:
    f, _ = build()
    x = f[EDGE]
    assert (x.child_independent_access, x.read_together_ratio, x.child_write_frequency) == (
        0.3,
        0.5,
        "med",
    )
    assert x.estimated_child_count_per_parent == 20
    assert set(x.sources.values()) == {"default"}


def test_one_to_one_default_count_is_one() -> None:
    f, _ = build()
    assert f["fk:one.p_id->p.id"].estimated_child_count_per_parent == 1
    assert f["fk:one.p_id->p.id"].cardinality == "1:1"


def test_self_reference_shape() -> None:
    f, _ = build()
    assert f["fk:tree.up->tree.id"].cardinality == "self"


def test_read_together_and_independent_from_queries() -> None:
    f, _ = build(
        "SELECT * FROM c; SELECT * FROM c; SELECT p.name, c.v FROM p JOIN c ON c.p_id = p.id; "
        "SELECT * FROM p;"
    )
    x = f[EDGE]
    assert x.read_together_ratio == pytest.approx(1 / 3)
    assert x.child_independent_access == pytest.approx(2 / 3)
    assert x.sources["read_together_ratio"] == "workload"


def test_child_never_queried_keeps_defaults() -> None:
    f, _ = build("SELECT * FROM p;")
    assert f[EDGE].read_together_ratio == 0.5 and f[EDGE].child_independent_access == 0.3


@pytest.mark.parametrize(
    ("writes", "freq"),
    [
        ("", "low"),
        ("INSERT INTO c (id, p_id) VALUES (1, 1);", "med"),
        ("UPDATE c SET v = 'a' WHERE id = 1; DELETE FROM c WHERE id = 2;", "med"),
        (
            "INSERT INTO c (id, p_id) VALUES (1, 1); UPDATE c SET v = 'a' WHERE id = 1; DELETE FROM c WHERE id = 2;",
            "high",
        ),
    ],
)
def test_write_frequency(writes: str, freq: str) -> None:
    f, _ = build("SELECT * FROM p; " + writes)
    assert f[EDGE].child_write_frequency == freq


def test_hints_override_everything() -> None:
    hint = AccessHint(
        child_independent_access=0.9,
        read_together_ratio=0.1,
        child_write_frequency="high",
        estimated_child_count_per_parent=777,
    )
    f, _ = build("SELECT p.name FROM p JOIN c ON c.p_id = p.id;", hints={EDGE: hint})
    x = f[EDGE]
    assert (x.child_independent_access, x.read_together_ratio, x.child_write_frequency) == (
        0.9,
        0.1,
        "high",
    )
    assert x.estimated_child_count_per_parent == 777
    assert set(x.sources.values()) == {"hint"}


def test_seed_data_estimates_fanout() -> None:
    seed = (
        "INSERT INTO p (id) VALUES (1), (2);"
        "INSERT INTO c (id, p_id) VALUES (1, 1), (2, 1), (3, 1), (4, 2), (5, 2), (6, 2);"
    )
    f, _ = build(seed=seed)
    assert f[EDGE].estimated_child_count_per_parent == 3
    assert f[EDGE].sources["estimated_child_count_per_parent"] == "seed"


def test_hint_beats_seed() -> None:
    seed = "INSERT INTO p (id) VALUES (1); INSERT INTO c (id, p_id) VALUES (1, 1), (2, 1);"
    f, _ = build(seed=seed, hints={EDGE: AccessHint(estimated_child_count_per_parent=9)})
    assert f[EDGE].estimated_child_count_per_parent == 9


def test_seed_rows_not_counted_as_workload_writes() -> None:
    f, _ = build("SELECT * FROM p;", seed="INSERT INTO c (id, p_id) VALUES (1, 1);")
    assert f[EDGE].child_write_frequency == "low"


def test_child_has_other_parents() -> None:
    f, _ = build()
    assert f["fk:multi.p_id->p.id"].child_has_other_parents is True
    assert f["fk:multi.q_id->q.id"].child_has_other_parents is True
    assert f[EDGE].child_has_other_parents is False
    assert f["fk:tree.up->tree.id"].child_has_other_parents is False


def test_guarantees_needing_embedding_counts_cascade_and_atomicity() -> None:
    f, _ = build()
    assert f[EDGE].guarantees_needing_embedding == 1
    f, _ = build(
        "BEGIN; UPDATE p SET name = 'x' WHERE id = 1; UPDATE c SET v = 'y' WHERE id = 1; COMMIT;"
    )
    assert f[EDGE].guarantees_needing_embedding == 2
    assert f["fk:multi.p_id->p.id"].guarantees_needing_embedding == 0


def test_set_null_counts_as_guarantee() -> None:
    parsed = parse(
        "CREATE TABLE p (id INT PRIMARY KEY); CREATE TABLE c (id INT PRIMARY KEY, p_id INT REFERENCES p(id) ON DELETE SET NULL);"
    )
    graph = analyze(parsed.schema_).graph
    f = extract_features(graph, build_ir(graph))
    assert f["fk:c.p_id->p.id"].guarantees_needing_embedding == 1


def test_doc_size_estimates() -> None:
    f, graph = build()
    assert f[EDGE].estimated_child_doc_size_bytes == estimate_doc_bytes(graph.schema.tables["c"])
    assert f[EDGE].estimated_child_doc_size_bytes > f[EDGE].parent_doc_size_bytes - 50


def test_value_bytes() -> None:
    from schemashift.models import NormalizedType

    assert value_bytes(NormalizedType(base="INTEGER")) == 4
    assert value_bytes(NormalizedType(base="VARCHAR", length=500)) == 200
    assert value_bytes(NormalizedType(base="VARCHAR", length=10)) == 10
    assert value_bytes(NormalizedType(base="INTEGER", is_array=True)) == 20


def test_junction_edge_flag() -> None:
    parsed = parse(
        "CREATE TABLE a (id INT PRIMARY KEY); CREATE TABLE b (id INT PRIMARY KEY);"
        "CREATE TABLE ab (a_id INT REFERENCES a(id), b_id INT REFERENCES b(id), PRIMARY KEY (a_id, b_id));"
    )
    graph = analyze(parsed.schema_).graph
    f = extract_features(graph, build_ir(graph))
    assert all(x.is_junction_edge for x in f.values())


# -------------------------------------------------------------------- workload
def test_flatten_and_seed_counts_ignore_non_inserts() -> None:
    parsed = parse(
        SCHEMA
        + "BEGIN; INSERT INTO c (id, p_id) VALUES (1, 1), (2, 1); DELETE FROM c WHERE id = 1; COMMIT;"
    )
    flat = flatten(parsed.queries)
    assert all(isinstance(q, Query) for q in flat) and len(flat) == 2
    assert seed_row_counts(parsed.queries) == {"c": 2}


def test_analyze_workload_without_queries() -> None:
    parsed = parse(SCHEMA)
    graph = analyze(parsed.schema_).graph
    stats = analyze_workload(graph, ())
    assert all(
        s.read_together_ratio is None and s.child_write_frequency is None for s in stats.values()
    )
