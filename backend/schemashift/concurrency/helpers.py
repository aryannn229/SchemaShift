"""Static analysis of the generated enforcement helpers: which guarantees rely on atomic primitives?

The helpers are plain Python (``codegen/runtime_helpers.py``, embedded verbatim in every generated
``helpers.py``). A helper that *reads* before it *writes* is a check-then-act sequence and can race;
one that takes a lock document inside a transaction, or folds the check into a single-document
conditional update, cannot. This module parses the runtime source with ``ast`` and reports, per helper,
which reads, writes and guards it contains, so the concurrency rules decide from the code that is
actually shipped instead of from a hand-kept list that could drift.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from functools import cache
from importlib import resources

READS = frozenset({"find", "find_one", "locate", "exists", "count_documents"})
WRITES = frozenset(
    {
        "insert_one",
        "insert_many",
        "update_one",
        "update_many",
        "delete_one",
        "delete_many",
        "find_one_and_update",
    }
)
LOCK_CALL = "lock_row"
ATOMIC_GUARD = "$elemMatch"  # check-and-push folded into one conditional update


@dataclass(frozen=True)
class FunctionProfile:
    """What one helper does: the reads, writes and lock calls it makes and the strings it uses."""

    name: str
    reads: frozenset[str]
    writes: frozenset[str]
    locks: bool
    strings: frozenset[str]

    @property
    def check_then_act(self) -> bool:
        """Reads and writes in separate operations (can race) with no lock and no atomic guard."""
        return (
            bool(self.reads and self.writes) and not self.locks and ATOMIC_GUARD not in self.strings
        )

    @property
    def single_atomic_write(self) -> bool:
        """One write and no read: atomic by itself (for example the counter's ``$inc``)."""
        return bool(self.writes) and not self.reads


@dataclass(frozen=True)
class HelperProfile:
    """The concurrency-relevant facts about the shipped helpers."""

    functions: dict[str, FunctionProfile]

    def get(self, name: str) -> FunctionProfile:
        return self.functions[name]

    @property
    def insert_locks_parent(self) -> bool:
        return self.get("insert_row").locks

    @property
    def delete_locks_row(self) -> bool:
        return self.get("delete_row").locks

    @property
    def update_locks_key(self) -> bool:
        return self.get("update_row").locks

    @property
    def array_insert_is_atomic(self) -> bool:
        return ATOMIC_GUARD in self.get("insert_row").strings

    @property
    def counter_is_atomic(self) -> bool:
        return self.get("next_id").single_atomic_write

    @property
    def transactions_retry(self) -> bool:
        """``run_in_transaction`` delegates to ``with_transaction`` (retries transient errors)."""
        return (
            "with_transaction"
            in self.get("run_in_transaction").reads
            | self.get("run_in_transaction").writes
            | self.get("run_in_transaction").strings
        )


def _call_name(node: ast.Call) -> str | None:
    f = node.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return None


def analyze_source(source: str) -> HelperProfile:
    """Profile every top-level function of the runtime helper module."""
    tree = ast.parse(source)
    out: dict[str, FunctionProfile] = {}
    for fn in tree.body:
        if not isinstance(fn, ast.FunctionDef):
            continue
        calls: set[str] = set()
        strings: set[str] = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and (name := _call_name(node)):
                calls.add(name)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                strings.add(node.value)
            elif isinstance(node, ast.Attribute):
                strings.add(node.attr)  # e.g. session.with_transaction passed as a reference
        out[fn.name] = FunctionProfile(
            name=fn.name,
            reads=frozenset(calls & READS),
            writes=frozenset(calls & WRITES),
            locks=LOCK_CALL in calls,
            strings=frozenset(strings),
        )
    return HelperProfile(functions=out)


@cache
def runtime_source() -> str:
    return resources.files("schemashift.codegen").joinpath("runtime_helpers.py").read_text("utf-8")


@cache
def shipped_profile() -> HelperProfile:
    """The profile of the helpers this installation generates."""
    return analyze_source(runtime_source())
