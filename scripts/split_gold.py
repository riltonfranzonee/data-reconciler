"""Split reference labels into development and test partitions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ror_reconcile.evaluation.gold import load_gold, split_gold, write_gold


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a seeded, disjoint gold dev/test split.")
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--dev-output", type=Path, required=True)
    parser.add_argument("--test-output", type=Path, required=True)
    parser.add_argument("--dev-fraction", type=float, default=0.5)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    dev, test = split_gold(
        load_gold(args.gold),
        dev_fraction=args.dev_fraction,
        seed=args.seed,
    )
    write_gold(dev, args.dev_output)
    write_gold(test, args.test_output)
    print(
        json.dumps(
            {
                "dev_cases": len(dev),
                "test_cases": len(test),
                "seed": args.seed,
                "dev_fraction": args.dev_fraction,
                "dev_output": str(args.dev_output),
                "test_output": str(args.test_output),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
