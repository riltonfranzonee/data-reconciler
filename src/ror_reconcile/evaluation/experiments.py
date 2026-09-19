from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import asdict, replace

from ror_reconcile.config import MatchConfig
from ror_reconcile.evaluation.gold import GoldCase
from ror_reconcile.evaluation.runner import run_evaluation
from ror_reconcile.matcher import RorMatcher
from ror_reconcile.store import RorStore


def ablation_configs(
    base: MatchConfig | None = None,
) -> OrderedDict[str, MatchConfig]:
    full = base or MatchConfig()
    exact = replace(
        full,
        use_fts=False,
        use_trigram=False,
        use_coverage=False,
        use_country=False,
        use_city=False,
        use_margin=False,
        require_multi_token_fuzzy_signal=False,
    )
    lexical = replace(exact, use_fts=True)
    coverage = replace(lexical, use_coverage=True)
    properties = replace(coverage, use_country=True, use_city=True)
    margin_and_guard = replace(
        properties,
        use_margin=True,
        require_multi_token_fuzzy_signal=True,
    )
    return OrderedDict(
        (
            ("normalized_exact", exact),
            ("lexical", lexical),
            ("coverage", coverage),
            ("properties", properties),
            ("margin_and_guard", margin_and_guard),
            ("full_with_trigram", full),
        )
    )


def run_ablation(
    store: RorStore,
    cases: list[GoldCase],
    *,
    base: MatchConfig | None = None,
) -> OrderedDict[str, dict[str, object]]:
    results: OrderedDict[str, dict[str, object]] = OrderedDict()
    for name, config in ablation_configs(base).items():
        payload = run_evaluation(RorMatcher(store, config), cases).as_dict()
        payload["configuration"] = asdict(config)
        results[name] = payload
    return results


def run_threshold_sweep(
    store: RorStore,
    cases: list[GoldCase],
    *,
    thresholds: Iterable[float],
    lexical_gates: Iterable[float] | None = None,
    base: MatchConfig | None = None,
) -> list[dict[str, object]]:
    starting_config = base or MatchConfig()
    results: list[dict[str, object]] = []
    gates = lexical_gates or (starting_config.lexical_gate,)
    for lexical_gate in gates:
        for threshold in thresholds:
            config = replace(
                starting_config,
                lexical_gate=float(lexical_gate),
                auto_match_threshold=float(threshold),
            )
            payload = run_evaluation(RorMatcher(store, config), cases).as_dict()
            payload["lexical_gate"] = float(lexical_gate)
            payload["auto_match_threshold"] = float(threshold)
            payload["min_margin"] = config.min_margin
            results.append(payload)
    return results
