"""Phase 19: every rewrite fires when its precondition holds and stays quiet when it does not."""

from __future__ import annotations

from pathlib import Path

import pytest

from schemashift.aggopt import REWRITES, estimate_cost, optimize_pipeline, recommend_indexes
from schemashift.aggopt.rewrites import (
    limit_before_projection,
    match_before_root_wrap,
    match_before_stage,
    merge_adjacent_matches,
    prune_before_group,
    remove_unused_lookup,
)
from schemashift.codegen import generate_code
from schemashift.pipeline import CompileOptions, compile_sql

WRAP = {"$replaceRoot": {"newRoot": {"c": "$$ROOT"}}}
LOOKUP = {"$lookup": {"from": "orders", "localField": "c._id", "foreignField": "cid", "as": "o"}}
UNWIND = {"$unwind": {"path": "$o", "preserveNullAndEmptyArrays": False}}


def test_every_rule_has_a_precondition_and_unique_id() -> None:
    assert len({r.id for r in REWRITES}) == len(REWRITES)
    assert all(r.precondition and r.title for r in REWRITES)


def test_merge_matches_flat_and_and() -> None:
    p = [{"$match": {"a": 1}}, {"$match": {"b": 2}}]
    assert merge_adjacent_matches(p) == [[{"$match": {"a": 1, "b": 2}}]]
    clash = [{"$match": {"a": 1}}, {"$match": {"a": 2}}]
    assert merge_adjacent_matches(clash) == [[{"$match": {"$and": [{"a": 1}, {"a": 2}]}}]]
    assert merge_adjacent_matches([{"$match": {"a": 1}}, {"$sort": {"a": 1}}]) == []


def test_match_moves_before_the_alias_wrap() -> None:
    p = [WRAP, {"$match": {"c.status": "x", "c.n": {"$gt": 1}}}]
    (out,) = match_before_root_wrap(p)
    assert out == [{"$match": {"status": "x", "n": {"$gt": 1}}}, WRAP]


def test_match_does_not_cross_the_wrap_when_it_reads_other_aliases_or_the_root() -> None:
    assert match_before_root_wrap([WRAP, {"$match": {"o.x": 1}}]) == []
    assert match_before_root_wrap([WRAP, {"$match": {"$expr": {"$eq": ["$$ROOT", 1]}}}]) == []


def test_match_pushes_before_lookup_and_unwind_but_not_on_the_joined_alias() -> None:
    free = [WRAP, LOOKUP, UNWIND, {"$match": {"c.k": 1}}]
    moved = match_before_stage(free)
    assert moved and moved[0].index({"$match": {"c.k": 1}}) == 2
    assert match_before_stage([WRAP, LOOKUP, UNWIND, {"$match": {"o.total": {"$gt": 1}}}]) == []
    # an ancestor of the written path counts as a dependency
    assert match_before_stage([LOOKUP, {"$match": {"o": {"$size": 1}}}]) == []


def test_match_is_never_pushed_past_group_limit_or_skip() -> None:
    for barrier in ({"$group": {"_id": "$a"}}, {"$limit": 5}, {"$skip": 1}):
        assert match_before_stage([barrier, {"$match": {"x": 1}}]) == []


def test_match_does_not_cross_an_added_field_it_reads() -> None:
    add = {"$addFields": {"n": {"$add": [1, 2]}}}
    assert match_before_stage([add, {"$match": {"n": 3}}]) == []
    assert match_before_stage([add, {"$match": {"k": 3}}])


def test_unused_lookup_is_removed_but_an_unwound_one_is_kept() -> None:
    used_later = [WRAP, LOOKUP, {"$project": {"c.name": 1}}]
    assert remove_unused_lookup(used_later) == [[WRAP, {"$project": {"c.name": 1}}]]
    assert remove_unused_lookup([WRAP, LOOKUP, UNWIND, {"$project": {"c.name": 1}}]) == []
    assert remove_unused_lookup([WRAP, LOOKUP, {"$project": {"o.total": 1}}]) == []
    assert remove_unused_lookup([LOOKUP, {"$replaceRoot": {"newRoot": "$$ROOT"}}]) == []


