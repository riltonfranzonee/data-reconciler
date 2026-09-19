from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AnalysisRun:
    organizations: tuple[dict[str, Any], ...]
    groups: tuple[dict[str, Any], ...]
    summary: dict[str, Any]


def analyse_combined_rows(rows: list[dict[str, str]]) -> AnalysisRun:
    """Create institution-level evidence from project-level CORDIS/OpenAlex rows."""
    by_ror: dict[str, dict[str, Any]] = {}
    unmatched_rows = 0

    for source_index, row in enumerate(rows):
        ror_id = (row.get("ror_id") or "").strip()
        if not ror_id or row.get("openalex_status") != "joined":
            unmatched_rows += 1
            continue
        record = by_ror.setdefault(
            ror_id,
            {
                "ror_id": ror_id,
                "ror_name": row.get("ror_name") or row.get("openalex_name") or "",
                "country": row.get("country") or row.get("openalex_country_code") or "",
                "activity_type": row.get("activityType") or "",
                "project_ids": set(),
                "participation_keys": set(),
                "cordis_ec_contribution": 0.0,
                "contribution_rows": 0,
                "openalex_works_count_period": _as_int(row.get("openalex_works_count_period")),
                "openalex_cited_by_count_period": _as_int(
                    row.get("openalex_cited_by_count_period")
                ),
                "openalex_from_year": _as_int(row.get("openalex_from_year")),
                "openalex_to_year": _as_int(row.get("openalex_to_year")),
            },
        )
        project_id = (row.get("projectID") or "").strip()
        if project_id:
            record["project_ids"].add(project_id)
            participation_key = project_id
        else:
            participation_key = f"row:{source_index}"
        if participation_key not in record["participation_keys"]:
            record["participation_keys"].add(participation_key)
            contribution = _first_amount(row)
            if contribution is not None:
                record["cordis_ec_contribution"] += contribution
                record["contribution_rows"] += 1

    organizations: list[dict[str, Any]] = []
    for record in by_ror.values():
        organizations.append(
            {
                "ror_id": record["ror_id"],
                "ror_name": record["ror_name"],
                "country": record["country"],
                "activity_type": record["activity_type"],
                "cordis_project_count": len(record["participation_keys"]),
                "cordis_ec_contribution": round(record["cordis_ec_contribution"], 2),
                "cordis_contribution_rows": record["contribution_rows"],
                "openalex_from_year": record["openalex_from_year"],
                "openalex_to_year": record["openalex_to_year"],
                "openalex_works_count_period": record["openalex_works_count_period"],
                "openalex_cited_by_count_period": record["openalex_cited_by_count_period"],
            }
        )
    organizations.sort(key=lambda row: (-row["cordis_project_count"], row["ror_id"]))

    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in organizations:
        key = (row["country"], row["activity_type"])
        group = grouped.setdefault(
            key,
            {
                "country": row["country"],
                "activity_type": row["activity_type"],
                "organization_count": 0,
                "cordis_project_participations": 0,
                "cordis_ec_contribution": 0.0,
                "openalex_works_count_period": 0,
                "openalex_cited_by_count_period": 0,
            },
        )
        group["organization_count"] += 1
        group["cordis_project_participations"] += row["cordis_project_count"]
        group["cordis_ec_contribution"] += row["cordis_ec_contribution"]
        group["openalex_works_count_period"] += row["openalex_works_count_period"]
        group["openalex_cited_by_count_period"] += row["openalex_cited_by_count_period"]
    groups = sorted(grouped.values(), key=lambda row: (row["country"], row["activity_type"]))
    for group in groups:
        group["cordis_ec_contribution"] = round(group["cordis_ec_contribution"], 2)

    project_counts = [float(row["cordis_project_count"]) for row in organizations]
    contribution = [float(row["cordis_ec_contribution"]) for row in organizations]
    works = [float(row["openalex_works_count_period"]) for row in organizations]
    summary = {
        "source_rows": len(rows),
        "joined_organizations": len(organizations),
        "unmatched_source_rows": unmatched_rows,
        "project_count_works_pearson_r": _pearson(project_counts, works),
        "ec_contribution_works_pearson_r": _pearson(contribution, works),
        "interpretation_warning": (
            "Descriptive association only; OpenAlex output and CORDIS participation are "
            "not evidence of a causal funding effect."
        ),
    }
    return AnalysisRun(
        organizations=tuple(organizations),
        groups=tuple(groups),
        summary=summary,
    )


def write_analysis_outputs(
    run: AnalysisRun,
    organization_path: Path | str,
    group_path: Path | str,
    summary_path: Path | str,
) -> None:
    _write_rows(run.organizations, Path(organization_path))
    _write_rows(run.groups, Path(group_path))
    summary_output = Path(summary_path)
    summary_output.parent.mkdir(parents=True, exist_ok=True)
    summary_output.write_text(
        json.dumps(run.summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_rows(rows: tuple[dict[str, Any], ...], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if fieldnames:
            writer.writeheader()
            writer.writerows(rows)


def _first_amount(row: dict[str, str]) -> float | None:
    for field in ("ecContribution", "netEcContribution", "ec_contribution"):
        value = row.get(field)
        if value not in (None, ""):
            cleaned = str(value).replace(",", "").replace("€", "").strip()
            try:
                return float(cleaned)
            except ValueError:
                return None
    return None


def _as_int(value: Any) -> int:
    if value in (None, ""):
        return 0
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) < 2 or len(left) != len(right):
        return None
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum((x - left_mean) * (y - right_mean) for x, y in zip(left, right, strict=True))
    left_ss = sum((x - left_mean) ** 2 for x in left)
    right_ss = sum((y - right_mean) ** 2 for y in right)
    denominator = math.sqrt(left_ss * right_ss)
    if denominator == 0:
        return None
    return round(numerator / denominator, 6)
