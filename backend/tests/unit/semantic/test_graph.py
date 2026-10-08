from schemashift.parser import parse
from schemashift.semantic import analyze, build_graph, fk_id
from schemashift.semantic.cardinality import detect_junction, unique_column_sets


def graph(sql: str):  # type: ignore[no-untyped-def]
    return build_graph(parse(sql).schema_)


def test_edges_point_child_to_parent() -> None:
    g = graph("CREATE TABLE p (id INT PRIMARY KEY); CREATE TABLE c (pid INT REFERENCES p(id));")
    assert list(g.graph.edges()) == [("c", "p")]
    assert [r.id for r in g.parents_of("c")] == ["fk:c.pid->p.id"]
    assert [r.id for r in g.children_of("p")] == ["fk:c.pid->p.id"]


def test_fk_id_format() -> None:
    schema = parse(
        "CREATE TABLE p (id INT PRIMARY KEY); CREATE TABLE c (pid INT REFERENCES p(id));"
    ).schema_
    assert fk_id("c", schema.tables["c"].foreign_keys[0]) == "fk:c.pid->p.id"


def test_missing_parent_skipped() -> None:
    g = graph("CREATE TABLE c (pid INT REFERENCES ghost(id));")
    assert g.relationships == {}


def test_multigraph_parallel_edges() -> None:
    g = graph(
        "CREATE TABLE p (id INT PRIMARY KEY);"
        "CREATE TABLE c (a INT REFERENCES p(id), b INT REFERENCES p(id));"
    )
    assert g.graph.number_of_edges("c", "p") == 2


def test_one_to_many() -> None:
    g = graph(
        "CREATE TABLE p (id INT PRIMARY KEY); CREATE TABLE c (id INT PRIMARY KEY, pid INT REFERENCES p(id));"
    )
    assert next(iter(g.relationships.values())).cardinality == "1:N"


def test_one_to_one_via_pk() -> None:
    g = graph(
        "CREATE TABLE p (id INT PRIMARY KEY); CREATE TABLE c (pid INT PRIMARY KEY REFERENCES p(id));"
    )
    assert next(iter(g.relationships.values())).cardinality == "1:1"


def test_one_to_one_via_unique() -> None:
    g = graph(
        "CREATE TABLE p (id INT PRIMARY KEY); CREATE TABLE c (id INT PRIMARY KEY, pid INT UNIQUE REFERENCES p(id));"
    )
    assert next(iter(g.relationships.values())).cardinality == "1:1"


def test_one_to_one_via_unique_index() -> None:
    g = graph(
        "CREATE TABLE p (id INT PRIMARY KEY); CREATE TABLE c (id INT PRIMARY KEY, pid INT REFERENCES p(id));"
        "CREATE UNIQUE INDEX u ON c (pid);"
    )
    assert next(iter(g.relationships.values())).cardinality == "1:1"


def test_partial_unique_index_is_not_one_to_one() -> None:
    g = graph(
        "CREATE TABLE p (id INT PRIMARY KEY); CREATE TABLE c (id INT PRIMARY KEY, pid INT REFERENCES p(id));"
        "CREATE UNIQUE INDEX u ON c (pid) WHERE id > 0;"
    )
    assert next(iter(g.relationships.values())).cardinality == "1:N"


def test_composite_fk_part_of_pk_is_one_to_many() -> None:
    g = graph(
        "CREATE TABLE p (id INT PRIMARY KEY); CREATE TABLE c (pid INT REFERENCES p(id), n INT, PRIMARY KEY (pid, n));"
    )
    assert next(iter(g.relationships.values())).cardinality == "1:N"


def test_unique_column_sets() -> None:
    t = parse(
        "CREATE TABLE t (a INT PRIMARY KEY, b INT UNIQUE, c INT, d INT); CREATE UNIQUE INDEX i ON t (c, d);"
    ).schema_.tables["t"]
    assert set(unique_column_sets(t)) == {frozenset("a"), frozenset("b"), frozenset("cd")}


