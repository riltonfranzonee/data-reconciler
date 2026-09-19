import csv
import json
from pathlib import Path

import httpx

from ror_reconcile.evaluation.baselines import RorApiBaseline, evaluate_ror_api_baseline
from ror_reconcile.evaluation.experiments import (
    ablation_configs,
    run_ablation,
    run_threshold_sweep,
)
from ror_reconcile.evaluation.gold import GoldCase, load_gold, split_gold, write_gold
from ror_reconcile.evaluation.runner import (
    run_detailed_evaluation,
    run_evaluation,
    write_evaluation_outputs,
)


def test_evaluation_reports_core_metrics(matcher):
    cases = load_gold(Path(__file__).parent / "fixtures" / "gold_fixture.csv")
    summary = run_evaluation(matcher, cases).as_dict()

    assert summary["cases"] == 6
    assert summary["blocking_recall"] >= 0.8
    assert summary["selective_precision"] >= 0.8
    assert summary["coverage"] > 0
    assert summary["per_stratum"]["easy"]["cases"] == 3
    assert summary["per_stratum"]["no_match"]["correct_abstentions"] == 1


def test_detailed_evaluation_is_reproducible_and_auditable(matcher, tmp_path):
    cases = load_gold(Path(__file__).parent / "fixtures" / "gold_fixture.csv")

    first = run_detailed_evaluation(matcher, cases, bootstrap_iterations=200, seed=17)
    second = run_detailed_evaluation(matcher, cases, bootstrap_iterations=200, seed=17)
    report_path = tmp_path / "evaluation.json"
    cases_path = tmp_path / "cases.csv"
    write_evaluation_outputs(first, report_path, cases_path)

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    with cases_path.open(encoding="utf-8", newline="") as handle:
        outcomes = list(csv.DictReader(handle))

    assert first.confidence_intervals == second.confidence_intervals
    assert set(first.confidence_intervals) >= {
        "blocking_recall",
        "selective_precision",
        "coverage",
        "answerable_coverage",
    }
    assert payload["bootstrap"]["iterations"] == 200
    assert payload["error_analysis"]["counts"]["accepted_wrong"] == 0
    assert payload["benchmark"]["latency_scope"] == "match_one only"
    assert len(outcomes) == len(cases)
    assert outcomes[0]["query"] == "University College London"
    assert outcomes[0]["predicted_ror_id"] == "https://ror.org/02jx3x895"


def test_gold_split_is_seeded_disjoint_and_round_trippable(tmp_path):
    cases = load_gold(Path(__file__).parent / "fixtures" / "gold_fixture.csv")

    dev, test = split_gold(cases, dev_fraction=0.5, seed=29)
    repeated_dev, repeated_test = split_gold(cases, dev_fraction=0.5, seed=29)
    dev_path = tmp_path / "dev.csv"
    test_path = tmp_path / "test.csv"
    write_gold(dev, dev_path)
    write_gold(test, test_path)

    assert [case.identifier for case in dev] == [case.identifier for case in repeated_dev]
    assert [case.identifier for case in test] == [case.identifier for case in repeated_test]
    assert {case.identifier for case in dev}.isdisjoint({case.identifier for case in test})
    assert len(dev) + len(test) == len(cases)
    assert [case.identifier for case in load_gold(dev_path)] == [case.identifier for case in dev]


def test_gold_split_balances_match_status_within_each_stratum():
    cases = []
    for stratum, size in (("standard", 12), ("short_name", 8)):
        for index in range(size):
            expected = f"https://ror.org/{index:09d}" if index < size // 2 else None
            cases.append(
                GoldCase(
                    query=f"{stratum}-{index}",
                    expected_ror_id=expected,
                    stratum=stratum,
                )
            )

    dev, test = split_gold(cases, dev_fraction=0.5, seed=0)

    for stratum in ("standard", "short_name"):
        for has_known_match in (True, False):
            dev_count = sum(
                case.stratum == stratum and case.has_known_match == has_known_match for case in dev
            )
            test_count = sum(
                case.stratum == stratum and case.has_known_match == has_known_match for case in test
            )
            assert abs(dev_count - test_count) <= 1


def test_ablation_and_threshold_experiments_emit_comparable_metrics(fixture_store):
    cases = load_gold(Path(__file__).parent / "fixtures" / "gold_fixture.csv")

    configs = ablation_configs()
    ablation = run_ablation(fixture_store, cases)
    sweep = run_threshold_sweep(fixture_store, cases, thresholds=(0.84, 0.88, 0.92))

    assert list(configs) == [
        "normalized_exact",
        "lexical",
        "coverage",
        "properties",
        "margin_and_guard",
        "full_with_trigram",
    ]
    assert list(ablation) == list(configs)
    assert all("selective_precision" in result for result in ablation.values())
    assert [result["auto_match_threshold"] for result in sweep] == [0.84, 0.88, 0.92]
    assert all("coverage" in result for result in sweep)


def test_threshold_sweep_explores_lexical_and_final_score_gates(fixture_store):
    cases = load_gold(Path(__file__).parent / "fixtures" / "gold_fixture.csv")

    sweep = run_threshold_sweep(
        fixture_store,
        cases,
        thresholds=(0.86, 0.88),
        lexical_gates=(0.80, 0.82),
    )

    assert [(result["lexical_gate"], result["auto_match_threshold"]) for result in sweep] == [
        (0.80, 0.86),
        (0.80, 0.88),
        (0.82, 0.86),
        (0.82, 0.88),
    ]


def test_ror_api_baseline_is_cached_and_evaluated(tmp_path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "https://ror.org/02jx3x895",
                        "names": [{"value": "University College London", "types": ["ror_display"]}],
                    }
                ]
            },
        )

    case = load_gold(Path(__file__).parent / "fixtures" / "gold_fixture.csv")[0]
    cache = tmp_path / "ror-api-cache.json"
    with httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.test") as http:
        baseline = RorApiBaseline(cache_path=cache, http_client=http)
        first = baseline.search(case.query)
        second = baseline.search(case.query)
        metrics, evidence = evaluate_ror_api_baseline(baseline, [case])

    assert first == second
    assert len(calls) == 1
    assert metrics["top1_accuracy_known"] == 1.0
    assert evidence[0]["predicted_ror_id"] == "https://ror.org/02jx3x895"
    assert cache.exists()


def test_ror_api_baseline_retries_and_audits_server_errors(tmp_path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500, json={"error": "temporary failure"})

    case = GoldCase(query="Unavailable organisation", expected_ror_id=None)
    cache = tmp_path / "ror-api-cache.json"
    with httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.test") as http:
        baseline = RorApiBaseline(
            cache_path=cache,
            http_client=http,
            max_retries=2,
            backoff_seconds=0,
        )
        metrics, evidence = evaluate_ror_api_baseline(baseline, [case])

    assert len(calls) == 3
    assert metrics["lookup_errors"] == 1
    assert metrics["correct_abstentions"] == 0
    assert metrics["no_match_specificity"] == 0.0
    assert evidence[0]["lookup_error"].startswith("HTTP 500")
    assert evidence[0]["predicted_ror_id"] is None