def test_prune_projects_only_what_the_group_reads_without_path_collisions() -> None:
    group = {"$group": {"_id": {"g0": "$c.name"}, "a": {"$sum": "$o.total"}, "b": {"$sum": "$o"}}}
    (out,) = prune_before_group([WRAP, LOOKUP, UNWIND, group])
    project = out[3]["$project"]
    assert project == {"c.name": 1, "o": 1, "_id": 0}  # "o" wins over "o.total"
    assert prune_before_group(out) == []  # idempotent
    assert prune_before_group([group]) == []
    assert prune_before_group([WRAP, {"$group": {"_id": None, "n": {"$sum": 1}}}]) == []


def test_limit_moves_ahead_of_a_one_to_one_projection_after_sort() -> None:
    p = [{"$sort": {"a": 1}}, {"$project": {"a": 1}}, {"$limit": 3}]
    assert limit_before_projection(p) == [[p[0], p[2], p[1]]]
    assert limit_before_projection([{"$sort": {"a": 1}}, {"$group": {"_id": "$a"}}, p[2]]) == []


def test_best_candidate_is_never_worse_than_the_original() -> None:
    p = [WRAP, {"$match": {"c.status": "x"}}, LOOKUP, UNWIND, {"$group": {"_id": "$c.n"}}]
    out = optimize_pipeline(p, "customers")
    assert out.best.cost.total <= out.baseline.total
    assert out.applied
    assert optimize_pipeline(p, "customers").model_dump() == out.model_dump()  # deterministic


def test_filter_before_wrap_enables_the_index_and_lowers_the_cost() -> None:
    from schemashift.aggopt import Stats

    p = [WRAP, {"$match": {"c.status": "x"}}]
    stats = Stats(indexed={"customers": {"status"}})
    assert (
        optimize_pipeline(p, "customers", stats).best.cost.total
        < estimate_cost(p, "customers", stats).total
    )


def test_index_recommendations_from_match_and_lookup() -> None:
    p = [{"$match": {"status": "x"}}, LOOKUP]
    assert recommend_indexes(p, "customers") == ("customers(status)", "orders(cid)")


@pytest.mark.parametrize("sample", ["ecommerce", "blog", "banking", "university"])
def test_samples_keep_the_unoptimized_pipeline_for_comparison(sample: str) -> None:
    d = Path(__file__).resolve().parents[4] / "samples" / sample
    r = compile_sql((d / "schema.sql").read_text("utf-8"), (d / "queries.sql").read_text("utf-8"))
    on = generate_code(r, CompileOptions()).queries
    off = generate_code(r, CompileOptions(optimize_pipelines=False)).queries
    changed = 0
    for a, b in zip(on, off, strict=True):
        if a.rewrites:
            changed += 1
            assert a.original_pipeline == b.pipeline
            assert a.cost_after is not None and a.cost_before is not None
            assert a.cost_after < a.cost_before
        else:
            assert a.pipeline == b.pipeline
    if sample == "banking":
        assert changed >= 1  # the filter on a wrapped alias moves in front of the wrap


def test_spearman_and_observed_cost_helpers() -> None:
    from schemashift.verification.explain import observed_cost, spearman

    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == 1.0
    assert spearman([1, 2, 3, 4], [4, 3, 2, 1]) == -1.0
    assert spearman([1, 1, 1], [1, 2, 3]) is None
    assert spearman([1, 2], [1, 2]) is None
    plan = {
        "stages": [
            {"$cursor": {"executionStats": {"totalDocsExamined": 10, "totalKeysExamined": 20}}},
            {"$group": {}, "nReturned": 3},
            {"$project": {}, "nReturned": 3},
        ]
    }
    assert observed_cost(plan) == (10 + 2.0 + 6, 10)
