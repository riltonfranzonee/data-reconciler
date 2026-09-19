"""Compute exact binomial confidence intervals from saved evaluation results."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path


def clopper_pearson_interval(
    successes: int,
    trials: int,
    *,
    confidence: float = 0.95,
) -> tuple[float, float]:
    """Return a two-sided Clopper-Pearson interval for a binomial proportion."""
    if isinstance(successes, bool) or isinstance(trials, bool):
        raise TypeError("successes and trials must be integers")
    if not isinstance(successes, int) or not isinstance(trials, int):
        raise TypeError("successes and trials must be integers")
    if trials <= 0:
        raise ValueError("trials must be positive")
    if not 0 <= successes <= trials:
        raise ValueError("successes must be between zero and trials")
    if not 0 < confidence < 1:
        raise ValueError("confidence must be between zero and one")

    tail = (1.0 - confidence) / 2.0
    lower = 0.0
    if successes:
        lower = _bisect_increasing_tail(successes, trials, tail)

    upper = 1.0
    if successes < trials:
        upper = _bisect_decreasing_cdf(successes, trials, tail)
    return lower, upper


def build_report(cases_path: Path, *, confidence: float = 0.95) -> dict[str, object]:
    with cases_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError("cases CSV is empty")

    answered = [row for row in rows if _as_bool(row["answered"])]
    correct_answered = [row for row in answered if _as_bool(row["correct_answered"])]
    no_match = [row for row in rows if not _as_bool(row["known"])]
    correct_abstentions = [row for row in no_match if _as_bool(row["correct_abstention"])]

    precision = _interval_payload(len(correct_answered), len(answered), confidence)
    specificity = _interval_payload(len(correct_abstentions), len(no_match), confidence)
    return {
        "source_cases": str(cases_path),
        "method": "two-sided Clopper-Pearson exact binomial interval",
        "confidence": confidence,
        "status": "post-hoc supplementary uncertainty analysis; frozen matcher output unchanged",
        "intervals": {
            "selective_precision": precision,
            "no_match_specificity": specificity,
        },
    }


def _interval_payload(successes: int, trials: int, confidence: float) -> dict[str, object]:
    lower, upper = clopper_pearson_interval(successes, trials, confidence=confidence)
    return {
        "successes": successes,
        "trials": trials,
        "estimate": successes / trials,
        "lower": lower,
        "upper": upper,
    }


def _as_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ValueError(f"expected boolean CSV value, got {value!r}")


def _binomial_cdf(successes: int, trials: int, probability: float) -> float:
    if probability <= 0:
        return 1.0
    if probability >= 1:
        return 1.0 if successes >= trials else 0.0
    terms = (
        math.comb(trials, observed)
        * probability**observed
        * (1.0 - probability) ** (trials - observed)
        for observed in range(successes + 1)
    )
    return math.fsum(terms)


def _bisect_increasing_tail(successes: int, trials: int, target: float) -> float:
    low, high = 0.0, 1.0
    for _ in range(100):
        midpoint = (low + high) / 2.0
        upper_tail = 1.0 - _binomial_cdf(successes - 1, trials, midpoint)
        if upper_tail < target:
            low = midpoint
        else:
            high = midpoint
    return (low + high) / 2.0


def _bisect_decreasing_cdf(successes: int, trials: int, target: float) -> float:
    low, high = 0.0, 1.0
    for _ in range(100):
        midpoint = (low + high) / 2.0
        cdf = _binomial_cdf(successes, trials, midpoint)
        if cdf > target:
            low = midpoint
        else:
            high = midpoint
    return (low + high) / 2.0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--confidence", type=float, default=0.95)
    args = parser.parse_args()

    report = build_report(args.cases, confidence=args.confidence)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
