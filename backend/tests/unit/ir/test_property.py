"""Property tests: randomly generated valid schemas never crash the builder."""

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from schemashift.ir import build_ir, print_ir
from schemashift.ir.nodes import ReferentialIntegrity
from schemashift.parser import parse
from schemashift.semantic import analyze

TYPES = ["INT", "BIGINT", "TEXT", "VARCHAR(20)", "NUMERIC(8,2)", "BOOLEAN", "TIMESTAMPTZ", "UUID"]
ACTIONS = ["CASCADE", "SET NULL", "RESTRICT", "NO ACTION"]


@st.composite
def schemas(draw: st.DrawFn) -> str:
    n = draw(st.integers(min_value=1, max_value=6))
    stmts: list[str] = []
    for i in range(n):
        cols = ["id INT PRIMARY KEY"]
        for j in range(draw(st.integers(0, 3))):
            sql_type = draw(st.sampled_from(TYPES))
            extras = draw(st.sampled_from(["", " NOT NULL", " UNIQUE", " DEFAULT NULL"]))
            cols.append(f"c{j} {sql_type}{extras}")
        for j in range(draw(st.integers(0, 2))):
            if i == 0 and draw(st.booleans()):
                target = 0  # self reference
            elif i > 0:
                target = draw(st.integers(0, i))
            else:
                continue
            action = draw(st.sampled_from(ACTIONS))
            update = draw(st.sampled_from(["", " ON UPDATE CASCADE"]))
            cols.append(f"r{j} INT REFERENCES t{target}(id) ON DELETE {action}{update}")
        stmts.append(f"CREATE TABLE t{i} ({', '.join(cols)});")
    return "\n".join(stmts)


@settings(max_examples=150, suppress_health_check=[HealthCheck.too_slow], deadline=None)
@given(schemas())
def test_builder_never_crashes_and_fk_invariant(sql: str) -> None:
    parsed = parse(sql)
    assert not parsed.has_errors
    analysis = analyze(parsed.schema_)
    program = build_ir(analysis.graph)
    fk_count = sum(len(t.foreign_keys) for t in parsed.schema_.tables.values())
    ri = [n for n in program.nodes if isinstance(n, ReferentialIntegrity)]
    assert len(ri) == fk_count
    assert len({n.id for n in program.nodes}) == len(program.nodes)
    assert print_ir(program).startswith("== ENTITIES ==")
