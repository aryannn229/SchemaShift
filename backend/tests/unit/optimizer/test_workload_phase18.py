"""Phase 18: frequency annotations, seed statistics, workload terms, explanations and sensitivity."""

from __future__ import annotations

from typer.testing import CliRunner

from schemashift.cli import app
from schemashift.optimizer.workload import WorkloadSpec
from schemashift.pipeline import CompileOptions, compile_sql

SCHEMA = (
    "CREATE TABLE p (id INT PRIMARY KEY, name TEXT);\n"
    "CREATE TABLE c (id INT PRIMARY KEY, p_id INT NOT NULL REFERENCES p(id), note TEXT);\n"
)
READ = "SELECT p.name, c.note FROM p JOIN c ON c.p_id = p.id WHERE p.id = 1;\n"
WRITE = "INSERT INTO c (id, p_id, note) VALUES (1, 1, 'x');\n"


def _plan(queries: str, **kw):  # type: ignore[no-untyped-def]
    return compile_sql(SCHEMA, queries, "", CompileOptions(**kw)).plan


def test_freq_annotation_changes_the_score() -> None:
    plain = _plan(READ + WRITE)
    hot_writes = _plan(READ + "-- @freq 50\n" + WRITE)
    rid = next(iter(plain.decisions))
    assert plain.decisions[rid].score != hot_writes.decisions[rid].score


def test_sidecar_spec_matches_annotation() -> None:
    annotated = _plan(READ + "-- @freq 50\n" + WRITE)
    sidecar = _plan(READ + WRITE, workload=WorkloadSpec(query_freq={"q2": 50}))
    rid = next(iter(annotated.decisions))
    assert annotated.decisions[rid].score == sidecar.decisions[rid].score


def test_explanation_and_fragility_are_recorded() -> None:
    plan = _plan(READ + WRITE)
    for d in plan.decisions.values():
        assert d.explanation
        assert d.fragility is not None
        assert d.fragility.level in {"fragile", "watch", "stable", "constrained"}


def test_sensitivity_is_reproducible() -> None:
    a = _plan(READ + WRITE)
    b = _plan(READ + WRITE)
    assert a.model_dump() == b.model_dump()


def test_sensitivity_can_be_disabled() -> None:
    plan = _plan(READ, sensitivity=False)
    assert all(d.fragility is None for d in plan.decisions.values())


def test_seed_fanout_changes_the_score() -> None:
    seed = "INSERT INTO p (id, name) VALUES (1,'a'),(2,'b');\n" + (
        "INSERT INTO c (id, p_id, note) VALUES "
        + ",".join(f"({i},1,'n')" for i in range(1, 40))
        + ";\n"
    )
    without = compile_sql(SCHEMA, READ).plan
    with_seed = compile_sql(SCHEMA, READ, seed).plan
    rid = next(iter(without.decisions))
    assert without.decisions[rid].score != with_seed.decisions[rid].score


def test_cli_sensitivity_and_workload(tmp_path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "s.sql").write_text(SCHEMA, encoding="utf-8")
    (tmp_path / "q.sql").write_text(READ + WRITE, encoding="utf-8")
    (tmp_path / "w.yaml").write_text("query_freq:\n  q2: 20\n", encoding="utf-8")
    r = CliRunner().invoke(
        app,
        [
            "sensitivity",
            str(tmp_path / "s.sql"),
            "-q",
            str(tmp_path / "q.sql"),
            "--workload",
            str(tmp_path / "w.yaml"),
        ],
    )
    assert r.exit_code == 0, r.output
    assert "c.p_id" in r.output or "->" in r.output or "stable" in r.output
    r2 = CliRunner().invoke(
        app,
        [
            "compile",
            str(tmp_path / "s.sql"),
            "-q",
            str(tmp_path / "q.sql"),
            "--workload",
            str(tmp_path / "w.yaml"),
        ],
    )
    assert r2.exit_code == 0, r2.output
