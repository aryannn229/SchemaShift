"""Every rule x every condition branch has a test. The meta-test at the bottom enforces it."""

import re
from dataclasses import dataclass, field

import pytest

import schemashift.equivalence.rules  # noqa: F401
from schemashift.equivalence import RuleOptions
from schemashift.equivalence.registry import all_rules
from schemashift.models import Placement, Status
from tests.unit.equivalence.helpers import verdict

P = "CREATE TABLE p (id INT PRIMARY KEY, code TEXT UNIQUE NOT NULL);"
C = "CREATE TABLE c (id INT PRIMARY KEY, pid INT {col} REFERENCES p(id) {actions});"
FK = "fk:c.pid->p.id"
EMB: dict[str, Placement] = {FK: "EMBED"}


def child(actions: str = "", col: str = "") -> str:
    return P + C.format(actions=actions, col=col)


ARRAY_CHILD = (
    P + "CREATE TABLE c (id INT PRIMARY KEY, pid INT REFERENCES p(id), sku TEXT NOT NULL UNIQUE);"
)
CHAIN = (
    P
    + "CREATE TABLE c (id INT PRIMARY KEY, pid INT REFERENCES p(id) ON DELETE CASCADE);"
    + "CREATE TABLE g (id INT PRIMARY KEY, cid INT REFERENCES c(id) ON DELETE CASCADE);"
)
JUNCTION = (
    "CREATE TABLE a (id INT PRIMARY KEY); CREATE TABLE b (id INT PRIMARY KEY);"
    "CREATE TABLE ab (a_id INT REFERENCES a(id), b_id INT REFERENCES b(id), PRIMARY KEY (a_id, b_id));"
)
TXN = child() + "BEGIN; UPDATE p SET code = 'x' WHERE id = 1; DELETE FROM c WHERE id = 1; COMMIT;"
JOINS = child() + "SELECT p.code FROM p {kind} JOIN c ON c.pid = p.id;"


@dataclass(frozen=True)
class Case:
    rule_id: str
    outcome: str
    status: Status
    sql: str
    node_id: str
    plan: dict[str, Placement] = field(default_factory=dict)
    options: RuleOptions | None = None


