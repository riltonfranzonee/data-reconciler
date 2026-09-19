from __future__ import annotations

import csv
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ror_reconcile.evaluation.gold import GoldCase
from ror_reconcile.matcher import RorMatcher
from ror_reconcile.models import MatchQuery, MatchResult
from ror_reconcile.normalize import normalize_city, normalize_country, normalize_name


@dataclass(frozen=True)
class CordisReconciliationRun:
    rows: tuple[dict[str, Any], ...]
    evidence: tuple[dict[str, Any], ...]
    unique_organizations: int
    matched_organizations: int
    abstained_organizations: int


def reconcile_cordis_rows(
    matcher: RorMatcher, rows: list[dict[str, str]]
) -> CordisReconciliationRun:
    """Reconcile unique CORDIS organisations while preserving project-level rows."""
    results_by_identity: dict[str, MatchResult] = {}
    source_by_identity: dict[str, dict[str, str]] = {}

    for row in rows:
        identity = cordis_organization_identity(row)
        if identity in results_by_identity:
            continue
        source_by_identity[identity] = dict(row)
        results_by_identity[identity] = matcher.match_one(
            MatchQuery(
                query=(row.get("name") or row.get("shortName") or "").strip(),
                country=(row.get("country") or "").strip() or None,
                city=(row.get("city") or "").strip() or None,
            )
        )

    output_rows: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    matched = 0
    abstained = 0

    for identity, result in results_by_identity.items():
        top = result.candidates[0] if result.candidates else None
        status = "matched" if top and top.match else "abstained" if top else "no_candidates"
        if status == "matched":
            matched += 1
        else:
            abstained += 1
        source = source_by_identity[identity]
        evidence.append(
            {
                "organization_identity": identity,
                "source": source,
                "match_status": status,
                "candidates": result.to_openrefine(),
            }
        )

    for row in rows:
        result = results_by_identity[cordis_organization_identity(row)]
        top = result.candidates[0] if result.candidates else None
        is_match = bool(top and top.match)
        enriched: dict[str, Any] = dict(row)
        enriched.update(
            {
                "match_status": "matched" if is_match else "abstained" if top else "no_candidates",
                "ror_id": top.org.ror_id if is_match else "",
                "ror_name": top.org.name if is_match else "",
                "candidate_ror_id": top.org.ror_id if top else "",
                "candidate_ror_name": top.org.name if top else "",
                "score": round(top.score, 6) if top else "",
                "name_score": round(top.name_score, 6) if top else "",
                "margin": round(top.margin, 6) if top else "",
                "best_name": top.best_name.value if top else "",
                "best_name_kind": top.best_name.kind if top else "",
                "country_match": top.country_match if top else "",
                "city_match": top.city_match if top else "",
                "exact_match": top.exact_match if top else "",
                "candidate_count": len(result.candidates),
            }
        )
        output_rows.append(enriched)

    return CordisReconciliationRun(
        rows=tuple(output_rows),
        evidence=tuple(evidence),
        unique_organizations=len(results_by_identity),
        matched_organizations=matched,
        abstained_organizations=abstained,
    )


def cordis_organization_identity(row: dict[str, str]) -> str:
    organization_id = (row.get("organisationID") or "").strip()
    if organization_id:
        return f"cordis:{organization_id}"
    return "fallback:" + "|".join(
        (
            normalize_name(row.get("name") or row.get("shortName") or ""),
            normalize_country(row.get("country")),
            normalize_city(row.get("city")),
        )
    )


def build_gold_label_template(
    rows: list[dict[str, str]],
    *,
    size: int,
    seed: int = 42,
) -> list[GoldCase]:
    """Select a country-balanced set of unique CORDIS organisations for manual labelling."""
    if size <= 0:
        raise ValueError("size must be positive")
    unique: dict[str, dict[str, str]] = {}
    for row in rows:
        if row.get("name") or row.get("shortName"):
            unique.setdefault(cordis_organization_identity(row), row)

    randomizer = random.Random(seed)
    by_country: dict[str, list[dict[str, str]]] = {}
    for row in unique.values():
        by_country.setdefault((row.get("country") or "unknown").strip() or "unknown", []).append(
            row
        )
    for group in by_country.values():
        randomizer.shuffle(group)

    selected: list[dict[str, str]] = []
    countries = sorted(by_country)
    while countries and len(selected) < min(size, len(unique)):
        next_countries: list[str] = []
        for country in countries:
            group = by_country[country]
            if group and len(selected) < size:
                selected.append(group.pop())
            if group:
                next_countries.append(country)
        countries = next_countries

    cases: list[GoldCase] = []
    for row in selected:
        query = (row.get("name") or row.get("shortName") or "").strip()
        organization_id = (row.get("organisationID") or "").strip()
        notes = "; ".join(
            part
            for part in (
                f"CORDIS organisationID={organization_id}" if organization_id else "",
                f"shortName={row.get('shortName')}" if row.get("shortName") else "",
                f"activityType={row.get('activityType')}" if row.get("activityType") else "",
            )
            if part
        )
        cases.append(
            GoldCase(
                query=query,
                expected_ror_id=None,
                country=(row.get("country") or "").strip() or None,
                city=(row.get("city") or "").strip() or None,
                stratum=_provisional_stratum(query),
                source=f"CORDIS:{organization_id}" if organization_id else "CORDIS",
                notes=notes or None,
            )
        )
    return cases


def select_cordis_cohort(
    rows: list[dict[str, str]],
    *,
    organization_limit: int,
    seed: int = 42,
) -> list[dict[str, str]]:
    """Select a country-balanced organisation cohort and retain all its project rows."""
    if organization_limit <= 0:
        raise ValueError("organization_limit must be positive")
    unique: dict[str, dict[str, str]] = {}
    for row in rows:
        if row.get("name") or row.get("shortName"):
            unique.setdefault(cordis_organization_identity(row), row)

    by_country: dict[str, list[str]] = {}
    for identity, row in unique.items():
        country = (row.get("country") or "unknown").strip() or "unknown"
        by_country.setdefault(country, []).append(identity)
    randomizer = random.Random(seed)
    for identities in by_country.values():
        randomizer.shuffle(identities)

    selected: set[str] = set()
    countries = sorted(by_country)
    while countries and len(selected) < min(organization_limit, len(unique)):
        remaining_countries: list[str] = []
        for country in countries:
            identities = by_country[country]
            if identities and len(selected) < organization_limit:
                selected.add(identities.pop())
            if identities:
                remaining_countries.append(country)
        countries = remaining_countries
    return [row for row in rows if cordis_organization_identity(row) in selected]


def write_cordis_rows(rows: list[dict[str, str]], path: Path | str) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if fieldnames:
            writer.writeheader()
            writer.writerows(rows)


def _provisional_stratum(query: str) -> str:
    if any(ord(character) > 127 for character in query):
        return "multilingual"
    tokens = query.split()
    if len(query) <= 6 and len(tokens) == 1:
        return "acronym"
    if len(tokens) <= 2:
        return "short_name"
    return "standard"


def write_cordis_outputs(
    run: CordisReconciliationRun,
    csv_path: Path | str,
    evidence_path: Path | str,
) -> None:
    csv_output = Path(csv_path)
    evidence_output = Path(evidence_path)
    csv_output.parent.mkdir(parents=True, exist_ok=True)
    evidence_output.parent.mkdir(parents=True, exist_ok=True)

    fieldnames: list[str] = []
    for row in run.rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)

    with csv_output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(run.rows)

    with evidence_output.open("w", encoding="utf-8", newline="\n") as handle:
        for item in run.evidence:
            handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
