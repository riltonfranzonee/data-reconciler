"""Summarise the joined CORDIS and OpenAlex cohort for the case study."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ror_reconcile.analysis import analyse_combined_rows, write_analysis_outputs
from ror_reconcile.openalex import load_reconciled_rows


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Aggregate CORDIS/OpenAlex rows without duplicating institution totals."
    )
    parser.add_argument("--input", type=Path, required=True, help="Enriched project-level CSV.")
    parser.add_argument("--organization-output", type=Path, required=True)
    parser.add_argument("--group-output", type=Path, required=True)
    parser.add_argument("--summary-output", type=Path, required=True)
    args = parser.parse_args()

    rows = load_reconciled_rows(args.input)
    run = analyse_combined_rows(rows)
    write_analysis_outputs(
        run,
        args.organization_output,
        args.group_output,
        args.summary_output,
    )
    print(json.dumps(run.summary, indent=2))


if __name__ == "__main__":
    main()
