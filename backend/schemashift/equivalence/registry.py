"""Rule registry. Rules are small functions; verdict text lives in declarative ``Outcome``s."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from schemashift.equivalence.context import RuleContext
from schemashift.ir.nodes import IRNode
from schemashift.models.verdict import ConditionTrace, Status, Verdict


@dataclass(frozen=True)
class Outcome:
    """One verdict branch a rule can produce.

    ``reason`` and ``mitigation`` are ``str.format`` templates; they may use ``{node}``
    attributes and the variables returned by the rule function.
    """

    key: str
    when: str  # human readable condition (used in the generated RULES.md)
    status: Status
    reason: str
    mitigation: str | None = None
    mongo_feature: str | None = None


class Trace:
    """Collects the conditions a rule checked, for the UI's "Why?" panel."""

    def __init__(self) -> None:
        self.items: list[ConditionTrace] = []

    def check(self, condition: str, result: bool, detail: str | None = None) -> bool:
        self.items.append(ConditionTrace(condition=condition, result=result, detail=detail))
        return result


RuleFn = Callable[[object, RuleContext, Trace], tuple[str, dict[str, str]]]


@dataclass(frozen=True)
class RuleSpec:
    rule_id: str
    node_type: type[IRNode]
    title: str
    target: str
    outcomes: tuple[Outcome, ...]
    fn: RuleFn = field(repr=False)
    notes: str = ""

    def outcome(self, key: str) -> Outcome:
        for o in self.outcomes:
            if o.key == key:
                return o
        raise KeyError(f"{self.rule_id}: unknown outcome {key!r}")

    def evaluate(self, node: IRNode, ctx: RuleContext) -> Verdict:
        trace = Trace()
        key, variables = self.fn(node, ctx, trace)
        outcome = self.outcome(key)
        fmt: dict[str, object] = {"node": node, **variables}
        return Verdict(
            node_id=node.id,
            rule_id=self.rule_id,
            ir_node_type=type(node).__name__,
            status=outcome.status,
            outcome=outcome.key,
            reason=outcome.reason.format_map(fmt),
            mitigation=outcome.mitigation.format_map(fmt) if outcome.mitigation else None,
            mongo_feature=outcome.mongo_feature,
            conditions_evaluated=tuple(trace.items),
            source_span=node.source_span,
        )


_REGISTRY: dict[tuple[str, type[IRNode]], RuleSpec] = {}


def rule(
    *,
    node_type: type[IRNode],
    rule_id: str,
    title: str,
    outcomes: tuple[Outcome, ...],
    target: str = "mongodb",
    notes: str = "",
) -> Callable[
    [Callable[..., tuple[str, dict[str, str]]]], Callable[..., tuple[str, dict[str, str]]]
]:
    """Register a rule for ``node_type`` against ``target`` (default MongoDB)."""

    def decorator(
        fn: Callable[..., tuple[str, dict[str, str]]],
    ) -> Callable[..., tuple[str, dict[str, str]]]:
        key = (target, node_type)
        if key in _REGISTRY:
            raise ValueError(f"duplicate rule for {target}/{node_type.__name__}")
        keys = [o.key for o in outcomes]
        if len(set(keys)) != len(keys):
            raise ValueError(f"{rule_id}: duplicate outcome keys")
        _REGISTRY[key] = RuleSpec(rule_id, node_type, title, target, outcomes, fn, notes)
        return fn

    return decorator


def get_rule(node: IRNode, target: str = "mongodb") -> RuleSpec | None:
    return _REGISTRY.get((target, type(node)))


def all_rules(target: str = "mongodb") -> list[RuleSpec]:
    return sorted((r for (t, _), r in _REGISTRY.items() if t == target), key=lambda r: r.rule_id)


def get_rule_by_id(rule_id: str, target: str = "mongodb") -> RuleSpec:
    for spec in all_rules(target):
        if spec.rule_id == rule_id:
            return spec
    raise KeyError(rule_id)
