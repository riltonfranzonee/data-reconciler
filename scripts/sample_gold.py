"""Select CORDIS organisations and create a reference-label template."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ror_reconcile.cordis import build_gold_label_template
from ror_reconcile.evaluation.gold import write_gold
from ror_reconcile.ingest import load_cordis_organizations


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a country-balanced CORDIS template for independent manual labelling."
    )
    parser.add_argument("--input", type=Path, required=True, help="CORDIS CSV or projects ZIP.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--size", type=int, default=150)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    rows = load_cordis_organizations(args.input)
    cases = build_gold_label_template(rows, size=args.size, seed=args.seed)
    write_gold(cases, args.output)
    print(
        json.dumps(
            {
                "source_rows": len(rows),
                "unique_cases_sampled": len(cases),
                "seed": args.seed,
                "output": str(args.output),
                "requires_manual_labels": True,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