CASES = [
    # --- EQ-REF-INTEGRITY
    Case("EQ-REF-INTEGRITY", "EMBEDDED", "SAFE", child(), FK, EMB),
    Case("EQ-REF-INTEGRITY", "REFERENCED", "CHANGED", child(), FK),
    # --- EQ-CASCADE-DEL
    Case(
        "EQ-CASCADE-DEL",
        "EMBEDDED",
        "SAFE",
        child("ON DELETE CASCADE"),
        "cascade_delete:c.pid->p.id",
        EMB,
    ),
    Case(
        "EQ-CASCADE-DEL",
        "REFERENCED",
        "BROKEN",
        child("ON DELETE CASCADE"),
        "cascade_delete:c.pid->p.id",
    ),
    Case("EQ-CASCADE-DEL", "CHAIN", "BROKEN", CHAIN, "cascade_delete:c.pid->p.id"),
    Case("EQ-CASCADE-DEL", "CHAIN", "BROKEN", CHAIN, "cascade_delete:c.pid->p.id", {FK: "EMBED"}),
    Case(
        "EQ-CASCADE-DEL",
        "EMBEDDED",
        "SAFE",
        CHAIN,
        "cascade_delete:c.pid->p.id",
        {FK: "EMBED", "fk:g.cid->c.id": "EMBED"},
    ),
    # --- EQ-CASCADE-UPD
    Case(
        "EQ-CASCADE-UPD",
        "EMBEDDED",
        "SAFE",
        child("ON UPDATE CASCADE"),
        "cascade_update:c.pid->p.id",
        EMB,
    ),
    Case(
        "EQ-CASCADE-UPD",
        "IMMUTABLE_KEY",
        "CHANGED",
        child("ON UPDATE CASCADE"),
        "cascade_update:c.pid->p.id",
    ),
    Case(
        "EQ-CASCADE-UPD",
        "MUTABLE_KEY",
        "BROKEN",
        P + "CREATE TABLE c (id INT PRIMARY KEY, pcode TEXT REFERENCES p(code) ON UPDATE CASCADE);",
        "cascade_update:c.pcode->p.code",
    ),
    # --- EQ-SET-NULL / EQ-SET-DEFAULT
    Case(
        "EQ-SET-NULL",
        "EMBEDDED",
        "CHANGED",
        child("ON DELETE SET NULL"),
        "set_null:c.pid->p.id",
        EMB,
    ),
    Case(
        "EQ-SET-NULL", "REFERENCED", "BROKEN", child("ON DELETE SET NULL"), "set_null:c.pid->p.id"
    ),
    Case(
        "EQ-SET-DEFAULT",
        "EMBEDDED",
        "CHANGED",
        child("ON DELETE SET DEFAULT"),
        "set_default:c.pid->p.id",
        EMB,
    ),
    Case(
        "EQ-SET-DEFAULT",
        "REFERENCED",
        "BROKEN",
        child("ON DELETE SET DEFAULT"),
        "set_default:c.pid->p.id",
    ),
    # --- EQ-RESTRICT
    Case(
        "EQ-RESTRICT",
        "EMBEDDED",
        "CHANGED",
        child("ON DELETE RESTRICT"),
        "restrict_delete:c.pid->p.id",
        EMB,
    ),
    Case("EQ-RESTRICT", "REFERENCED", "CHANGED", child(), "restrict_delete:c.pid->p.id"),
    # --- EQ-ENTITY-UNIQ
    Case("EQ-ENTITY-UNIQ", "PK_SINGLE", "SAFE", child(), "pk:c.id"),
    Case(
        "EQ-ENTITY-UNIQ",
        "PK_COMPOSITE",
        "SAFE",
        "CREATE TABLE t (a INT, b INT, PRIMARY KEY (a, b));",
        "pk:t.a,b",
    ),
    Case("EQ-ENTITY-UNIQ", "PK_EMBEDDED", "CHANGED", child(), "pk:c.id", EMB),
    Case(
        "EQ-ENTITY-UNIQ",
        "PK_SINGLE",
        "SAFE",
        P + "CREATE TABLE c (pid INT PRIMARY KEY REFERENCES p(id));",
        "pk:c.pid",
        {FK: "EMBED"},
    ),
    Case(
        "EQ-ENTITY-UNIQ",
        "PK_FOLDED",
        "CHANGED",
        JUNCTION,
        "pk:ab.a_id,b_id",
        {"m2n:ab": "REF_ARRAY"},
    ),
    Case("EQ-ENTITY-UNIQ", "PK_COMPOSITE", "SAFE", JUNCTION, "pk:ab.a_id,b_id"),
    # --- EQ-VALUE-UNIQ
    Case("EQ-VALUE-UNIQ", "EMBEDDED", "CHANGED", ARRAY_CHILD, "unique:c.sku", EMB),
    Case(
        "EQ-VALUE-UNIQ", "UNIQUE_INDEX", "SAFE", child(col="UNIQUE NOT NULL"), "unique:c.pid", EMB
    ),
    Case(
        "EQ-VALUE-UNIQ",
        "PARTIAL_OK",
        "SAFE",
        "CREATE TABLE t (a TEXT NOT NULL, d BOOL); CREATE UNIQUE INDEX i ON t (a) WHERE d = false;",
        "unique:t.a",
    ),
    Case(
        "EQ-VALUE-UNIQ",
        "PARTIAL_UNTRANSLATABLE",
        "CHANGED",
        "CREATE TABLE t (a TEXT NOT NULL, d TEXT); CREATE UNIQUE INDEX i ON t (a) WHERE lower(d) = 'x';",
        "unique:t.a",
    ),
    Case("EQ-VALUE-UNIQ", "NULLABLE", "CHANGED", "CREATE TABLE t (a TEXT UNIQUE);", "unique:t.a"),
    Case(
        "EQ-VALUE-UNIQ",
        "UNIQUE_INDEX",
        "SAFE",
        "CREATE TABLE t (a TEXT NOT NULL UNIQUE);",
        "unique:t.a",
    ),
    # --- EQ-CROSS-UNIQ
    Case("EQ-CROSS-UNIQ", "OWN_COLLECTION", "SAFE", JUNCTION, "cross_unique:ab.a_id,b_id"),
    Case(
        "EQ-CROSS-UNIQ",
        "EMBEDDED",
        "CHANGED",
        JUNCTION,
        "cross_unique:ab.a_id,b_id",
        {"fk:ab.a_id->a.id": "EMBED"},
    ),
    Case(
        "EQ-CROSS-UNIQ",
        "SPLIT",
        "BROKEN",
        JUNCTION,
        "cross_unique:ab.a_id,b_id",
        {"m2n:ab": "REF_ARRAY"},
    ),
    # --- EQ-NOT-NULL / EQ-ENUM
    Case("EQ-NOT-NULL", "VALIDATOR", "SAFE", "CREATE TABLE t (a INT NOT NULL);", "not_null:t.a"),
    Case(
        "EQ-ENUM",
        "ENUM_LIST",
        "SAFE",
        "CREATE TYPE m AS ENUM ('x'); CREATE TABLE t (a m);",
        "enum:t.a",
    ),
    # --- EQ-CHECK
    Case(
        "EQ-CHECK",
        "SAFE_JSONSCHEMA",
        "SAFE",
        "CREATE TABLE t (a INT CHECK (a BETWEEN 1 AND 5));",
        "check:t.a_BETWEEN_1_AND_5",
    ),
    Case(
        "EQ-CHECK",
        "SAFE_EXPR",
        "SAFE",
        "CREATE TABLE t (a INT, b INT, CONSTRAINT ck CHECK (a <= b));",
        "check:t.ck",
    ),
    Case(
        "EQ-CHECK",
        "UNTRANSLATABLE",
        "CHANGED",
        "CREATE TABLE t (a TEXT, CONSTRAINT ck CHECK (upper(a) = 'X'));",
        "check:t.ck",
    ),
    # --- EQ-TYPE
    Case("EQ-TYPE", "EXACT", "SAFE", "CREATE TABLE t (a INT);", "type:t.a"),
    Case("EQ-TYPE", "LOSSY", "CHANGED", "CREATE TABLE t (a TIMESTAMPTZ);", "type:t.a"),
    # --- EQ-DEFAULT
    Case("EQ-DEFAULT", "CONSTANT", "CHANGED", "CREATE TABLE t (a INT DEFAULT 1);", "default:t.a"),
    Case(
        "EQ-DEFAULT", "NOW", "CHANGED", "CREATE TABLE t (a TIMESTAMP DEFAULT now());", "default:t.a"
    ),
    Case(
        "EQ-DEFAULT",
        "EXPRESSION",
        "CHANGED",
        "CREATE TABLE t (a UUID DEFAULT gen_random_uuid());",
        "default:t.a",
    ),
    # --- EQ-AUTOINC
    Case("EQ-AUTOINC", "OBJECTID", "CHANGED", "CREATE TABLE t (a SERIAL);", "auto_increment:t.a"),
    Case(
        "EQ-AUTOINC",
        "COUNTER",
        "CHANGED",
        "CREATE TABLE t (a SERIAL);",
        "auto_increment:t.a",
        options=RuleOptions(preserve_integer_ids=True),
    ),
    # --- EQ-ATOMIC
    Case("EQ-ATOMIC", "SINGLE_DOCUMENT", "SAFE", TXN, "txn:t1", EMB),
    Case("EQ-ATOMIC", "TRANSACTION", "CHANGED", TXN, "txn:t1"),
    Case(
        "EQ-ATOMIC",
        "NO_TRANSACTION",
        "BROKEN",
        TXN,
        "txn:t1",
        options=RuleOptions(transactions_available=False),
    ),
    # --- EQ-JOIN
    Case("EQ-JOIN", "INNER_EMBEDDED", "SAFE", JOINS.format(kind="INNER"), "join:q1#0", EMB),
    Case("EQ-JOIN", "INNER_REFERENCED", "SAFE", JOINS.format(kind="INNER"), "join:q1#0"),
    Case("EQ-JOIN", "LEFT_CLEAN", "SAFE", JOINS.format(kind="LEFT"), "join:q1#0"),
    Case(
        "EQ-JOIN",
        "LEFT_NULLS",
        "CHANGED",
        child() + "SELECT p.code, c.id FROM p LEFT JOIN c ON c.pid = p.id;",
        "join:q1#0",
    ),
    # --- EQ-AGG
    Case(
        "EQ-AGG",
        "AGG_SAFE",
        "SAFE",
        child() + "SELECT pid, COUNT(*), MIN(id), MAX(id) FROM c GROUP BY pid;",
        "aggregate:q1",
    ),
    Case("EQ-AGG", "AGG_CHANGED", "CHANGED", child() + "SELECT SUM(id) FROM c;", "aggregate:q1"),
]


