from __future__ import annotations

import csv
import hashlib
import random
from dataclasses import dataclass
from pathlib import Path

from ror_reconcile.models import MatchQuery


@dataclass(frozen=True)
class GoldCase:
    query: str
    expected_ror_id: str | None
    country: str | None = None
    city: str | None = None
    stratum: str | None = None
    notes: str | None = None
    case_id: str | None = None
    source: str | None = None

    @property
    def has_known_match(self) -> bool:
        return bool(self.expected_ror_id)

    @property
    def identifier(self) -> str:
        if self.case_id:
            return self.case_id
        identity = "|".join(
            (
                self.query.strip().casefold(),
                (self.country or "").strip().casefold(),
                (self.city or "").strip().casefold(),
            )
        )
        return "case-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]

    def to_match_query(self) -> MatchQuery:
        return MatchQuery(query=self.query, country=self.country, city=self.city)


def load_gold(path: Path | str) -> list[GoldCase]:
    cases: list[GoldCase] = []
    with Path(path).open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            query = (row.get("query") or row.get("name") or "").strip()
            if not query:
                continue
            expected = (row.get("expected_ror_id") or row.get("ror_id") or "").strip() or None
            cases.append(
                GoldCase(
                    query=query,
                    expected_ror_id=expected,
                    country=(row.get("country") or "").strip() or None,
                    city=(row.get("city") or "").strip() or None,
                    stratum=(row.get("stratum") or "").strip() or None,
                    notes=(row.get("notes") or "").strip() or None,
                    case_id=(row.get("case_id") or "").strip() or None,
                    source=(row.get("source") or "").strip() or None,
                )
            )
    validate_gold(cases)
    return cases


def validate_gold(cases: list[GoldCase]) -> None:
    seen: dict[tuple[str, str, str], str] = {}
    for case in cases:
        key = (
            case.query.strip().casefold(),
            (case.country or "").strip().casefold(),
            (case.city or "").strip().casefold(),
        )
        if key in seen:
            raise ValueError(
                f"duplicate gold query/property combination: {case.identifier} and {seen[key]}"
            )
        seen[key] = case.identifier


def split_gold(
    cases: list[GoldCase],
    *,
    dev_fraction: float = 0.5,
    seed: int = 42,
) -> tuple[list[GoldCase], list[GoldCase]]:
    if not 0 < dev_fraction < 1:
        raise ValueError("dev_fraction must be between zero and one")
    validate_gold(cases)
    if len(cases) < 2:
        raise ValueError("at least two cases are required for a dev/test split")

    randomizer = random.Random(seed)
    by_stratum: dict[tuple[str, bool], list[GoldCase]] = {}
    for case in cases:
        key = (case.stratum or "__unstratified__", case.has_known_match)
        by_stratum.setdefault(key, []).append(case)

    dev_ids: set[str] = set()
    for group in by_stratum.values():
        shuffled = list(group)
        randomizer.shuffle(shuffled)
        if len(shuffled) == 1:
            dev_count = 0
        else:
            dev_count = min(len(shuffled) - 1, max(1, round(len(shuffled) * dev_fraction)))
        dev_ids.update(case.identifier for case in shuffled[:dev_count])

    target = min(len(cases) - 1, max(1, round(len(cases) * dev_fraction)))
    candidates = list(cases)
    randomizer.shuffle(candidates)
    if len(dev_ids) < target:
        for case in candidates:
            if case.identifier not in dev_ids:
                dev_ids.add(case.identifier)
                if len(dev_ids) == target:
                    break
    elif len(dev_ids) > target:
        for case in candidates:
            if case.identifier in dev_ids:
                dev_ids.remove(case.identifier)
                if len(dev_ids) == target:
                    break

    dev = [case for case in cases if case.identifier in dev_ids]
    test = [case for case in cases if case.identifier not in dev_ids]
    return dev, test


def write_gold(cases: list[GoldCase], path: Path | str) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = (
        "case_id",
        "query",
        "country",
        "city",
        "expected_ror_id",
        "stratum",
        "source",
        "notes",
    )
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for case in cases:
            writer.writerow(
                {
                    "case_id": case.identifier,
                    "query": case.query,
                    "country": case.country or "",
                    "city": case.city or "",
                    "expected_ror_id": case.expected_ror_id or "",
                    "stratum": case.stratum or "",
                    "source": case.source or "",
                    "notes": case.notes or "",
                }
            )