JUNCTION_SQL = """
CREATE TABLE students (id INT PRIMARY KEY);
CREATE TABLE courses (id INT PRIMARY KEY);
CREATE TABLE enrollments (
  student_id INT REFERENCES students(id),
  course_id INT REFERENCES courses(id),
  grade CHAR(1),
  PRIMARY KEY (student_id, course_id)
);
"""


def test_junction_detected() -> None:
    g = graph(JUNCTION_SQL)
    j = g.junctions["enrollments"]
    assert (j.left, j.right, j.payload_columns) == ("students", "courses", ("grade",))


def test_junction_with_unique_instead_of_pk() -> None:
    g = graph(
        "CREATE TABLE a (id INT PRIMARY KEY); CREATE TABLE b (id INT PRIMARY KEY);"
        "CREATE TABLE ab (id SERIAL PRIMARY KEY, a_id INT REFERENCES a(id), b_id INT REFERENCES b(id), UNIQUE (a_id, b_id));"
    )
    assert "ab" in g.junctions and g.junctions["ab"].payload_columns == ()


def test_junction_rejected_too_many_payload_columns() -> None:
    g = graph(
        "CREATE TABLE a (id INT PRIMARY KEY); CREATE TABLE b (id INT PRIMARY KEY);"
        "CREATE TABLE ab (a_id INT REFERENCES a(id), b_id INT REFERENCES b(id), x INT, y INT, z INT, PRIMARY KEY (a_id, b_id));"
    )
    assert g.junctions == {}


def test_junction_rejected_same_parent() -> None:
    g = graph(
        "CREATE TABLE a (id INT PRIMARY KEY);"
        "CREATE TABLE aa (x INT REFERENCES a(id), y INT REFERENCES a(id), PRIMARY KEY (x, y));"
    )
    assert g.junctions == {}


def test_junction_rejected_when_keys_do_not_cover_fks() -> None:
    g = graph(
        "CREATE TABLE a (id INT PRIMARY KEY); CREATE TABLE b (id INT PRIMARY KEY);"
        "CREATE TABLE ab (id INT PRIMARY KEY, a_id INT REFERENCES a(id), b_id INT REFERENCES b(id));"
    )
    assert g.junctions == {}


def test_junction_rejected_three_fks() -> None:
    g = graph(
        "CREATE TABLE a (id INT PRIMARY KEY); CREATE TABLE b (id INT PRIMARY KEY); CREATE TABLE c (id INT PRIMARY KEY);"
        "CREATE TABLE abc (a_id INT REFERENCES a(id), b_id INT REFERENCES b(id), c_id INT REFERENCES c(id), PRIMARY KEY (a_id, b_id, c_id));"
    )
    assert g.junctions == {}


def test_junction_rejected_self_parent() -> None:
    schema = parse(
        "CREATE TABLE a (id INT PRIMARY KEY);"
        "CREATE TABLE t (x INT REFERENCES a(id), y INT REFERENCES t(x), PRIMARY KEY (x, y));"
    ).schema_
    assert detect_junction(schema.tables["t"], schema) is None


def test_junction_ignores_dangling_fk() -> None:
    schema = parse(
        "CREATE TABLE a (id INT PRIMARY KEY);"
        "CREATE TABLE t (x INT REFERENCES a(id), y INT REFERENCES ghost(id), PRIMARY KEY (x, y));"
    ).schema_
    assert detect_junction(schema.tables["t"], schema) is None


def test_self_reference_relationship() -> None:
    g = graph("CREATE TABLE e (id INT PRIMARY KEY, m INT REFERENCES e(id));")
    assert [r.id for r in g.self_references] == ["fk:e.m->e.id"]
    assert g.relationship("fk:e.m->e.id").is_self_reference


def test_analyze_returns_graph_and_diagnostics() -> None:
    result = analyze(parse(JUNCTION_SQL).schema_)
    assert result.graph.junctions and not result.has_errors
