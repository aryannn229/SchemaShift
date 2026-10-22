"""Optimizer vs AI advisor agreement: raw rate, Cohen's kappa and the table of disagreements.

Both raters answer the same binary question per relationship: EMBED or REFERENCE. Kappa corrects the
raw agreement for the agreement expected by chance, which matters because most relationships are
REFERENCE. The mock advisor is deterministic and not a real signal: its numbers are reported, but
labelled as such and never counted as AI agreement.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from schemashift.ai.base import LLMAdvisor
from schemashift.models.base import FrozenModel
from schemashift.optimizer import extract_features
from schemashift.pipeline import CompileOptions, CompileResult, compile_sql


class Pair(FrozenModel):
    case: str
    relationship_id: str
    optimizer: str  # EMBED / REFERENCE (REF_ARRAY counts as REFERENCE)
    optimizer_reason: str
    optimizer_factors: tuple[str, ...]
    advisor: str
    advisor_confidence: float
    advisor_justification: str

    @property
    def agree(self) -> bool:
        return self.optimizer == self.advisor


class AgreementReport(FrozenModel):
    advisor: str
    real_model: bool
    pairs: int
    agreed: int
    rate: float | None
    kappa: float | None
    disagreements: tuple[Pair, ...]


def cohens_kappa(a: Sequence[bool], b: Sequence[bool]) -> float | None:
    """Cohen's kappa for two raters over a binary label (None when undefined)."""
    n = len(a)
    if n == 0 or n != len(b):
        return None
    observed = sum(x == y for x, y in zip(a, b, strict=True)) / n
    pa, pb = sum(a) / n, sum(b) / n
    expected = pa * pb + (1 - pa) * (1 - pb)
    if expected == 1:
        return None  # both raters always give the same answer: kappa is undefined
    return (observed - expected) / (1 - expected)


def _binary(decision: str) -> str:
    return "EMBED" if decision == "EMBED" else "REFERENCE"


def collect_pairs(
    cases: list[tuple[str, str, str, CompileOptions]], advisor: LLMAdvisor
) -> list[Pair]:
    """(name, schema_sql, queries_sql, options) -> one Pair per relationship the advisor judged."""
    pairs: list[Pair] = []
    for name, schema, queries, options in cases:
        result: CompileResult = compile_sql(schema, queries, "", options)
        feats = extract_features(
            result.graph, result.ir, result.queries, result.seed_queries, options.access_hints
        )
        for rid, decision in result.plan.decisions.items():
            f = feats.get(rid)
            if f is None or f.cardinality == "self":
                continue
            try:
                s = advisor.suggest_placement(f, "")
            except Exception:  # an unavailable advisor must not abort the evaluation
                continue
            pairs.append(
                Pair(
                    case=name,
                    relationship_id=rid,
                    optimizer=_binary(decision.decision),
                    optimizer_reason=decision.reason,
                    optimizer_factors=tuple(
                        f"{x.name} {x.contribution:+.2f}" for x in decision.top_factors[:3]
                    ),
                    advisor=_binary(s.decision),
                    advisor_confidence=s.confidence,
                    advisor_justification=s.justification,
                )
            )
    return pairs


def summarize(pairs: list[Pair], advisor_name: str, real_model: bool) -> AgreementReport:
    opt = [p.optimizer == "EMBED" for p in pairs]
    adv = [p.advisor == "EMBED" for p in pairs]
    agreed = sum(p.agree for p in pairs)
    return AgreementReport(
        advisor=advisor_name,
        real_model=real_model,
        pairs=len(pairs),
        agreed=agreed,
        rate=agreed / len(pairs) if pairs else None,
        kappa=cohens_kappa(opt, adv),
        disagreements=tuple(p for p in pairs if not p.agree),
    )


def to_json(r: AgreementReport) -> dict[str, Any]:
    return {
        "advisor": r.advisor,
        "real_model": r.real_model,
        "pairs": r.pairs,
        "agreed": r.agreed,
        "rate": r.rate,
        "kappa": r.kappa,
        "disagreements": [p.model_dump() for p in r.disagreements],
    }
