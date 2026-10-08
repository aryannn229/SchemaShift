"""Builder tests: every SQL construct -> expected node(s)."""

from typing import TypeVar

import pytest

from schemashift.ir import IRProgram, build_ir
from schemashift.ir.nodes import (
    AggregateSemantics,
    AttributeNode,
    AutoIncrement,
    CascadingDelete,
    CascadingUpdate,
    CrossEntityUniqueness,
    DefaultValue,
    DomainConstraint,
    EntityNode,
    EntityUniqueness,
    EnumDomain,
    IRNode,
    JoinSemantics,
    MultiEntityAtomicity,
    NotNullGuarantee,
    ReferentialIntegrity,
    RelationshipNode,
    RestrictDelete,
    SetDefaultOnDelete,
    SetNullOnDelete,
    TypeGuarantee,
    ValueUniqueness,
)
from schemashift.parser import parse
from schemashift.semantic import analyze

T = TypeVar("T", bound=IRNode)


def ir(sql: str) -> IRProgram:
    result = parse(sql)
    return build_ir(analyze(result.schema_, result.queries).graph, result.queries)


def of(program: IRProgram, cls: type[T]) -> list[T]:
    return [n for n in program.nodes if isinstance(n, cls)]


PC = "CREATE TABLE p (id INT PRIMARY KEY); "


def test_entity_and_attribute_nodes() -> None:
    p = ir("CREATE TABLE t (a INT, b TEXT);")
    assert [e.id for e in of(p, EntityNode)] == ["entity:t"]
    assert [a.id for a in of(p, AttributeNode)] == ["attr:t.a", "attr:t.b"]
    assert p.by_id("attr:t.a").origin == "column definition"


def test_every_node_has_id_origin_and_span() -> None:
    p = ir(
        PC
        + "CREATE TABLE c (id INT PRIMARY KEY, pid INT NOT NULL REFERENCES p(id) ON DELETE CASCADE);"
    )
    assert all(n.id and n.origin for n in p.nodes)
    assert all(n.source_span is not None for n in p.nodes)
    assert len({n.id for n in p.nodes}) == len(p.nodes)


def test_relationship_and_referential_integrity() -> None:
    p = ir(PC + "CREATE TABLE c (id INT PRIMARY KEY, pid INT REFERENCES p(id));")
    ri = of(p, ReferentialIntegrity)
    assert [r.id for r in ri] == ["fk:c.pid->p.id"]
    assert (ri[0].child, ri[0].parent, ri[0].columns) == ("c", "p", ("pid",))
    rel = of(p, RelationshipNode)[0]
    assert (rel.parent, rel.child, rel.cardinality, rel.fk_ref) == (
        "p",
        "c",
        "1:N",
        "fk:c.pid->p.id",
    )


def test_one_to_one_relationship_node() -> None:
    p = ir(PC + "CREATE TABLE c (pid INT PRIMARY KEY REFERENCES p(id));")
    assert of(p, RelationshipNode)[0].cardinality == "1:1"


def test_self_reference_flag() -> None:
    p = ir("CREATE TABLE e (id INT PRIMARY KEY, m INT REFERENCES e(id));")
    assert of(p, RelationshipNode)[0].self_reference is True


@pytest.mark.parametrize(
    ("action", "node_cls"),
    [
        ("CASCADE", CascadingDelete),
        ("SET NULL", SetNullOnDelete),
        ("SET DEFAULT", SetDefaultOnDelete),
        ("RESTRICT", RestrictDelete),
        ("NO ACTION", RestrictDelete),
    ],
)
def test_on_delete_actions(action: str, node_cls: type[IRNode]) -> None:
    p = ir(PC + f"CREATE TABLE c (pid INT REFERENCES p(id) ON DELETE {action});")
    delete_nodes = [
        n
        for n in p.nodes
        if type(n) in (CascadingDelete, SetNullOnDelete, SetDefaultOnDelete, RestrictDelete)
    ]
    assert [type(n) for n in delete_nodes] == [node_cls]


def test_default_on_delete_is_restrict_no_action() -> None:
    p = ir(PC + "CREATE TABLE c (pid INT REFERENCES p(id));")
    node = of(p, RestrictDelete)[0]
    assert node.action == "NO ACTION"


def test_restrict_keeps_action_name() -> None:
    p = ir(PC + "CREATE TABLE c (pid INT REFERENCES p(id) ON DELETE RESTRICT);")
    assert of(p, RestrictDelete)[0].action == "RESTRICT"


def test_on_update_cascade() -> None:
    p = ir(PC + "CREATE TABLE c (pid INT REFERENCES p(id) ON UPDATE CASCADE);")
    assert len(of(p, CascadingUpdate)) == 1
    assert of(p, CascadingUpdate)[0].parent_columns == ("id",)


def test_on_update_default_makes_no_node() -> None:
    p = ir(PC + "CREATE TABLE c (pid INT REFERENCES p(id) ON UPDATE RESTRICT);")
    assert of(p, CascadingUpdate) == []


