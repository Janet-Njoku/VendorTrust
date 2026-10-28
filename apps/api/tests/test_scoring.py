"""Scoring rules (docs/scoring.md): exact, deterministic, documented."""

from __future__ import annotations

import random
import uuid
from decimal import Decimal

import pytest

from app.questionnaire_starter import STARTER_QUESTIONS, starter_questions
from app.scoring import METHOD, Answer, ScoringError, Thresholds, calculate, classify

DEFAULTS = Thresholds()


def answer(weight: int, points: int | None, category: str = "access_control") -> Answer:
    return Answer(uuid.uuid4(), category, weight, points)


def test_best_answers_score_zero_and_worst_score_one_hundred() -> None:
    best = calculate([answer(w, 0) for w in (1, 5, 10)], DEFAULTS)
    worst = calculate([answer(w, 100) for w in (1, 5, 10)], DEFAULTS)
    assert (best.score, best.risk_level) == (Decimal("0.0"), "low")
    assert (worst.score, worst.risk_level) == (Decimal("100.0"), "critical")


def test_weights_pull_the_score_toward_the_heavier_question() -> None:
    # (3 x 100 + 1 x 0) / 4 = 75.0
    result = calculate([answer(3, 100), answer(1, 0)], DEFAULTS)
    assert result.score == Decimal("75.0")
    # the same answers with the weights swapped: (1 x 100 + 3 x 0) / 4 = 25.0
    assert calculate([answer(1, 100), answer(3, 0)], DEFAULTS).score == Decimal("25.0")


def test_a_worked_example_from_the_documentation() -> None:
    answers = [answer(5, 0), answer(4, 50), answer(3, 100), answer(2, 25)]
    # (5x0 + 4x50 + 3x100 + 2x25) / 14 = 550 / 14 = 39.2857 -> 39.3, medium
    result = calculate(answers, DEFAULTS)
    assert result.score == Decimal("39.3")
    assert result.risk_level == "medium"
    assert result.applicable_weight == 14


def test_not_applicable_questions_are_left_out_entirely() -> None:
    with_na = calculate([answer(5, 0), answer(5, None), answer(5, 100)], DEFAULTS)
    without = calculate([answer(5, 0), answer(5, 100)], DEFAULTS)
    assert with_na.score == without.score == Decimal("50.0")
    assert with_na.applicable_weight == 10
    assert len(with_na.not_applicable) == 1


def test_all_not_applicable_cannot_be_scored() -> None:
    with pytest.raises(ScoringError):
        calculate([answer(5, None), answer(2, None)], DEFAULTS)
    with pytest.raises(ScoringError):
        calculate([], DEFAULTS)


def test_rounding_is_half_up_not_bankers() -> None:
    # 100 / 16 = 6.25 exactly: half-up gives 6.3 (banker's rounding would give 6.2)
    answers = [answer(1, 100)] + [answer(1, 0) for _ in range(15)]
    assert calculate(answers, DEFAULTS).score == Decimal("6.3")
    # 100 x 3 / 8 = 37.5 sits on one decimal already; 100 x 5 / 16 = 31.25 -> 31.3
    answers = [answer(1, 100) for _ in range(5)] + [answer(1, 0) for _ in range(11)]
    assert calculate(answers, DEFAULTS).score == Decimal("31.3")


@pytest.mark.parametrize(
    ("score", "level"),
    [
        ("0.0", "low"),
        ("24.9", "low"),
        ("25.0", "medium"),
        ("49.9", "medium"),
        ("50.0", "high"),
        ("74.9", "high"),
        ("75.0", "critical"),
        ("100.0", "critical"),
    ],
)
def test_classification_boundaries_are_lower_inclusive(score: str, level: str) -> None:
    assert classify(Decimal(score), DEFAULTS) == level


def test_classification_uses_the_rounded_score() -> None:
    # raw 24.96 (99 answers of 25 and one of 21, all weight 10) rounds to 25.0: medium, not low
    answers = [answer(10, 25) for _ in range(99)] + [answer(10, 21)]
    result = calculate(answers, DEFAULTS)
    assert result.score == Decimal("25.0")
    assert result.risk_level == "medium"
    # raw 24.94 (99 answers of 25 and one of 19) rounds down to 24.9: low
    below = [answer(10, 25) for _ in range(99)] + [answer(10, 19)]
    assert calculate(below, DEFAULTS).score == Decimal("24.9")
    assert calculate(below, DEFAULTS).risk_level == "low"


def test_custom_thresholds_apply() -> None:
    strict = Thresholds(low_max=10, medium_max=20, high_max=30)
    result = calculate([answer(1, 100), answer(1, 0), answer(1, 0), answer(1, 0)], strict)
    assert result.score == Decimal("25.0")
    assert result.risk_level == "high"
    assert (
        calculate([answer(1, 100), answer(1, 0), answer(1, 0), answer(1, 0)], DEFAULTS).risk_level
        == "medium"
    )


