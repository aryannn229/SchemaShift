"""Advisory AI layer: a second opinion on embed-vs-reference. The optimizer always decides."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any

from schemashift.ai.anthropic_advisor import DEFAULT_MODEL, AnthropicAdvisor
from schemashift.ai.base import AdvisorError, LLMAdvisor
from schemashift.ai.cache import AdvisorCache, InMemoryCache, cache_key
from schemashift.ai.mock_advisor import MockAdvisor
from schemashift.ai.prompts import PROMPT_VERSION, build_prompt, schema_excerpt
from schemashift.models.placement import AIOpinion, AISuggestion, PlacementDecision, PlacementPlan
from schemashift.models.schema import Schema
from schemashift.optimizer.features import RelationshipFeatures

MAX_CONCURRENCY = 5
CALL_TIMEOUT_SECONDS = 15.0


def make_advisor(api_key: str = "", model: str = DEFAULT_MODEL) -> LLMAdvisor:
    """Real advisor when a key is configured, otherwise the deterministic mock."""
    return AnthropicAdvisor(api_key=api_key, model=model) if api_key else MockAdvisor()


def _opinion_for(
    advisor: LLMAdvisor,
    schema: Schema,
    rel: RelationshipFeatures,
    cache: AdvisorCache | None,
    timeout: float,
) -> AIOpinion:
    prompt = build_prompt(rel, schema_excerpt(schema, rel))
    key = cache_key(advisor.model, prompt)
    if cache is not None and (hit := cache.get(key)) is not None:
        return AIOpinion.model_validate(hit)
    try:
        suggestion = advisor.suggest_placement(rel, schema_excerpt(schema, rel))
        opinion = AIOpinion(source=advisor.name, available=advisor.available, suggestion=suggestion)
    except AdvisorError as exc:
        return AIOpinion(source="unavailable", available=False, error=str(exc))
    except Exception as exc:  # noqa: BLE001 (an advisor bug must never break a compile)
        return AIOpinion(
            source="unavailable", available=False, error=f"{type(exc).__name__}: {exc}"
        )
    if cache is not None:
        cache.put(key, opinion.model_dump(mode="json"))
    return opinion


def attach_opinions(
    plan: PlacementPlan,
    features: dict[str, RelationshipFeatures],
    schema: Schema,
    advisor: LLMAdvisor,
    cache: AdvisorCache | None = None,
    max_workers: int = MAX_CONCURRENCY,
    timeout: float = CALL_TIMEOUT_SECONDS,
) -> PlacementPlan:
    """Ask the advisor about every relationship (bounded concurrency, 15 s each) and record
    both opinions; ``agree`` compares EMBED vs not-EMBED. The optimizer's decision is unchanged."""
    todo = {
        rid: f for rid, f in features.items() if f.cardinality != "self" and rid in plan.decisions
    }
    opinions: dict[str, AIOpinion] = {}
    pool = ThreadPoolExecutor(max_workers=max(1, min(max_workers, MAX_CONCURRENCY)))
    try:
        futures = {
            rid: pool.submit(_opinion_for, advisor, schema, f, cache, timeout)
            for rid, f in todo.items()
        }
        for rid, fut in futures.items():
            try:
                opinions[rid] = fut.result(timeout=timeout + 5)
            except FutureTimeout:
                opinions[rid] = AIOpinion(source="unavailable", available=False, error="timed out")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    decisions: dict[str, PlacementDecision] = dict(plan.decisions)
    for rid, opinion in opinions.items():
        base = decisions[rid]
        agree: bool | None = None
        if opinion.available and opinion.suggestion is not None:
            agree = (opinion.suggestion.decision == "EMBED") == (base.decision == "EMBED")
        decisions[rid] = base.model_copy(update={"ai": opinion, "agree": agree})
    return plan.model_copy(update={"decisions": decisions})


def agreement_rate(plan: PlacementPlan) -> float | None:
    """AI agreement over placements with an available AI opinion (None when there are none)."""
    judged = [d.agree for d in plan.decisions.values() if d.agree is not None]
    return sum(judged) / len(judged) if judged else None


__all__: list[Any] = [
    "AIOpinion",
    "AISuggestion",
    "AdvisorCache",
    "AdvisorError",
    "AnthropicAdvisor",
    "InMemoryCache",
    "LLMAdvisor",
    "MockAdvisor",
    "PROMPT_VERSION",
    "agreement_rate",
    "attach_opinions",
    "make_advisor",
]
