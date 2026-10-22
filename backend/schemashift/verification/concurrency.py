"""Concurrency probes: run the generated helpers from parallel threads and count what slips through.

For each referenced foreign key, embedded-array uniqueness, id counter and transaction scenario of a
schema the harness races two or more writers against the sandbox replica set, many rounds, with seeded
random start jitter (so a run is reproducible in what it does; the interleavings themselves remain the
database's). Each probe runs twice: ``generated`` (the helpers as shipped) and ``unguarded`` (the same
operations with the concurrency guard removed), so a nonzero count for the second shows the hazard is real
and a zero for the first is evidence the guard works. Zero violations is test evidence, never proof.
"""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any

from pymongo.errors import PyMongoError

from schemashift.models.concurrency import ConcurrencyResult, ConcurrencySkip, Kind, Strategy
from schemashift.pipeline import CompileResult
from schemashift.semantic.graph import Relationship
from schemashift.verification.ddl import q
from schemashift.verification.runner import Env, run_python_source

INT_TYPES = ("INTEGER", "BIGINT", "SMALLINT")
TEXT_TYPES = ("TEXT", "VARCHAR", "CHAR")
MAX_JITTER_S = 0.002
Outcome = tuple[ConcurrencyResult | None, ConcurrencySkip | None]
STRATEGIES: tuple[Strategy, Strategy] = ("generated", "unguarded")


class _Probe:
    """Shared plumbing: helpers namespace, sandbox handles, seeded jitter and the race itself."""

    def __init__(self, env: Env, seed: int, threads: int, iterations: int) -> None:
        self.env = env
        self.db = env.sb.mongo_db
        self.client = env.sb.mongo_db.client
        self.seed = seed
        self.threads = threads
        self.iterations = iterations
        self.ns: dict[str, Any] = run_python_source(
            env.codegen.file("python/helpers.py").content, ""
        )
        self.plan = self.ns["PLAN"]

    # ------------------------------------------------------------------ strategies
    def guards(self, on: bool) -> None:
        self.ns["GUARDS"] = on

    def tx(self, fn: Callable[[Any], Any]) -> Any:
        return self.ns["run_in_transaction"](self.client, fn)

    def tx_call(self, helper: str, table: str, arg: dict[str, Any]) -> Any:
        """Call a helper (insert_row / delete_row) inside one retried transaction."""
        return self.tx(lambda session: self.ns[helper](self.db, self.plan, table, arg, session))

    # ---------------------------------------------------------------------- the race
    def race(self, jobs: list[Callable[[], Any]], rng: random.Random) -> list[BaseException | None]:
        """Run ``jobs`` at once (barrier start, seeded sub-2 ms jitter); each job's exception or None."""
        barrier = threading.Barrier(len(jobs))
        delays = [rng.random() * MAX_JITTER_S for _ in jobs]

        def run(i: int) -> BaseException | None:
            try:
                barrier.wait(timeout=10)
                time.sleep(delays[i])
                jobs[i]()
                return None
            except BaseException as exc:  # noqa: BLE001 (an expected IntegrityError is a normal outcome)
                return exc

        with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
            return list(pool.map(run, range(len(jobs))))

    # ------------------------------------------------------------------ row cloning
    def fetch_row(self, table: str) -> dict[str, Any] | None:
        cur = self.env.sb.pg.execute(f"SELECT * FROM {q(table)} LIMIT 1")
        row = cur.fetchone()
        if row is None or cur.description is None:
            return None
        return {d.name: v for d, v in zip(cur.description, row, strict=True)}

    def key_columns(self, table: str) -> set[str]:
        t = self.env.schema.tables[table]
        cols: set[str] = set(t.primary_key.columns if t.primary_key else ())
        for u in t.uniques:
            cols.update(u.columns)
        for ix in t.indexes:
            if ix.unique:
                cols.update(ix.columns)
        return cols

    def fresh(self, table: str, row: dict[str, Any], ordinal: int) -> dict[str, Any] | None:
        """Copy ``row`` with new primary/unique values (None when a key type cannot be varied)."""
        t = self.env.schema.tables[table]
        out = dict(row)
        for name in self.key_columns(table):
            col = t.column(name)
            if col is None:
                return None
            base = col.sql_type.base
            if base in INT_TYPES:
                out[name] = 1_000_000 + ordinal
            elif base in TEXT_TYPES:
                limit = col.sql_type.length or 40
                suffix = f"~c{ordinal}"
                out[name] = (str(row[name])[: max(0, limit - len(suffix))] + suffix)[:limit]
                if len(out[name]) < len(suffix):
                    return None
            else:
                return None
        return out

    def result(
        self,
        scenario: str,
        kind: Kind,
        strategy: Strategy,
        violations: int,
        started: float,
        violation: str,
        detail: str = "",
        threads: int | None = None,
        iterations: int | None = None,
    ) -> ConcurrencyResult:
        return ConcurrencyResult(
            scenario_id=scenario,
            kind=kind,
            strategy=strategy,
            threads=threads or self.threads,
            iterations=iterations or self.iterations,
            violations=violations,
            violation=violation,
            seed=self.seed,
            duration_ms=int((time.monotonic() - started) * 1000),
            detail=detail,
        )


