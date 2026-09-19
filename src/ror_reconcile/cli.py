from __future__ import annotations

import argparse
import json
from pathlib import Path

import uvicorn

from ror_reconcile.config import DEFAULT_DB_PATH
from ror_reconcile.cordis import (
    reconcile_cordis_rows,
    write_cordis_outputs,
)
from ror_reconcile.evaluation.baselines import RorApiBaseline, evaluate_ror_api_baseline
from ror_reconcile.evaluation.experiments import run_ablation, run_threshold_sweep
from ror_reconcile.evaluation.gold import load_gold
from ror_reconcile.evaluation.runner import (
    run_detailed_evaluation,
    write_evaluation_outputs,
)
from ror_reconcile.ingest import (
    load_cordis_organizations,
    load_ror_records,
)
from ror_reconcile.matcher import RorMatcher
from ror_reconcile.openalex import (
    OpenAlexClient,
    enrich_reconciled_rows,
    load_reconciled_rows,
    write_enriched_rows,
)
from ror_reconcile.openrefine_api import create_app
from ror_reconcile.store import RorStore


def ingest_main() -> None:
    parser = argparse.ArgumentParser(description="Build a local index from a ROR dump.")
    parser.add_argument(
        "--ror-dump", type=Path, required=True, help="Path to a ROR JSON or ZIP dump."
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="SQLite output path.")
    args = parser.parse_args()

    records = load_ror_records(args.ror_dump)
    store = RorStore(args.db)
    try:
        count = store.replace_records(records)
    finally:
        store.close()
    print(json.dumps({"db": str(args.db), "organizations": count}, indent=2))


def _require_populated_db(parser: argparse.ArgumentParser, db_path: Path) -> None:
    # RorStore creates a missing file on open, which would silently score an
    # empty registry; refuse to serve or evaluate anything but a populated DB.
    if not db_path.exists():
        parser.error(f"database not found: {db_path}")
    store = RorStore(db_path)
    try:
        store.init_schema()
        if store.count_orgs() == 0:
            parser.error(f"database holds no organisations: {db_path}")
    finally:
        store.close()


