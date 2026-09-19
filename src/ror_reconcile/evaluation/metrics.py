from __future__ import annotations

import time
from dataclasses import dataclass, field
from statistics import median
from typing import Any

from ror_reconcile.evaluation.gold import GoldCase
from ror_reconcile.matcher import RorMatcher


@dataclass(frozen=True)
class EvaluationSummary:
    cases: int
    known_match_cases: int
    answered: int
    answered_known: int
    correct_answered: int
    correct_abstentions: int
    blocking_hits: int
    top1_hits: int
    median_latency_ms: float
    per_stratum: dict[str, EvaluationSummary] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        known = self.known_match_cases
        payload: dict[str, object] = {
            "cases": self.cases,
            "known_match_cases": known,
            "blocking_recall": _ratio(self.blocking_hits, known),
            "top1_accuracy_known": _ratio(self.top1_hits, known),
            "selective_precision": _ratio(self.correct_answered, self.answered),
            "coverage": _ratio(self.answered, self.cases),
            "answerable_coverage": _ratio(self.answered_known, known),
            "end_to_end_recall": _ratio(self.correct_answered, known),
            "correct_abstentions": self.correct_abstentions,
            "no_match_specificity": _ratio(
                self.correct_abstentions, self.cases - self.known_match_cases
            ),
            "median_latency_ms": round(self.median_latency_ms, 3),
        }
        if self.per_stratum:
            payload["per_stratum"] = {
                name: {
                    key: value
                    for key, value in summary.as_dict().items()
                    if key != "median_latency_ms"
                }
                for name, summary in self.per_stratum.items()
            }
        return payload


@dataclass(frozen=True)
class CaseEvaluation:
    case_id: str
    query: str
    country: str | None
    city: str | None
    stratum: str | None
    expected_ror_id: str | None
    predicted_ror_id: str | None
    predicted_name: str | None
    known: bool
    blocking_hit: bool
    top1_hit: bool
    answered: bool
    correct_answered: bool
    correct_abstention: bool
    score: float | None
    name_score: float | None
    margin: float | None
    latency_ms: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "query": self.query,
            "country": self.country,
            "city": self.city,
            "stratum": self.stratum,
            "expected_ror_id": self.expected_ror_id,
            "predicted_ror_id": self.predicted_ror_id,
            "predicted_name": self.predicted_name,
            "known": self.known,
            "blocking_hit": self.blocking_hit,
            "top1_hit": self.top1_hit,
            "answered": self.answered,
            "correct_answered": self.correct_answered,
            "correct_abstention": self.correct_abstention,
            "score": self.score,
            "name_score": self.name_score,
            "margin": self.margin,
            "latency_ms": round(self.latency_ms, 3),
        }


def evaluate_cases(
    matcher: RorMatcher,
    cases: list[GoldCase],
    *,
    latencies_ms: list[float] | None = None,
) -> EvaluationSummary:
    outcomes = [
        evaluate_case(
            matcher,
            case,
            latency_ms=latencies_ms[index] if latencies_ms else None,
        )
        for index, case in enumerate(cases)
    ]
    return summarize_outcomes(outcomes)


def evaluate_case(
    matcher: RorMatcher,
    case: GoldCase,
    *,
    latency_ms: float | None = None,
) -> CaseEvaluation:
    match_query = case.to_match_query()
    started = time.perf_counter()
    result = matcher.match_one(match_query)
    measured_latency = (time.perf_counter() - started) * 1000
    candidates = result.candidates
    top = candidates[0] if candidates else None
    chosen = top if top and top.match else None
    expected = case.expected_ror_id
    known = bool(expected)
    blocking_hit = bool(expected and expected in matcher.blocking_candidate_ids(match_query))

    return CaseEvaluation(
        case_id=case.identifier,
        query=case.query,
        country=case.country,
        city=case.city,
        stratum=case.stratum,
        expected_ror_id=expected,
        predicted_ror_id=chosen.org.ror_id if chosen else None,
        predicted_name=chosen.org.name if chosen else None,
        known=known,
        blocking_hit=blocking_hit,
        top1_hit=bool(expected and top and top.org.ror_id == expected),
        answered=chosen is not None,
        correct_answered=bool(expected and chosen and chosen.org.ror_id == expected),
        correct_abstention=not known and chosen is None,
        score=top.score if top else None,
        name_score=top.name_score if top else None,
        margin=top.margin if top else None,
        latency_ms=measured_latency if latency_ms is None else latency_ms,
    )


def summarize_outcomes(
    outcomes: list[CaseEvaluation],
    *,
    include_strata: bool = True,
) -> EvaluationSummary:
    per_stratum: dict[str, EvaluationSummary] = {}
    if include_strata:
        for name in dict.fromkeys(outcome.stratum for outcome in outcomes if outcome.stratum):
            subset = [outcome for outcome in outcomes if outcome.stratum == name]
            per_stratum[name] = summarize_outcomes(subset, include_strata=False)

    return EvaluationSummary(
        cases=len(outcomes),
        known_match_cases=sum(outcome.known for outcome in outcomes),
        answered=sum(outcome.answered for outcome in outcomes),
        answered_known=sum(outcome.answered and outcome.known for outcome in outcomes),
        correct_answered=sum(outcome.correct_answered for outcome in outcomes),
        correct_abstentions=sum(outcome.correct_abstention for outcome in outcomes),
        blocking_hits=sum(outcome.blocking_hit for outcome in outcomes),
        top1_hits=sum(outcome.top1_hit for outcome in outcomes),
        median_latency_ms=median([outcome.latency_ms for outcome in outcomes]) if outcomes else 0.0,
        per_stratum=per_stratum,
    )


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 4)