# ------------------------------------------------------------------------------ FK race
def _remove(p: _Probe, table: str, key: dict[str, Any]) -> None:
    for doc, _node, _row in p.ns["locate"](p.db, p.plan, table, key):
        coll = p.plan["tables"][table]["collection"]
        p.db[coll].delete_one({"_id": doc["_id"]})


def _pk_columns(env: Env, table: str) -> list[str]:
    pk = env.schema.tables[table].primary_key
    return list(pk.columns) if pk is not None else []


def fk_race(p: _Probe, rel: Relationship, strategy: Strategy) -> Outcome:
    scenario = f"conc:fk_race:{rel.id}"
    env = p.env
    if not (env.layouts[rel.child].is_root and env.layouts[rel.parent].is_root):
        return None, ConcurrencySkip(
            scenario_id=scenario, reason="a side is stored inside another document"
        )
    parent_tpl, child_tpl = p.fetch_row(rel.parent), p.fetch_row(rel.child)
    if parent_tpl is None or child_tpl is None:
        return None, ConcurrencySkip(
            scenario_id=scenario, reason="the seed has no parent or child row to clone"
        )
    ref_cols = list(rel.fk.ref_columns)
    parent_pk = _pk_columns(env, rel.parent) or ref_cols
    child_pk = _pk_columns(env, rel.child)
    p.guards(strategy == "generated")
    rng = random.Random(f"{p.seed}:{scenario}:{strategy}")
    started = time.monotonic()
    orphans = 0
    ran = 0
    for i in range(p.iterations):
        parent = p.fresh(rel.parent, parent_tpl, i * 2 + 1)
        child = p.fresh(rel.child, child_tpl, i * 2 + 1)
        if parent is None or child is None:
            return None, ConcurrencySkip(
                scenario_id=scenario, reason="a key column is not an integer or text"
            )
        for cc, pc in zip(rel.fk.columns, ref_cols, strict=True):
            child[cc] = parent[pc]
        try:
            p.ns["insert_row"](p.db, p.plan, rel.parent, parent, None)
        except Exception as exc:  # noqa: BLE001
            return None, ConcurrencySkip(
                scenario_id=scenario, reason=f"cannot create a parent row: {exc}"
            )
        pkey = {c: parent[c] for c in ref_cols}
        ckey = {c: child[c] for c in child_pk} or {c: child[c] for c in rel.fk.columns}
        p.race(
            [
                partial(p.tx_call, "insert_row", rel.child, child),
                partial(p.tx_call, "delete_row", rel.parent, pkey),
            ],
            rng,
        )
        ran += 1
        found = p.ns["locate"](p.db, p.plan, rel.child, ckey)
        still_pointing = bool(found) and all(
            found[0][2].get(cc) is not None for cc in rel.fk.columns
        )
        if still_pointing and not p.ns["exists"](p.db, p.plan, rel.parent, pkey):
            orphans += 1  # the child kept a foreign key to a parent that no longer exists
        _remove(p, rel.child, ckey)
        _remove(p, rel.parent, {c: parent[c] for c in parent_pk})
    p.guards(True)
    return (
        p.result(
            scenario,
            "fk_race",
            strategy,
            orphans,
            started,
            "orphan child",
            threads=2,
            iterations=ran,
            detail=f"insert {rel.child} vs delete {rel.parent}, each in its own transaction",
        ),
        None,
    )


