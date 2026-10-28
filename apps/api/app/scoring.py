"""Deterministic vendor risk scoring (method `weighted_average_risk_v1`).

Rules, in full (also documented in docs/scoring.md):

1. Every question has an integer weight, 1 (minor) to 10 (critical control).
2. Every answer option has `risk_points`, 0 (best answer) to 100 (worst). An option with no
   risk points means "not applicable": the question is left out of the score entirely.
3. Overall score = sum(weight x risk_points) / sum(weight), over the applicable questions only,
   rounded half-up to one decimal. 0 is the lowest risk, 100 the highest.
4. The same formula over one category's questions gives that category's score.
5. Risk level from the ROUNDED score and the version's thresholds (frozen with the version):
   score < low_max -> low, < medium_max -> medium, < high_max -> high, otherwise critical.
   Defaults 25 / 50 / 75, so a score of exactly 25.0 is medium.
6. An assessment where every question is not applicable cannot be scored.

Exact `Decimal` arithmetic is used, so the same answers always give the same result, on any
machine. No randomness, no model, no hidden inputs.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

METHOD = "weighted_average_risk_v1"
ONE_DECIMAL = Decimal("0.1")


class ScoringError(Exception):
    """The answers cannot be scored (for example every question is not applicable)."""


@dataclass(frozen=True)
class Thresholds:
    low_max: int = 25
    medium_max: int = 50
    high_max: int = 75

    def __post_init__(self) -> None:
        if not 0 < self.low_max < self.medium_max < self.high_max <= 100:
            raise ValueError("thresholds must satisfy 0 < low < medium < high <= 100")


@dataclass(frozen=True)
class Answer:
    question_id: uuid.UUID
    category: str
    weight: int
    risk_points: int | None  # None = not applicable


@dataclass(frozen=True)
class ScoreResult:
    score: Decimal
    risk_level: str
    thresholds: Thresholds
    applicable_weight: int
    categories: dict[str, dict[str, Any]]
    not_applicable: list[uuid.UUID]
    items: list[Answer]

    def breakdown(self) -> dict[str, Any]:
        """The JSON stored with the assessment: everything needed to re-derive the score."""
        return {
            "method": METHOD,
            "score": float(self.score),
            "risk_level": self.risk_level,
            "thresholds": {
                "low_max": self.thresholds.low_max,
                "medium_max": self.thresholds.medium_max,
                "high_max": self.thresholds.high_max,
            },
            "applicable_weight": self.applicable_weight,
            "categories": self.categories,
            "not_applicable": [str(q) for q in self.not_applicable],
            "items": [
                {
                    "question_id": str(a.question_id),
                    "category": a.category,
                    "weight": a.weight,
                    "risk_points": a.risk_points,
                }
                for a in self.items
            ],
        }


def _average(weighted: int, weight: int) -> Decimal:
    return (Decimal(weighted) / Decimal(weight)).quantize(ONE_DECIMAL, rounding=ROUND_HALF_UP)


def classify(score: Decimal, thresholds: Thresholds) -> str:
    if score < thresholds.low_max:
        return "low"
    if score < thresholds.medium_max:
        return "medium"
    if score < thresholds.high_max:
        return "high"
    return "critical"


def calculate(answers: Sequence[Answer], thresholds: Thresholds) -> ScoreResult:
    applicable = [a for a in answers if a.risk_points is not None]
    weight = sum(a.weight for a in applicable)
    if not applicable or weight == 0:
        raise ScoringError("every question is not applicable, so there is nothing to score")
    score = _average(sum(a.weight * (a.risk_points or 0) for a in applicable), weight)

    categories: dict[str, dict[str, Any]] = {}
    for category in sorted({a.category for a in answers}):
        in_category = [a for a in applicable if a.category == category]
        category_weight = sum(a.weight for a in in_category)
        categories[category] = {
            "score": (
                float(
                    _average(
                        sum(a.weight * (a.risk_points or 0) for a in in_category), category_weight
                    )
                )
                if category_weight
                else None
            ),
            "weight": category_weight,
            "questions": sum(1 for a in answers if a.category == category),
        }
    return ScoreResult(
        score=score,
        risk_level=classify(score, thresholds),
        thresholds=thresholds,
        applicable_weight=weight,
        categories=categories,
        not_applicable=[a.question_id for a in answers if a.risk_points is None],
        items=list(answers),
    )
