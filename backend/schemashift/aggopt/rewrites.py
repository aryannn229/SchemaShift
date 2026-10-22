"""Registered pipeline rewrites. Every rule states the precondition under which it preserves
results.

A rule's ``apply`` returns every pipeline reachable by applying the rule once; the search
decides which
to keep. Rules never look at data, only at the stages, so each is pure and testable on its own.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from schemashift.aggopt.paths import (
    Stage,
    refs_in,
    rewrite_refs,
    stage_name,
    stage_reads,
    touches,
)

Pipeline = list[Stage]
ONE_TO_ONE = {"$project", "$addFields", "$set"}


@dataclass(frozen=True)
class RewriteRule:
    id: str
    title: str
    precondition: str
    apply: Callable[[Pipeline], list[Pipeline]]


def _whole_doc(paths: set[str]) -> bool:
    return any(p.startswith("$$") for p in paths)


def _outputs(stage: Stage) -> set[str] | None:
    """Paths a stage writes, for stages that otherwise pass each document through; None =
    unknown."""
    name = stage_name(stage)
    body = stage[name]
    if name == "$lookup":
        return {body["as"]}
    if name == "$unwind":
        out = {body["path"][1:] if isinstance(body, dict) else body[1:]}
        if isinstance(body, dict) and "includeArrayIndex" in body:
            out.add(body["includeArrayIndex"])
        return out
    if name in ("$addFields", "$set"):
        return set(body)
    if name == "$sort":
        return set()
    return None


def _merge_two(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    if all(not k.startswith("$") for k in (*a, *b)) and not set(a) & set(b):
        return {**a, **b}
    return {"$and": [a, b]}


def merge_adjacent_matches(p: Pipeline) -> list[Pipeline]:
    out = []
    for i in range(len(p) - 1):
        if stage_name(p[i]) == "$match" and stage_name(p[i + 1]) == "$match":
            merged = {"$match": _merge_two(p[i]["$match"], p[i + 1]["$match"])}
            out.append([*p[:i], merged, *p[i + 2 :]])
    return out


def match_before_root_wrap(p: Pipeline) -> list[Pipeline]:
    """[$replaceRoot {a: $$ROOT}, $match on a.*] -> [$match on the bare fields, $replaceRoot]."""
    out = []
    for i in range(len(p) - 1):
        s, m = p[i], p[i + 1]
        if stage_name(s) != "$replaceRoot" or stage_name(m) != "$match":
            continue
        new_root = s["$replaceRoot"].get("newRoot")
        if not (isinstance(new_root, dict) and len(new_root) == 1):
            continue
        ((alias, value),) = new_root.items()
        if value != "$$ROOT" or alias.startswith("$"):
            continue
        refs = refs_in(m["$match"])
        if not refs or _whole_doc(refs) or not all(r.startswith(alias + ".") for r in refs):
            continue
        stripped = rewrite_refs(m["$match"], alias, "")
        out.append([*p[:i], {"$match": stripped}, s, *p[i + 2 :]])
    return out


def match_before_stage(p: Pipeline) -> list[Pipeline]:
    """Move a $match in front of a $lookup/$unwind/$addFields/$sort that it does not depend on."""
    out = []
    for i in range(1, len(p)):
        if stage_name(p[i]) != "$match":
            continue
        prev = p[i - 1]
        written = _outputs(prev)
        refs = refs_in(p[i]["$match"])
        if written is None or _whole_doc(refs) or not refs:
            continue
        if stage_name(prev) in ("$addFields", "$set") and _whole_doc(stage_reads(prev)):
            continue
        if any(touches(refs, w) for w in written):
            continue
        out.append([*p[: i - 1], p[i], prev, *p[i + 1 :]])
    return out


def remove_unused_lookup(p: Pipeline) -> list[Pipeline]:
    """Drop a $lookup whose result is never read. An $unwind of it counts as a read (it changes
    rows)."""
    out = []
    for i, s in enumerate(p):
        if stage_name(s) != "$lookup" or "pipeline" in s["$lookup"] or "let" in s["$lookup"]:
            continue
        alias = s["$lookup"]["as"]
        later: set[str] = set()
        for t in p[i + 1 :]:
            later |= stage_reads(t)
        if _whole_doc(later) or touches(later, alias):
            continue
        out.append([*p[:i], *p[i + 1 :]])
    return out


def prune_before_group(p: Pipeline) -> list[Pipeline]:
    """Insert a $project that keeps only what the first $group reads, shrinking the documents
    it sees."""
    for g, s in enumerate(p):
        if stage_name(s) != "$group":
            continue
        if g == 0 or stage_name(p[g - 1]) == "$project":
            return []
        reads = stage_reads(s)
        if not reads or _whole_doc(reads):
            return []
        keep = {r for r in reads if not any(r != q and r.startswith(q + ".") for q in reads)}
        spec: dict[str, Any] = {r: 1 for r in sorted(keep)}
        if "_id" not in reads:
            spec["_id"] = 0
        return [[*p[:g], {"$project": spec}, *p[g:]]]
    return []


def limit_before_projection(p: Pipeline) -> list[Pipeline]:
    """[$sort, 1:1 stage, $limit] -> [$sort, $limit, 1:1 stage] so the server can run a top-k
    sort."""
    out = []
    for i in range(len(p) - 2):
        if (
            stage_name(p[i]) == "$sort"
            and stage_name(p[i + 1]) in ONE_TO_ONE
            and stage_name(p[i + 2]) == "$limit"
        ):
            out.append([*p[: i + 1], p[i + 2], p[i + 1], *p[i + 3 :]])
    return out


REWRITES: tuple[RewriteRule, ...] = (
    RewriteRule(
        "AGG-MERGE-MATCH",
        "Merge adjacent $match stages",
        "none: a conjunction of filters is order independent; keys that do not collide become "
        "one flat "
        "document, otherwise an $and",
        merge_adjacent_matches,
    ),
    RewriteRule(
        "AGG-MATCH-BEFORE-WRAP",
        "Filter before wrapping the document under its alias",
        "every field the $match reads is under the alias that $replaceRoot wraps $$ROOT in, and "
        "it reads "
        "no whole document; the alias prefix is removed, which selects the same documents",
        match_before_root_wrap,
    ),
    RewriteRule(
        "AGG-MATCH-PUSHDOWN",
        "Push $match before $lookup / $unwind / $addFields / $sort",
        "the $match reads no path the earlier stage writes (output field of the $lookup, "
        "unwound array, "
        "added field or its ancestors) and no whole document: per-document stages commute with a "
        "per-document filter, so null and missing semantics are untouched. Never moved past "
        "$group, $limit or $skip",
        match_before_stage,
    ),
    RewriteRule(
        "AGG-LOOKUP-ELIMINATE",
        "Remove a $lookup whose result is never used",
        "no later stage reads the joined field (an $unwind of it counts as a read, because it "
        "changes the "
        "number of rows) and no stage reads a whole document",
        remove_unused_lookup,
    ),
    RewriteRule(
        "AGG-PRUNE-PROJECT",
        "Project away unused fields before $group",
        "the $group reads only the kept paths and no whole document; paths with a kept ancestor "
        "are "
        "dropped so the projection never collides",
        prune_before_group,
    ),
    RewriteRule(
        "AGG-TOPK",
        "Move $limit before a 1:1 projection so $sort + $limit run as a top-k",
        "the stage between $sort and $limit is $project/$addFields/$set (one output per input), "
        "so it "
        "commutes with $limit",
        limit_before_projection,
    ),
)