def test_primary_key_entity_uniqueness_and_not_null() -> None:
    p = ir("CREATE TABLE t (a INT, b INT, PRIMARY KEY (a, b));")
    assert [(n.id, n.columns) for n in of(p, EntityUniqueness)] == [("pk:t.a,b", ("a", "b"))]
    assert {n.column for n in of(p, NotNullGuarantee)} == {"a", "b"}
    assert of(p, NotNullGuarantee)[0].origin == "PRIMARY KEY"


def test_not_null_origin() -> None:
    p = ir("CREATE TABLE t (a INT NOT NULL, b INT);")
    assert [(n.column, n.origin) for n in of(p, NotNullGuarantee)] == [("a", "NOT NULL")]


def test_unique_constraint_and_nullable_flag() -> None:
    p = ir("CREATE TABLE t (a INT PRIMARY KEY, email TEXT UNIQUE, ssn TEXT NOT NULL UNIQUE);")
    by_col = {n.columns: n for n in of(p, ValueUniqueness)}
    assert by_col[("email",)].nullable is True
    assert by_col[("ssn",)].nullable is False


def test_unique_index_and_partial_index() -> None:
    p = ir(
        "CREATE TABLE t (a INT PRIMARY KEY, b TEXT, d BOOL);"
        "CREATE UNIQUE INDEX i1 ON t (b); CREATE UNIQUE INDEX i2 ON t (b) WHERE d = false;"
        "CREATE INDEX i3 ON t (d);"
    )
    nodes = of(p, ValueUniqueness)
    assert len(nodes) == 2
    assert nodes[0].origin == "UNIQUE INDEX" and nodes[0].partial_where is None
    assert nodes[1].partial_where == "d = FALSE"
    assert nodes[0].id != nodes[1].id  # deduplicated ids


def test_composite_unique() -> None:
    p = ir("CREATE TABLE t (a INT, b INT, UNIQUE (a, b));")
    assert of(p, ValueUniqueness)[0].columns == ("a", "b")


def test_check_constraint() -> None:
    p = ir("CREATE TABLE t (lo INT, hi INT, CONSTRAINT rng CHECK (lo <= hi), CHECK (lo > 0));")
    nodes = of(p, DomainConstraint)
    assert [(n.name, n.columns) for n in nodes] == [("rng", ("lo", "hi")), (None, ("lo",))]
    assert nodes[0].id == "check:t.rng"


def test_type_guarantee_per_column() -> None:
    p = ir("CREATE TABLE t (a NUMERIC(10,2), b VARCHAR(5));")
    types = {n.column: n.type for n in of(p, TypeGuarantee)}
    assert types["a"].scale == 2 and types["b"].length == 5


def test_default_value() -> None:
    p = ir("CREATE TABLE t (a INT DEFAULT 5, b TIMESTAMPTZ DEFAULT now());")
    assert [(n.column, n.default_kind) for n in of(p, DefaultValue)] == [
        ("a", "literal"),
        ("b", "now"),
    ]


def test_auto_increment() -> None:
    p = ir("CREATE TABLE t (a SERIAL, b INT GENERATED ALWAYS AS IDENTITY, c INT);")
    assert [n.column for n in of(p, AutoIncrement)] == ["a", "b"]


def test_enum_domain() -> None:
    p = ir("CREATE TYPE m AS ENUM ('x', 'y'); CREATE TABLE t (a m);")
    node = of(p, EnumDomain)[0]
    assert (node.column, node.values, node.enum_name) == ("a", ("x", "y"), "m")


JUNCTION = (
    "CREATE TABLE a (id INT PRIMARY KEY); CREATE TABLE b (id INT PRIMARY KEY);"
    "CREATE TABLE ab (a_id INT REFERENCES a(id), b_id INT REFERENCES b(id), PRIMARY KEY (a_id, b_id));"
)


def test_junction_gives_many_to_many_and_cross_entity_uniqueness() -> None:
    p = ir(JUNCTION)
    m2n = [r for r in of(p, RelationshipNode) if r.cardinality == "M:N"]
    assert len(m2n) == 1 and m2n[0].junction == "ab" and (m2n[0].parent, m2n[0].child) == ("a", "b")
    cross = of(p, CrossEntityUniqueness)
    assert len(cross) == 1
    assert cross[0].tables == ("a", "b") and cross[0].columns == ("a_id", "b_id")
    assert len(of(p, ReferentialIntegrity)) == 2  # still one per FK


def test_no_cross_entity_uniqueness_without_junction() -> None:
    assert of(ir(PC + "CREATE TABLE c (pid INT REFERENCES p(id));"), CrossEntityUniqueness) == []


SCHEMA = (
    "CREATE TABLE customers (id INT PRIMARY KEY, name TEXT);"
    "CREATE TABLE orders (id INT PRIMARY KEY, customer_id INT REFERENCES customers(id), total INT);"
    "CREATE TABLE accounts (id INT PRIMARY KEY);"
)


