from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

ORG_TYPE = {"id": "organization", "name": "Organization"}


@dataclass(frozen=True)
class IndexedName:
    value: str
    norm: str
    kind: str


@dataclass(frozen=True)
class OrgLocation:
    country: str | None = None
    country_code: str | None = None
    city: str | None = None


@dataclass(frozen=True)
class OrgRecord:
    ror_id: str
    name: str
    country: str | None = None
    country_code: str | None = None
    city: str | None = None
    status: str | None = None
    types: tuple[str, ...] = ()
    names: tuple[IndexedName, ...] = ()
    locations: tuple[OrgLocation, ...] = ()


@dataclass(frozen=True)
class MatchQuery:
    query: str
    country: str | None = None
    city: str | None = None
    limit: int | None = None


@dataclass(frozen=True)
class Candidate:
    org: OrgRecord
    score: float
    name_score: float
    best_name: IndexedName
    match: bool = False
    margin: float = 0.0
    country_match: bool | None = None
    city_match: bool | None = None
    exact_match: bool = False

    def to_openrefine(self) -> dict[str, Any]:
        description_parts = [
            f"name {self.name_score:.2f} via {self.best_name.kind}",
        ]
        if self.country_match is not None:
            description_parts.append(f"country {'match' if self.country_match else 'mismatch'}")
        if self.city_match is not None:
            description_parts.append(f"city {'match' if self.city_match else 'mismatch'}")
        if self.margin:
            description_parts.append(f"margin {self.margin:.2f}")

        features: list[dict[str, bool | float]] = [
            {"id": "name_fuzzy", "value": round(self.name_score, 4)},
            {"id": "exact_name", "value": self.exact_match},
            {"id": "final_score", "value": round(self.score, 4)},
            {"id": "margin", "value": round(self.margin, 4)},
        ]
        if self.country_match is not None:
            features.append({"id": "country_match", "value": self.country_match})
        if self.city_match is not None:
            features.append({"id": "city_match", "value": self.city_match})

        types = tuple({"id": t, "name": t} for t in self.org.types) or (ORG_TYPE,)

        return {
            "id": self.org.ror_id,
            "name": self.org.name,
            "type": list(types),
            "score": round(self.score * 100, 3),
            "match": self.match,
            "description": "; ".join(description_parts),
            "features": features,
        }


@dataclass(frozen=True)
class MatchResult:
    candidates: tuple[Candidate, ...] = field(default_factory=tuple)

    def to_openrefine(self) -> list[dict[str, Any]]:
        return [candidate.to_openrefine() for candidate in self.candidates]
