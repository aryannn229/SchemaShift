"""Result types of the risk score."""

from __future__ import annotations

from typing import Literal

from schemashift.models.base import FrozenModel

Level = Literal["low", "moderate", "high", "severe"]


class RiskFinding(FrozenModel):
    rule_id: str
    category: str
    title: str
    points: float
    detail: str
    links: tuple[str, ...] = ()  # verdict node ids, relationship ids or query ids behind the points


class RiskCategory(FrozenModel):
    id: str
    label: str
    weight: float
    reference: float  # points that count as severe (score 100)
    points: float
    score: int  # 0-100: min(100, round(100 x points / reference))
    findings: tuple[RiskFinding, ...]


class RiskReport(FrozenModel):
    overall: int  # weighted mean of the category scores, rounded
    level: Level
    categories: tuple[RiskCategory, ...]
    formula: str