def test_multi_entity_atomicity() -> None:
    p = ir(
        SCHEMA
        + "BEGIN; UPDATE customers SET name = 'x' WHERE id = 1; DELETE FROM orders WHERE id = 1; COMMIT;"
    )
    node = of(p, MultiEntityAtomicity)[0]
    assert node.tables == ("customers", "orders") and node.txn_id == "t1" and node.id == "txn:t1"


def test_single_table_transaction_has_no_atomicity_node() -> None:
    p = ir(
        SCHEMA
        + "BEGIN; UPDATE customers SET name = 'x' WHERE id = 1; UPDATE customers SET name = 'y' WHERE id = 2; COMMIT;"
    )
    assert of(p, MultiEntityAtomicity) == []


def test_inner_join_semantics_with_fk_match() -> None:
    p = ir(
        SCHEMA + "SELECT c.name, o.total FROM customers c JOIN orders o ON o.customer_id = c.id;"
    )
    j = of(p, JoinSemantics)[0]
    assert (j.kind, j.tables, j.fk_ref) == (
        "INNER",
        ("customers", "orders"),
        "fk:orders.customer_id->customers.id",
    )
    assert j.right_columns_projected is True
    assert j.query_id == "q1"


def test_left_join_semantics() -> None:
    p = ir(SCHEMA + "SELECT c.name FROM customers c LEFT JOIN orders o ON o.customer_id = c.id;")
    j = of(p, JoinSemantics)[0]
    assert j.kind == "LEFT" and j.right_columns_projected is False


def test_join_not_matching_fk_has_no_fk_ref() -> None:
    p = ir(SCHEMA + "SELECT * FROM customers c JOIN orders o ON o.total = c.id;")
    assert of(p, JoinSemantics)[0].fk_ref is None


def test_select_star_counts_as_projecting_right_side() -> None:
    p = ir(SCHEMA + "SELECT * FROM customers c LEFT JOIN orders o ON o.customer_id = c.id;")
    assert of(p, JoinSemantics)[0].right_columns_projected is True


def test_multiple_joins_get_indexed_ids() -> None:
    p = ir(
        SCHEMA
        + "CREATE TABLE items (id INT PRIMARY KEY, order_id INT REFERENCES orders(id));"
        + "SELECT * FROM customers c JOIN orders o ON o.customer_id = c.id JOIN items i ON i.order_id = o.id;"
    )
    assert [j.id for j in of(p, JoinSemantics)] == ["join:q1#0", "join:q1#1"]
    assert of(p, JoinSemantics)[1].tables == ("orders", "items")


def test_aggregate_semantics() -> None:
    p = ir(
        SCHEMA + "SELECT c.name, COUNT(*), SUM(o.total), AVG(o.total) FROM customers c "
        "JOIN orders o ON o.customer_id = c.id GROUP BY c.name HAVING COUNT(*) > 1;"
    )
    agg = of(p, AggregateSemantics)[0]
    assert agg.group_by == ("customers.name",)
    assert [(a.func, a.argument) for a in agg.aggregates] == [
        ("COUNT", "*"),
        ("SUM", "orders.total"),
        ("AVG", "orders.total"),
        ("COUNT", "*"),
    ]
    assert agg.has_having is True and agg.tables == ("customers", "orders")


def test_count_distinct() -> None:
    p = ir(SCHEMA + "SELECT COUNT(DISTINCT name) FROM customers;")
    call = of(p, AggregateSemantics)[0].aggregates[0]
    assert call.distinct is True and call.argument == "customers.name"


def test_group_by_without_aggregate() -> None:
    p = ir(SCHEMA + "SELECT name FROM customers GROUP BY name;")
    assert of(p, AggregateSemantics)[0].aggregates == ()


def test_plain_select_has_no_query_nodes() -> None:
    p = ir(SCHEMA + "SELECT * FROM customers WHERE id = 1;")
    assert of(p, JoinSemantics) == [] and of(p, AggregateSemantics) == []


def test_queries_inside_transactions_are_lowered() -> None:
    p = ir(
        SCHEMA + "BEGIN; SELECT COUNT(*) FROM orders; DELETE FROM customers WHERE id = 1; COMMIT;"
    )
    assert len(of(p, AggregateSemantics)) == 1


def test_dangling_fk_makes_no_relationship() -> None:
    p = ir("CREATE TABLE c (pid INT REFERENCES ghost(id));")
    assert of(p, ReferentialIntegrity) == []


def test_entities_and_guarantees_split() -> None:
    p = ir(PC)
    assert {type(n) for n in p.entities} == {EntityNode, AttributeNode}
    assert all(not isinstance(n, (EntityNode, AttributeNode)) for n in p.guarantees)


def test_by_id_missing() -> None:
    with pytest.raises(KeyError):
        ir(PC).by_id("nope")


def test_program_roundtrips_through_json() -> None:
    p = ir(
        PC
        + "CREATE TABLE c (pid INT REFERENCES p(id) ON DELETE CASCADE);"
        + "SELECT COUNT(*) FROM c;"
    )
    again = IRProgram.model_validate_json(p.model_dump_json())
    assert again == p
