"""Select the CORDIS case-study cohort and retain its project rows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ror_reconcile.cordis import select_cordis_cohort, write_cordis_rows
from ror_reconcile.ingest import load_cordis_organizations


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Select a country-balanced CORDIS cohort and retain all project rows."
    )
    parser.add_argument("--input", type=Path, required=True, help="CORDIS CSV or projects ZIP.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--organizations", type=int, default=300)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    rows = load_cordis_organizations(args.input)
    cohort = select_cordis_cohort(
        rows,
        organization_limit=args.organizations,
        seed=args.seed,
    )
    write_cordis_rows(cohort, args.output)
    print(
        json.dumps(
            {
                "source_rows": len(rows),
                "cohort_rows": len(cohort),
                "unique_organizations": len({row.get("organisationID") for row in cohort}),
                "seed": args.seed,
                "output": str(args.output),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
