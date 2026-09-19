from __future__ import annotations

import csv
import json
import platform
import random
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ror_reconcile.evaluation.gold import GoldCase
from ror_reconcile.evaluation.metrics import (
    CaseEvaluation,
    EvaluationSummary,
    evaluate_case,
    summarize_outcomes,
)
from ror_reconcile.matcher import RorMatcher

_BOOTSTRAP_METRICS = (
    "blocking_recall",
    "top1_accuracy_known",
    "selective_precision",
    "coverage",
    "answerable_coverage",
    "end_to_end_recall",
    "no_match_specificity",
)


@dataclass(frozen=True)
class EvaluationReport:
    summary: EvaluationSummary
    outcomes: tuple[CaseEvaluation, ...]
    confidence_intervals: dict[str, dict[str, float]]
    bootstrap_iterations: int
    seed: int
    confidence: float
    configuration: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary.as_dict(),
            "confidence_intervals": self.confidence_intervals,
            "bootstrap": {
                "method": "stratified non-parametric case bootstrap",
                "iterations": self.bootstrap_iterations,
                "seed": self.seed,
                "confidence": self.confidence,
            },
            "error_analysis": error_analysis(self.outcomes),
            "configuration": self.configuration,
            "benchmark": {
                "clock": "time.perf_counter",
                "latency_scope": "match_one only",
                "measurements_per_case": 1,
                "case_order": "gold-file order",
                "python": sys.version.split()[0],
                "platform": platform.platform(),
            },
        }


def run_evaluation(matcher: RorMatcher, cases: list[GoldCase]) -> EvaluationSummary:
    return run_detailed_evaluation(matcher, cases, bootstrap_iterations=0).summary


def run_detailed_evaluation(
    matcher: RorMatcher,
    cases: list[GoldCase],
    *,
    bootstrap_iterations: int = 2000,
    seed: int = 42,
    confidence: float = 0.95,
) -> EvaluationReport:
    outcomes = [evaluate_case(matcher, case) for case in cases]
    summary = summarize_outcomes(outcomes)
    intervals = (
        bootstrap_confidence_intervals(
            outcomes,
            iterations=bootstrap_iterations,
            seed=seed,
            confidence=confidence,
        )
        if bootstrap_iterations
        else {}
    )
    return EvaluationReport(
        summary=summary,
        outcomes=tuple(outcomes),
        confidence_intervals=intervals,
        bootstrap_iterations=bootstrap_iterations,
        seed=seed,
        confidence=confidence,
        configuration=asdict(matcher.config),
    )


def error_analysis(outcomes: tuple[CaseEvaluation, ...]) -> dict[str, Any]:
    categories = (
        "correct_answer",
        "correct_abstention",
        "accepted_wrong",
        "blocking_miss",
        "ranking_error",
        "threshold_or_margin_abstention",
    )
    cases_by_category: dict[str, list[str]] = {category: [] for category in categories}
    for outcome in outcomes:
        if outcome.correct_answered:
            category = "correct_answer"
        elif outcome.correct_abstention:
            category = "correct_abstention"
        elif outcome.answered:
            category = "accepted_wrong"
        elif not outcome.blocking_hit:
            category = "blocking_miss"
        elif not outcome.top1_hit:
            category = "ranking_error"
        else:
            category = "threshold_or_margin_abstention"
        cases_by_category[category].append(outcome.case_id)
    return {
        "counts": {category: len(case_ids) for category, case_ids in cases_by_category.items()},
        "case_ids": cases_by_category,
    }


def bootstrap_confidence_intervals(
    outcomes: list[CaseEvaluation],
    *,
    iterations: int,
    seed: int,
    confidence: float,
) -> dict[str, dict[str, float]]:
    if iterations <= 0:
        raise ValueError("iterations must be positive")
    if not 0 < confidence < 1:
        raise ValueError("confidence must be between zero and one")
    if not outcomes:
        return {}

    strata: dict[str, list[CaseEvaluation]] = {}
    for outcome in outcomes:
        strata.setdefault(outcome.stratum or "__unstratified__", []).append(outcome)
    samples: dict[str, list[float]] = {metric: [] for metric in _BOOTSTRAP_METRICS}
    randomizer = random.Random(seed)

    for _ in range(iterations):
        resampled: list[CaseEvaluation] = []
        for stratum in strata.values():
            resampled.extend(randomizer.choice(stratum) for _ in range(len(stratum)))
        metrics = summarize_outcomes(resampled, include_strata=False).as_dict()
        for metric in _BOOTSTRAP_METRICS:
            samples[metric].append(float(metrics[metric]))

    alpha = (1.0 - confidence) / 2.0
    return {
        metric: {
            "lower": _percentile(values, alpha),
            "upper": _percentile(values, 1.0 - alpha),
            "confidence": confidence,
        }
        for metric, values in samples.items()
    }


def write_evaluation_outputs(
    report: EvaluationReport,
    report_path: Path | str,
    cases_path: Path | str,
) -> None:
    report_output = Path(report_path)
    cases_output = Path(cases_path)
    report_output.parent.mkdir(parents=True, exist_ok=True)
    cases_output.parent.mkdir(parents=True, exist_ok=True)
    report_output.write_text(
        json.dumps(report.as_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    rows = [outcome.as_dict() for outcome in report.outcomes]
    with cases_output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else [])
        if rows:
            writer.writeheader()
            writer.writerows(rows)


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = quantile * (len(ordered) - 1)
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(ordered) - 1)
    fraction = position - lower_index
    value = ordered[lower_index] + (ordered[upper_index] - ordered[lower_index]) * fraction
    return round(value, 4)