# ------------------------------------------------------------------- duplicate inserts
def duplicate_insert(
    p: _Probe, table: str, columns: tuple[str, ...], strategy: Strategy
) -> Outcome:
    scenario = f"conc:dup:{table}.{','.join(columns)}"
    env = p.env
    layout = env.layouts[table]
    tpl = p.fetch_row(table)
    if tpl is None:
        return None, ConcurrencySkip(scenario_id=scenario, reason="the seed has no row to clone")
    t = env.schema.tables[table]
    host_ok = layout.is_root or (env.layouts[layout.host or ""].is_root and layout.is_array)
    if not host_ok:
        return None, ConcurrencySkip(
            scenario_id=scenario,
            reason="nested more than one array deep: no atomic guard exists, covered by the rule",
        )
    for c in columns:
        col = t.column(c)
        if col is None or col.sql_type.base not in (*INT_TYPES, *TEXT_TYPES):
            return None, ConcurrencySkip(
                scenario_id=scenario, reason="the key column is not an integer or text"
            )
    embedded = not layout.is_root
    p.guards(strategy == "generated")
    rng = random.Random(f"{p.seed}:{scenario}:{strategy}")
    started = time.monotonic()
    dups = 0
    ran = 0
    use_tx = strategy == "generated" or layout.is_root
    for i in range(p.iterations):
        a = p.fresh(table, tpl, i * 2 + 1)
        b = p.fresh(table, tpl, i * 2 + 2)
        if a is None or b is None:
            return None, ConcurrencySkip(
                scenario_id=scenario, reason="a key column cannot be varied"
            )
        for c in columns:  # the raced key is the same in both rows; other keys differ
            b[c] = a[c]

        def do(row: dict[str, Any]) -> Any:
            if use_tx:
                return p.tx(lambda s: p.ns["insert_row"](p.db, p.plan, table, row, s))
            return p.ns["insert_row"](p.db, p.plan, table, row, None)

        p.race([partial(do, a), partial(do, b)], rng)
        ran += 1
        match = {c: a[c] for c in columns}
        count = len(p.ns["locate"](p.db, p.plan, table, match))
        if count > 1:
            dups += 1
        for doc, _n, _r in p.ns["locate"](p.db, p.plan, table, match):
            if embedded:
                break
            p.db[p.plan["tables"][table]["collection"]].delete_one({"_id": doc["_id"]})
        if embedded:
            _pull_embedded(p, table, match)
    p.guards(True)
    how = (
        "atomic conditional push"
        if embedded and strategy == "generated"
        else ("unique index" if not embedded else "check then push, no guard, no transaction")
    )
    return (
        p.result(
            scenario,
            "duplicate_insert",
            strategy,
            dups,
            started,
            "duplicate key",
            threads=2,
            iterations=ran,
            detail=f"two inserts of one key into {table} ({how})",
        ),
        None,
    )


def _pull_embedded(p: _Probe, table: str, match: dict[str, Any]) -> None:
    for doc, _node, row in p.ns["locate"](p.db, p.plan, table, match):
        p.ns["_remove_row"](p.db, p.plan, table, doc, row, None)