@pytest.mark.parametrize(
    "case", CASES, ids=[f"{c.rule_id}-{c.outcome}-{i}" for i, c in enumerate(CASES)]
)
def test_branch(case: Case) -> None:
    v = verdict(case.sql, case.node_id, case.plan, case.options)
    assert (v.rule_id, v.outcome, v.status) == (case.rule_id, case.outcome, case.status)
    unresolved = re.compile(r"\{[a-z_.]+\}")
    assert v.reason and not unresolved.search(v.reason)  # template fully rendered
    assert v.mitigation is None or not unresolved.search(v.mitigation)
    assert v.conditions_evaluated, "every verdict records the conditions it checked"


def test_every_registered_outcome_is_covered() -> None:
    covered = {(c.rule_id, c.outcome) for c in CASES}
    declared = {(spec.rule_id, o.key) for spec in all_rules() for o in spec.outcomes}
    assert declared - covered == set(), f"untested outcomes: {sorted(declared - covered)}"
    assert covered - declared == set()


def test_every_rule_covers_every_ir_guarantee_node_type() -> None:
    from schemashift.ir import nodes

    guarantee_types = {
        nodes.ReferentialIntegrity,
        nodes.CascadingDelete,
        nodes.CascadingUpdate,
        nodes.SetNullOnDelete,
        nodes.SetDefaultOnDelete,
        nodes.RestrictDelete,
        nodes.EntityUniqueness,
        nodes.ValueUniqueness,
        nodes.CrossEntityUniqueness,
        nodes.NotNullGuarantee,
        nodes.DomainConstraint,
        nodes.TypeGuarantee,
        nodes.DefaultValue,
        nodes.AutoIncrement,
        nodes.EnumDomain,
        nodes.MultiEntityAtomicity,
        nodes.JoinSemantics,
        nodes.AggregateSemantics,
    }
    assert {s.node_type for s in all_rules()} == guarantee_types


def test_status_matches_declared_outcome() -> None:
    for spec in all_rules():
        for o in spec.outcomes:
            assert o.status in ("SAFE", "CHANGED", "BROKEN")