@pytest.mark.parametrize(
    "bad", [(0, 50, 75), (25, 25, 75), (50, 25, 75), (25, 50, 101), (-1, 50, 75), (25, 75, 50)]
)
def test_invalid_thresholds_are_rejected(bad: tuple[int, int, int]) -> None:
    with pytest.raises(ValueError, match="thresholds"):
        Thresholds(*bad)


def test_category_scores_use_the_same_formula() -> None:
    result = calculate(
        [
            answer(4, 0, "mfa"),
            answer(1, 100, "mfa"),
            answer(2, 100, "encryption"),
            answer(2, None, "encryption"),
            answer(3, None, "governance"),
            answer(1, 50, "access_control"),
        ],
        DEFAULTS,
    )
    categories = result.categories
    assert categories["mfa"]["score"] == 20.0
    assert categories["encryption"]["score"] == 100.0
    assert categories["encryption"]["weight"] == 2
    assert categories["access_control"]["score"] == 50.0
    assert categories["governance"] == {"score": None, "weight": 0, "questions": 1}
    assert list(categories) == sorted(categories)


def test_the_result_does_not_depend_on_answer_order_and_is_repeatable() -> None:
    rng = random.Random(7)
    answers = [
        answer(rng.randint(1, 10), rng.choice([0, 25, 50, 75, 100, None]), rng.choice("abc"))
        for _ in range(40)
    ]
    first = calculate(answers, DEFAULTS)
    for _ in range(25):
        shuffled = answers[:]
        rng.shuffle(shuffled)
        again = calculate(shuffled, DEFAULTS)
        assert (again.score, again.risk_level) == (first.score, first.risk_level)
        assert again.categories == first.categories


def test_a_worse_answer_never_lowers_the_score() -> None:
    rng = random.Random(11)
    for _ in range(300):
        answers = [
            answer(rng.randint(1, 10), rng.choice([0, 10, 50, 90, 100, None])) for _ in range(12)
        ]
        if all(a.risk_points is None for a in answers):
            continue
        base = calculate(answers, DEFAULTS).score
        index = rng.randrange(len(answers))
        target = answers[index]
        if target.risk_points is None:
            continue
        worse = Answer(target.question_id, target.category, target.weight, 100)
        changed = answers[:index] + [worse] + answers[index + 1 :]
        assert calculate(changed, DEFAULTS).score >= base


def test_the_score_is_always_between_zero_and_one_hundred() -> None:
    rng = random.Random(3)
    for _ in range(300):
        answers = [
            answer(rng.randint(1, 10), rng.randint(0, 100)) for _ in range(rng.randint(1, 20))
        ]
        assert Decimal(0) <= calculate(answers, DEFAULTS).score <= Decimal(100)


def test_the_breakdown_records_everything_needed_to_rederive_the_score() -> None:
    answers = [answer(3, 100, "mfa"), answer(1, None, "mfa"), answer(2, 0, "encryption")]
    breakdown = calculate(answers, Thresholds(20, 40, 60)).breakdown()
    assert breakdown["method"] == METHOD
    assert breakdown["thresholds"] == {"low_max": 20, "medium_max": 40, "high_max": 60}
    assert breakdown["applicable_weight"] == 5
    assert [i["risk_points"] for i in breakdown["items"]] == [100, None, 0]
    assert breakdown["not_applicable"] == [str(answers[1].question_id)]
    assert breakdown["score"] == 60.0
    assert breakdown["risk_level"] == "critical"


# --- the starter questionnaire is data, and sound data ----------------------------------------


def test_the_starter_covers_every_required_area_with_sane_values() -> None:
    questions = starter_questions()
    assert 10 <= len(questions) <= 15
    assert {q["category"] for q in questions} == {
        "access_control",
        "mfa",
        "encryption",
        "vulnerability_management",
        "incident_response",
        "business_continuity",
        "data_protection",
        "vendor_governance",
    }
    for question in questions:
        assert 1 <= question["weight"] <= 10
        assert len(question["options"]) >= 2
        points = [o["risk_points"] for o in question["options"] if o["risk_points"] is not None]
        assert min(points) == 0
        assert max(points) == 100
    assert len({q["prompt"] for q in questions}) == len(questions)


def test_the_starter_scores_zero_for_the_best_answers_and_one_hundred_for_the_worst() -> None:
    def pick(choose: str) -> list[Answer]:
        out = []
        for category, _prompt, weight, options in STARTER_QUESTIONS:
            scored = [p for _label, p in options if p is not None]
            out.append(
                Answer(
                    uuid.uuid4(), category, weight, min(scored) if choose == "best" else max(scored)
                )
            )
        return out

    assert calculate(pick("best"), DEFAULTS).risk_level == "low"
    assert calculate(pick("best"), DEFAULTS).score == Decimal("0.0")
    assert calculate(pick("worst"), DEFAULTS).score == Decimal("100.0")
    assert calculate(pick("worst"), DEFAULTS).risk_level == "critical"