# ---------------------------------------------------------------------------- counters
def counter(p: _Probe, table: str, column: str, strategy: Strategy) -> Outcome:
    scenario = f"conc:counter:{table}.{column}"
    key = f"probe.{p.seed}.{strategy}"
    started = time.monotonic()
    per = max(4, p.iterations // 2)
    ids: list[int] = []
    lock = threading.Lock()
    coll = p.db["_counters"]

    def atomic() -> int:
        return int(p.ns["next_id"](p.db, key, None))

    def racy() -> int:  # what a read-then-write counter would do
        doc = coll.find_one({"_id": key}) or {"seq": 0}
        value = int(doc["seq"]) + 1
        coll.update_one({"_id": key}, {"$set": {"seq": value}}, upsert=True)
        return value

    take = atomic if strategy == "generated" else racy

    def worker() -> None:
        for _ in range(per):
            got = take()
            with lock:
                ids.append(got)

    p.race([worker for _ in range(p.threads)], random.Random(f"{p.seed}:{scenario}"))
    coll.delete_one({"_id": key})
    dups = len(ids) - len(set(ids))
    return (
        p.result(
            scenario,
            "counter",
            strategy,
            dups,
            started,
            "duplicate id",
            iterations=per * p.threads,
            detail=f"{p.threads} writers x {per} ids from next_id"
            if strategy == "generated"
            else "read the counter, then write it back",
        ),
        None,
    )


# ---------------------------------------------------------------------- transactions
def transaction(p: _Probe, scenario: str, strategy: Strategy) -> Outcome:
    """Read-modify-write of two documents under write conflicts: no update may be lost."""
    coll = p.db["_conc_probe"]
    coll.delete_many({})
    coll.insert_many([{"_id": "A", "n": 0}, {"_id": "B", "n": 0}])
    started = time.monotonic()
    per = max(4, p.iterations // 4)
    attempts = 0
    lock = threading.Lock()

    def bump(session: Any) -> None:
        nonlocal attempts
        with lock:
            attempts += 1
        for _id in ("A", "B"):
            doc = coll.find_one({"_id": _id}, session=session)
            coll.update_one({"_id": _id}, {"$set": {"n": doc["n"] + 1}}, session=session)

    def worker() -> None:
        for _ in range(per):
            if strategy == "generated":
                p.tx(bump)
            else:
                bump(None)  # the same writes, without a transaction

    p.race([worker for _ in range(p.threads)], random.Random(f"{p.seed}:{scenario}"))
    expected = per * p.threads
    lost = sum(expected - int(coll.find_one({"_id": i})["n"]) for i in ("A", "B"))
    coll.drop()
    note = f"{p.threads} writers x {per} updates of two documents"
    if strategy == "generated":
        note += f"; the callback ran {attempts} times for {expected} updates (retries after write conflicts)"
    return p.result(
        scenario,
        "transaction",
        strategy,
        lost,
        started,
        "lost update",
        iterations=expected,
        detail=note,
    ), None


# ------------------------------------------------------------------------------- driver
def run_concurrency_probes(
    env: Env,
    result: CompileResult,
    seed: int = 0,
    threads: int = 4,
    iterations: int = 60,
    limit: int = 12,
) -> tuple[list[ConcurrencyResult], list[ConcurrencySkip]]:
    """Probe every scenario that has a referenced foreign key, array key, counter or transaction."""
    p = _Probe(env, seed, threads, iterations)
    results: list[ConcurrencyResult] = []
    skips: list[ConcurrencySkip] = []

    def add(out: Outcome) -> None:
        if out[0] is not None:
            results.append(out[0])
        if out[1] is not None:
            skips.append(out[1])

    done_txn = False
    for v in result.concurrency.verdicts:
        if len(results) + len(skips) >= limit * 2:
            break
        node = v.node_id
        try:
            if node.startswith("conc:fk_race:") and v.outcome in (
                "LOCKED",
                "UNLOCKED",
                "NO_TRANSACTION",
            ):
                rel = result.graph.relationships[node.removeprefix("conc:fk_race:")]
                for strategy in STRATEGIES:
                    add(fk_race(p, rel, strategy))
            elif node.startswith("conc:dup:") and v.outcome.startswith("ARRAY"):
                table, cols = node.removeprefix("conc:dup:").split(".", 1)
                for strategy in STRATEGIES:
                    add(duplicate_insert(p, table, tuple(cols.split(",")), strategy))
            elif node.startswith("conc:dup:") and v.outcome == "INDEX" and "~" not in node:
                table, cols = node.removeprefix("conc:dup:").split(".", 1)
                if cols and not any(r.scenario_id == node for r in results):
                    add(duplicate_insert(p, table, tuple(cols.split(",")), "generated"))
            elif node.startswith("conc:counter:") and v.outcome == "COUNTER":
                table, column = node.removeprefix("conc:counter:").split(".", 1)
                for strategy in STRATEGIES:
                    add(counter(p, table, column, strategy))
            elif node.startswith("conc:txn:") and not done_txn and v.outcome == "RETRIED":
                done_txn = True
                for strategy in STRATEGIES:
                    add(transaction(p, node, strategy))
        except PyMongoError as exc:
            skips.append(ConcurrencySkip(scenario_id=node, reason=f"the probe failed: {exc}"))
    p.guards(True)
    return results, skips
