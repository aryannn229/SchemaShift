"""Phase 23 acceptance: a migration is killed mid-run, resumed, and passes every consistency check.

The kill is real: the CLI runs in a child process that calls ``os._exit(137)`` after a number of batches
(no cleanup, no finally blocks), so what is left on disk is exactly what a power cut would leave.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest

from schemashift.migrate import Job, dry_run, load, status
from schemashift.migrate.runtime import CRASH_ENV, QUARANTINE
from schemashift.pipeline import CompileOptions, compile_sql
from tests.conftest import MONGO_URL, PG_DSN

pytestmark = pytest.mark.integration

SCHEMA = """
CREATE TABLE customers (id INT PRIMARY KEY, name TEXT NOT NULL, tier TEXT);
CREATE TABLE orders (
  id INT PRIMARY KEY, customer_id INT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
  price NUMERIC(10,2) NOT NULL CHECK (price > 0));
CREATE TABLE items (id INT PRIMARY KEY, order_id INT NOT NULL REFERENCES orders(id), sku TEXT NOT NULL);
"""
N_CUSTOMERS = 40


def load_data(conn: Any, bad_prices: bool = False) -> None:
    # the CHECK is added NOT VALID afterwards, so PostgreSQL keeps rows that MongoDB's validator rejects
    conn.execute(SCHEMA.replace("CHECK (price > 0)", ""))
    for i in range(1, N_CUSTOMERS + 1):
        conn.execute(
            "INSERT INTO customers VALUES (%s, %s, %s)", (i, f"c{i}", "gold" if i % 3 else None)
        )
        price = -1 if bad_prices and i % 10 == 0 else 5 + i
        conn.execute("INSERT INTO orders VALUES (%s, %s, %s)", (i, i, price))
        conn.execute("INSERT INTO orders VALUES (%s, %s, %s)", (100 + i, i, 7 + i))
        conn.execute("INSERT INTO items VALUES (%s, %s, %s)", (i, i, f"sku{i}"))
    conn.execute("ALTER TABLE orders ADD CONSTRAINT price_pos CHECK (price > 0) NOT VALID")


def cli(
    args: list[str], env_extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", **(env_extra or {})}
    return subprocess.run(
        [sys.executable, "-m", "schemashift.cli", *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )


@pytest.fixture
def setup(pg_schema: Any, mongo_client: Any, tmp_path: Path) -> Any:
    conn, schema_name = pg_schema
    database = f"t_{uuid.uuid4().hex[:12]}"
    schema_file = tmp_path / "schema.sql"
    schema_file.write_text(SCHEMA, encoding="utf-8")
    base = [
        str(schema_file),
        "--source",
        PG_DSN,
        "--target",
        MONGO_URL,
        "--database",
        database,
        "--pg-schema",
        schema_name,
        "--state-dir",
        str(tmp_path / "state"),
    ]
    yield conn, mongo_client[database], base, tmp_path / "state", schema_file
    mongo_client.drop_database(database)


def make_job(setup_: Any, **kw: Any) -> Job:
    conn, db, base, state, schema_file = setup_
    opts = CompileOptions()
    result = compile_sql(schema_file.read_text(encoding="utf-8"), "", "", opts)
    return Job(
        result, opts, PG_DSN, MONGO_URL, db.name, state, base[base.index("--pg-schema") + 1], **kw
    )


def test_killed_migration_resumes_and_passes_every_check(setup: Any) -> None:
    conn, db, base, state, _ = setup
    load_data(conn)
    crashed = cli(["migrate", "run", *base, "--batch-size", "7"], {CRASH_ENV: "3"})
    assert crashed.returncode == 137, crashed.stderr[-500:]
    m = load(state)
    assert m is not None and m.state == "running"  # the process died without updating anything else
    partial = {t.table: (t.state, t.written) for t in m.tables}
    assert any(s == "running" and 0 < w < N_CUSTOMERS for s, w in partial.values()), partial
    stored = db["customers"].count_documents({})
    assert 0 < stored < N_CUSTOMERS

    resumed = cli(["migrate", "resume", *base, "--batch-size", "7"])
    assert resumed.returncode == 0, resumed.stderr[-800:]
    m = load(state)
    assert m is not None and m.state == "completed" and m.resumed == 1
    assert db["customers"].count_documents({}) == N_CUSTOMERS  # nothing missing
    ids = [d["_id"] for d in db["customers"].find({}, {"_id": 1})]
    assert len(ids) == len(set(ids)) == N_CUSTOMERS  # nothing duplicated
    assert any("resumed" in a for a in m.recovery_actions)

    checks = cli(["migrate", "verify", *base])
    assert checks.returncode == 0, checks.stdout[-1500:]
    assert "FAIL" not in checks.stdout and "PASS  document count customers" in checks.stdout
    assert "sampled deep comparison customers" in checks.stdout
    st = status(state)
    assert (
        st["state"] == "completed"
        and st["checks"]
        and all(c.startswith("ok") for c in st["checks"])
    )


def test_rerunning_a_finished_migration_changes_nothing(setup: Any) -> None:
    conn, db, base, state, _ = setup
    load_data(conn)
    assert cli(["migrate", "run", *base]).returncode == 0
    before = list(db["customers"].find().sort("_id", 1))
    again = cli(["migrate", "run", *base])
    assert again.returncode == 0
    assert list(db["customers"].find().sort("_id", 1)) == before


def test_dry_run_writes_nothing_and_reports_the_records_that_would_fail(setup: Any) -> None:
    conn, db, base, state, _ = setup
    load_data(conn, bad_prices=True)
    with make_job(setup) as job:
        report = dry_run(job)
    assert report.writes_performed == 0
    assert db.list_collection_names() == []  # not even the collections were created
    assert not state.exists()
    assert report.tables["customers"] == N_CUSTOMERS
    assert report.estimated_storage_bytes > 0 and report.avg_document_bytes > 0
    assert report.invalid_in_sample == 4  # customers 10, 20, 30, 40 have a non-positive price
    assert all(
        "orders[" in " ".join(p["reasons"]) and "price" in " ".join(p["reasons"])
        for p in report.problems
    )
    cmd = cli(["migrate", "dry-run", *base])
    assert (
        cmd.returncode == 1 and "writes performed : 0" in cmd.stdout
    )  # exit 1: records would fail


def test_invalid_records_are_quarantined_not_dropped(setup: Any) -> None:
    conn, db, base, state, _ = setup
    load_data(conn, bad_prices=True)
    done = cli(["migrate", "run", *base])
    assert done.returncode == 0, done.stderr[-800:]
    m = load(state)
    assert m is not None and m.quarantine_total == 4
    assert db[QUARANTINE].count_documents({}) == 4
    held = db[QUARANTINE].find_one({})
    assert held is not None and held["reasons"] and held["doc"]  # the whole record is kept
    # the orders are embedded: a customer with one invalid order is quarantined as a whole
    assert db["customers"].count_documents({}) == N_CUSTOMERS - 4
    embedded = sum(len(d.get("orders", [])) for d in db["customers"].find({}, {"orders": 1}))
    assert embedded == 2 * (N_CUSTOMERS - 4)
    checks = cli(["migrate", "verify", *base])
    assert checks.returncode == 0, checks.stdout[
        -1500:
    ]  # counts add up: written + quarantined == rows
    assert json.loads(json.dumps(status(state)))["quarantined"] == 4


def test_status_before_any_run_and_resume_without_manifest(setup: Any) -> None:
    _, _, base, state, _ = setup
    assert status(state) == {"state": "none"}
    r = cli(["migrate", "resume", *base])
    assert r.returncode == 2 and "nothing to resume" in r.stderr


# ---------------------------------------------------------------------------------- scripts
def _script_ns(alter: str, schema: str = SCHEMA) -> dict[str, Any]:
    from schemashift.evolve import plan_evolution, render_script
    from tests.integration.test_evolve import load_script

    return load_script(render_script(plan_evolution(schema, alter_sql=alter)))


def test_script_with_a_manual_step_needs_consent_and_takes_a_snapshot(setup: Any) -> None:
    from schemashift.migrate import LossyStepsNeedConsent, rollback_script, run_script

    conn, db, base, state, _ = setup
    load_data(conn)
    assert cli(["migrate", "run", *base]).returncode == 0
    ns = _script_ns(
        "ALTER TABLE customers ADD COLUMN vip BOOLEAN DEFAULT false;"
        "ALTER TABLE customers DROP CONSTRAINT customers_pkey; ALTER TABLE customers ADD PRIMARY KEY (name);"
    )
    assert any(s["manual"] for s in ns["STEPS"])
    script_state = state.parent / "script-state"
    with pytest.raises(LossyStepsNeedConsent):
        run_script(db, ns, script_state, database=db.name)
    # the safe steps before the manual one ran; the manual one is refused, not skipped silently
    m = load(script_state)
    assert m is not None and m.state == "failed" and "irreversible" in (m.error or "")
    done = run_script(
        db, ns, script_state, database=db.name, consent_lossy=True, log=lambda *_: None
    )
    assert done.state == "completed"
    skipped = [s for s in done.steps if s.state == "skipped"]
    assert skipped and skipped[0].snapshot and skipped[0].snapshot in db.list_collection_names()
    assert any("snapshot of customers" in a for a in done.recovery_actions)
    assert db["customers"].find_one({"_id": 1})["vip"] is False  # type: ignore[index]
    # rollback undoes the reversible steps; the manual step was never executed, its snapshot stays
    rolled = rollback_script(db, ns, script_state, log=lambda *_: None)
    assert rolled.state == "rolled_back"
    assert "vip" not in db["customers"].find_one({"_id": 1})  # type: ignore[operator]


def test_script_run_is_resumable_after_a_crash(setup: Any, tmp_path: Path) -> None:
    from schemashift.evolve import plan_evolution, render_script

    conn, db, base, state, _ = setup
    load_data(conn)
    assert cli(["migrate", "run", *base]).returncode == 0
    alter = (
        "ALTER TABLE customers ADD COLUMN nick TEXT DEFAULT 'x';"
        "ALTER TABLE customers RENAME COLUMN tier TO level;"
        "ALTER TABLE customers DROP COLUMN name;"
    )
    script = tmp_path / "evolve.py"
    script.write_text(render_script(plan_evolution(SCHEMA, alter_sql=alter)), encoding="utf-8")
    sdir = tmp_path / "sstate"
    args = [
        "migrate",
        "apply",
        str(script),
        "--target",
        MONGO_URL,
        "--database",
        db.name,
        "--state-dir",
        str(sdir),
    ]
    killed = cli(args, {"SCHEMASHIFT_CRASH_AFTER_STEPS": "2"})
    assert killed.returncode == 137
    m = load(sdir)
    assert m is not None and 0 < sum(s.state == "done" for s in m.steps) < len(m.steps)
    again = cli(args)
    assert again.returncode == 0, again.stderr[-600:]
    m = load(sdir)
    assert m is not None and m.state == "completed" and m.resumed == 1
    doc = db["customers"].find_one({"_id": 1})
    assert doc is not None and doc["nick"] == "x" and "name" not in doc and doc["level"] == "gold"
