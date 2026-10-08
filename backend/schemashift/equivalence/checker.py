"""Runs the rules over an IR program and builds the equivalence reports."""

from __future__ import annotations

from typing import Literal

import schemashift.equivalence.rules  # noqa: F401  (registers every rule)
from schemashift.equivalence.context import RuleContext, RuleOptions
from schemashift.equivalence.registry import get_rule
from schemashift.ir.nodes import IRProgram
from schemashift.models.base import FrozenModel
from schemashift.models.placement import PlacementPlan
from schemashift.models.verdict import Status, Verdict, worst
from schemashift.semantic.graph import SchemaGraph


class EquivalenceReport(FrozenModel):
    pass_name: Literal["initial", "final"]
    verdicts: tuple[Verdict, ...]
    counts: dict[Status, int]
    overall: Status

    def verdict_for(self, node_id: str) -> Verdict:
        for v in self.verdicts:
            if v.node_id == node_id:
                return v
        raise KeyError(node_id)


class PlacementChange(FrozenModel):
    """A verdict whose status changed between the initial and final pass."""

    node_id: str
    rule_id: str
    initial: Status
    final: Status


class EquivalenceResult(FrozenModel):
    initial: EquivalenceReport
    final: EquivalenceReport
    changed_by_placement: tuple[PlacementChange, ...]


def check_program(
    program: IRProgram,
    graph: SchemaGraph,
    plan: PlacementPlan | None = None,
    options: RuleOptions | None = None,
    pass_name: Literal["initial", "final"] = "final",
) -> EquivalenceReport:
    """Evaluate every guarantee node that has a rule."""
    ctx = RuleContext(graph=graph, plan=plan or PlacementPlan(), options=options or RuleOptions())
    verdicts: list[Verdict] = []
    for node in program.nodes:
        spec = get_rule(node)
        if spec is not None:
            verdicts.append(spec.evaluate(node, ctx))
    counts: dict[Status, int] = {"SAFE": 0, "CHANGED": 0, "BROKEN": 0}
    for v in verdicts:
        counts[v.status] += 1
    return EquivalenceReport(
        pass_name=pass_name,
        verdicts=tuple(verdicts),
        counts=counts,
        overall=worst([v.status for v in verdicts]),
    )


def check_both(
    program: IRProgram,
    graph: SchemaGraph,
    plan: PlacementPlan,
    options: RuleOptions | None = None,
) -> EquivalenceResult:
    """Initial pass (everything by reference) then the final pass with the placement plan."""
    initial = check_program(program, graph, PlacementPlan(), options, "initial")
    final = check_program(program, graph, plan, options, "final")
    before = {v.node_id: v for v in initial.verdicts}
    changes = tuple(
        PlacementChange(
            node_id=v.node_id, rule_id=v.rule_id, initial=before[v.node_id].status, final=v.status
        )
        for v in final.verdicts
        if v.node_id in before and before[v.node_id].status != v.status
    )
    return EquivalenceResult(initial=initial, final=final, changed_by_placement=changes)
