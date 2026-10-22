"""Concurrency rules: every outcome of every EQ-CONC rule is reached, and the helper analysis is honest."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import pytest

import schemashift.equivalence.rules  # noqa: F401
from schemashift.concurrency.helpers import (
    ATOMIC_GUARD,
    analyze_source,
    runtime_source,
    shipped_profile,
)
from schemashift.equivalence import RuleOptions, check_concurrency
from schemashift.equivalence.registry import all_rules
from schemashift.models import Placement, PlacementPlan, Status
from tests.unit.equivalence.helpers import compile_ir

P = "CREATE TABLE p (id INT PRIMARY KEY, code TEXT UNIQUE NOT NULL);"
C = "CREATE TABLE c (id INT PRIMARY KEY, pid INT NOT NULL REFERENCES p(id), sku TEXT NOT NULL UNIQUE);"
SCHEMA = P + C
FK = "fk:c.pid->p.id"
EMB: dict[str, Placement] = {FK: "EMBED"}
SERIAL = "CREATE TABLE s (id SERIAL PRIMARY KEY);"
TXN = SCHEMA + "BEGIN; UPDATE p SET code = 'x' WHERE id = 1; DELETE FROM c WHERE id = 1; COMMIT;"
TXN_SINGLE = "BEGIN; UPDATE p SET code = 'x' WHERE id = 1; INSERT INTO c (id, pid, sku) VALUES (1, 1, 'a'); COMMIT;"

# The shipped helpers' source with one safeguard removed: what an unguarded implementation would look like.
SHIPPED = runtime_source()
NO_LOCKS = analyze_source(SHIPPED.replace("lock_row(", "_noop("))
NO_GUARD = analyze_source(SHIPPED.replace(ATOMIC_GUARD, "$other"))
RACY_COUNTER = analyze_source(
    SHIPPED.replace(
        'doc = db["_counters"].find_one_and_update(',
        'cur = db["_counters"].find_one({"_id": key})\n    doc = db["_counters"].find_one_and_update(',
    )
)


@dataclass(frozen=True)
class Case:
    rule_id: str
    outcome: str
    status: Status
    sql: str
    node_id: str
    plan: dict[str, Placement] = field(default_factory=dict)
    options: RuleOptions | None = None
    helpers: Any = None


NO_TXN = RuleOptions(transactions_available=False)
COUNTERS = RuleOptions(preserve_integer_ids=True)

CASES = [
    # --- EQ-CONC-FK
    Case("EQ-CONC-FK", "EMBEDDED", "SAFE", SCHEMA, f"conc:fk_race:{FK}", EMB),
    Case("EQ-CONC-FK", "LOCKED", "CHANGED", SCHEMA, f"conc:fk_race:{FK}"),
    Case("EQ-CONC-FK", "UNLOCKED", "BROKEN", SCHEMA, f"conc:fk_race:{FK}", helpers=NO_LOCKS),
    Case("EQ-CONC-FK", "NO_TRANSACTION", "BROKEN", SCHEMA, f"conc:fk_race:{FK}", options=NO_TXN),
    # --- EQ-CONC-DUP
    Case("EQ-CONC-DUP", "INDEX", "SAFE", SCHEMA, "conc:dup:c.sku"),
    Case("EQ-CONC-DUP", "ARRAY_ATOMIC", "SAFE", SCHEMA, "conc:dup:c.sku", EMB),
    Case("EQ-CONC-DUP", "ARRAY_TXN", "CHANGED", SCHEMA, "conc:dup:c.sku", EMB, helpers=NO_GUARD),
    Case(
        "EQ-CONC-DUP",
        "ARRAY_RACE",
        "BROKEN",
        SCHEMA,
        "conc:dup:c.sku",
        EMB,
        NO_TXN,
        helpers=NO_GUARD,
    ),
    # --- EQ-CONC-ARRAY
    Case("EQ-CONC-ARRAY", "ATOMIC", "SAFE", SCHEMA, "conc:array:c", EMB),
    # --- EQ-CONC-TXN
    Case("EQ-CONC-TXN", "SINGLE_DOCUMENT", "SAFE", SCHEMA + TXN_SINGLE, "conc:txn:t1", EMB),
    Case("EQ-CONC-TXN", "RETRIED", "CHANGED", TXN, "conc:txn:t1"),
    Case("EQ-CONC-TXN", "UNPROTECTED", "BROKEN", TXN, "conc:txn:t1", options=NO_TXN),
    # --- EQ-CONC-COUNTER
    Case("EQ-CONC-COUNTER", "OBJECTID", "SAFE", SERIAL, "conc:counter:s.id"),
    Case("EQ-CONC-COUNTER", "COUNTER", "CHANGED", SERIAL, "conc:counter:s.id", options=COUNTERS),
    Case(
        "EQ-CONC-COUNTER",
        "COUNTER_RACE",
        "BROKEN",
        SERIAL,
        "conc:counter:s.id",
        options=COUNTERS,
        helpers=RACY_COUNTER,
    ),
]


def run(case: Case):  # type: ignore[no-untyped-def]
    graph, program = compile_ir(case.sql)
    report = check_concurrency(
        program, graph, PlacementPlan.of(case.plan), case.options, case.helpers
    )
    return report, {v.node_id: v for v in report.verdicts}


@pytest.mark.parametrize(
    "case", CASES, ids=[f"{c.rule_id}-{c.outcome}-{i}" for i, c in enumerate(CASES)]
)
def test_branch(case: Case) -> None:
    _, verdicts = run(case)
    v = verdicts[case.node_id]
    assert (v.rule_id, v.outcome, v.status) == (case.rule_id, case.outcome, case.status)
    assert v.conditions_evaluated, "every verdict records the conditions it checked"
    assert v.reason and not re.search(r"\{[a-z_.]+\}", v.reason)
    assert v.decided_by.startswith(f"{case.rule_id}/{case.outcome}")
    assert v.evidence in ("PROVEN", "CONDITIONAL")  # never UNVERIFIED without a probe


def test_every_concurrency_outcome_is_covered() -> None:
    declared = {
        (s.rule_id, o.key)
        for s in all_rules()
        if s.rule_id.startswith("EQ-CONC-")
        for o in s.outcomes
    }
    assert declared == {(c.rule_id, c.outcome) for c in CASES}


def test_report_counts_and_overall() -> None:
    graph, program = compile_ir(SCHEMA)
    rep = check_concurrency(program, graph, PlacementPlan.of({}), NO_TXN)
    assert rep.overall == "BROKEN" and rep.counts["BROKEN"] >= 1
    assert sum(rep.counts.values()) == len(rep.verdicts)
    g2, p2 = compile_ir("CREATE TABLE t (id INT PRIMARY KEY);")
    assert check_concurrency(p2, g2).overall == "SAFE"


def test_scenarios_follow_the_schema_and_plan() -> None:
    graph, program = compile_ir(SCHEMA + TXN_SINGLE)
    ids = {v.node_id for v in check_concurrency(program, graph, PlacementPlan.of(EMB)).verdicts}
    assert {
        f"conc:fk_race:{FK}",
        "conc:dup:c.sku",
        "conc:dup:p.code",
        "conc:array:c",
        "conc:txn:t1",
    } <= ids
    # no embedding, no array scenario
    plain = {v.node_id for v in check_concurrency(program, graph, PlacementPlan.of({})).verdicts}
    assert "conc:array:c" not in plain


# ------------------------------------------------------------------- static helper analysis
def test_shipped_helpers_lock_guard_and_retry() -> None:
    h = shipped_profile()
    assert h.insert_locks_parent and h.delete_locks_row and h.update_locks_key
    assert h.array_insert_is_atomic and h.counter_is_atomic and h.transactions_retry


def test_the_analysis_sees_reads_writes_and_check_then_act() -> None:
    src = (
        "def racy(db):\n"
        "    if not db.t.find_one({'k': 1}):\n"
        "        db.t.insert_one({'k': 1})\n"
        "def atomic(db):\n"
        "    db.t.find_one_and_update({'k': 1}, {'$inc': {'n': 1}}, upsert=True)\n"
        "def locked(db, s):\n"
        "    lock_row(db, 't', {}, s)\n"
        "    if not db.t.find_one({'k': 1}):\n"
        "        db.t.insert_one({'k': 1})\n"
        "def guarded(db):\n"
        "    if db.t.find_one({'k': 1}):\n"
        "        pass\n"
        "    db.t.update_one({'a': {'$elemMatch': {}}}, {})\n"
    )
    p = analyze_source(src)
    assert p.get("racy").check_then_act and not p.get("racy").locks
    assert p.get("atomic").single_atomic_write and not p.get("atomic").check_then_act
    assert p.get("locked").locks and not p.get("locked").check_then_act
    assert not p.get("guarded").check_then_act  # an atomic guard folds the check into the write


def test_removing_a_safeguard_changes_the_profile() -> None:
    assert not NO_LOCKS.insert_locks_parent and not NO_LOCKS.delete_locks_row
    assert not NO_GUARD.array_insert_is_atomic
    assert not RACY_COUNTER.counter_is_atomic
    assert shipped_profile().get("insert_row").reads and shipped_profile().get("insert_row").writes


def test_only_top_level_functions_are_profiled() -> None:
    names = set(shipped_profile().functions)
    assert {
        "insert_row",
        "delete_row",
        "update_row",
        "next_id",
        "run_in_transaction",
        "lock_row",
    } <= names