def serve_main() -> None:
    parser = argparse.ArgumentParser(description="Serve the local ROR reconciler.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    _require_populated_db(parser, args.db)
    app = create_app(args.db)
    uvicorn.run(app, host=args.host, port=args.port)


def reconcile_main() -> None:
    parser = argparse.ArgumentParser(
        description="Reconcile CORDIS organisation rows and retain project-level linkage."
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--input", type=Path, required=True, help="CORDIS CSV or projects ZIP.")
    parser.add_argument("--output", type=Path, required=True, help="Reconciled CSV output.")
    parser.add_argument(
        "--evidence-output",
        type=Path,
        required=True,
        help="One JSON evidence record per unique CORDIS organisation.",
    )
    parser.add_argument("--limit", type=int, help="Optional source-row limit for development.")
    args = parser.parse_args()

    _require_populated_db(parser, args.db)
    rows = load_cordis_organizations(args.input, limit=args.limit)
    store = RorStore(args.db)
    try:
        store.init_schema()
        run = reconcile_cordis_rows(RorMatcher(store), rows)
    finally:
        store.close()
    write_cordis_outputs(run, args.output, args.evidence_output)
    print(
        json.dumps(
            {
                "source_rows": len(rows),
                "unique_organizations": run.unique_organizations,
                "matched_organizations": run.matched_organizations,
                "abstained_organizations": run.abstained_organizations,
                "output": str(args.output),
                "evidence_output": str(args.evidence_output),
            },
            indent=2,
        )
    )


def enrich_openalex_main() -> None:
    parser = argparse.ArgumentParser(
        description="Join reconciled CORDIS rows to OpenAlex institutions by ROR ID."
    )
    parser.add_argument("--input", type=Path, required=True, help="Reconciled CORDIS CSV.")
    parser.add_argument("--output", type=Path, required=True, help="Enriched CSV output.")
    parser.add_argument(
        "--cache",
        type=Path,
        default=Path("data/processed/openalex_cache.json"),
        help="Persistent OpenAlex response cache.",
    )
    parser.add_argument("--from-year", type=int, required=True)
    parser.add_argument("--to-year", type=int, required=True)
    parser.add_argument("--refresh", action="store_true", help="Refresh cached institutions.")
    args = parser.parse_args()

    rows = load_reconciled_rows(args.input)
    with OpenAlexClient(cache_path=args.cache) as client:
        enriched = enrich_reconciled_rows(
            client,
            rows,
            from_year=args.from_year,
            to_year=args.to_year,
            refresh=args.refresh,
        )
    write_enriched_rows(enriched, args.output)
    joined_ids = {row["ror_id"] for row in enriched if row.get("openalex_status") == "joined"}
    print(
        json.dumps(
            {
                "source_rows": len(rows),
                "joined_institutions": len(joined_ids),
                "from_year": args.from_year,
                "to_year": args.to_year,
                "output": str(args.output),
                "cache": str(args.cache),
            },
            indent=2,
        )
    )


def eval_main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the matcher on a gold CSV.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--report", type=Path, help="Detailed JSON report output.")
    parser.add_argument("--cases-output", type=Path, help="Per-case CSV output.")
    parser.add_argument("--bootstrap", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--confidence", type=float, default=0.95)
    parser.add_argument("--ablation-output", type=Path)
    parser.add_argument("--threshold-sweep-output", type=Path)
    parser.add_argument(
        "--thresholds",
        default="0.82,0.84,0.86,0.88,0.90,0.92,0.94",
        help="Comma-separated auto-match thresholds.",
    )
    parser.add_argument(
        "--lexical-gates",
        default="0.78,0.80,0.82,0.84",
        help="Comma-separated lexical gates for the development grid search.",
    )
    parser.add_argument("--ror-baseline-cache", type=Path)
    parser.add_argument("--ror-baseline-output", type=Path)
    parser.add_argument("--refresh-baseline", action="store_true")
    args = parser.parse_args()

    _require_populated_db(parser, args.db)
    cases = load_gold(args.gold)
    store = RorStore(args.db)
    try:
        store.init_schema()
        matcher = RorMatcher(store)
        report = run_detailed_evaluation(
            matcher,
            cases,
            bootstrap_iterations=args.bootstrap,
            seed=args.seed,
            confidence=args.confidence,
        )
        if args.ablation_output:
            _write_json(args.ablation_output, run_ablation(store, cases))
        if args.threshold_sweep_output:
            try:
                thresholds = tuple(float(value) for value in args.thresholds.split(","))
                lexical_gates = tuple(float(value) for value in args.lexical_gates.split(","))
            except ValueError:
                parser.error("--thresholds and --lexical-gates must be comma-separated numbers")
            _write_json(
                args.threshold_sweep_output,
                run_threshold_sweep(
                    store,
                    cases,
                    thresholds=thresholds,
                    lexical_gates=lexical_gates,
                ),
            )
    finally:
        store.close()

    if args.report:
        cases_output = args.cases_output or args.report.with_name(args.report.stem + "-cases.csv")
        write_evaluation_outputs(report, args.report, cases_output)
    elif args.cases_output:
        parser.error("--cases-output requires --report")

    if bool(args.ror_baseline_cache) != bool(args.ror_baseline_output):
        parser.error("provide both --ror-baseline-cache and --ror-baseline-output")
    if args.ror_baseline_cache:
        with RorApiBaseline(cache_path=args.ror_baseline_cache) as baseline:
            metrics, evidence = evaluate_ror_api_baseline(
                baseline,
                cases,
                refresh=args.refresh_baseline,
            )
        _write_json(args.ror_baseline_output, {"metrics": metrics, "cases": evidence})

    print(json.dumps(report.as_dict(), indent=2))


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
