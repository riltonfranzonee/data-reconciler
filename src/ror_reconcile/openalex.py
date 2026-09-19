from __future__ import annotations

import csv
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

OPENALEX_BASE_URL = "https://api.openalex.org"


class OpenAlexClient:
    """ROR-keyed OpenAlex institution client with a persistent reproducibility cache."""

    def __init__(
        self,
        *,
        cache_path: Path | str,
        http_client: httpx.Client | None = None,
        api_key: str | None = None,
        mailto: str | None = None,
        request_interval_seconds: float = 0.11,
        max_retries: int = 3,
    ):
        self.cache_path = Path(cache_path)
        self._owns_client = http_client is None
        self.http = http_client or httpx.Client(
            base_url=OPENALEX_BASE_URL,
            timeout=httpx.Timeout(30.0),
            headers={"User-Agent": "ror-reconcile/0.1"},
        )
        self.api_key = api_key or os.environ.get("OPENALEX_API_KEY")
        self.mailto = mailto or os.environ.get("OPENALEX_MAILTO")
        self.request_interval_seconds = max(0.0, request_interval_seconds)
        self.max_retries = max(0, max_retries)
        self._last_request_started: float | None = None
        self.cache = self._load_cache()

    def close(self) -> None:
        if self._owns_client:
            self.http.close()

    def __enter__(self) -> OpenAlexClient:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def get_institution(self, ror_id: str, *, refresh: bool = False) -> dict[str, Any] | None:
        normalized_ror = ror_id.strip()
        cached = self.cache["institutions"].get(normalized_ror)
        if cached and not refresh:
            return cached.get("data")

        params: dict[str, str] = {}
        if self.api_key:
            params["api_key"] = self.api_key
        if self.mailto:
            params["mailto"] = self.mailto
        response = self._get(f"/institutions/{quote(normalized_ror, safe='')}", params=params)
        fetched_at = datetime.now(UTC).isoformat()
        if response.status_code == 404:
            self.cache["institutions"][normalized_ror] = {
                "status": "not_found",
                "fetched_at": fetched_at,
                "data": None,
            }
            self._save_cache()
            return None
        response.raise_for_status()
        data = _institution_record(response.json(), normalized_ror)
        self.cache["institutions"][normalized_ror] = {
            "status": "ok",
            "fetched_at": fetched_at,
            "data": data,
        }
        self._save_cache()
        return data

    def _get(self, path: str, *, params: dict[str, str]) -> httpx.Response:
        response: httpx.Response | None = None
        for attempt in range(self.max_retries + 1):
            if self._last_request_started is not None:
                elapsed = time.monotonic() - self._last_request_started
                if elapsed < self.request_interval_seconds:
                    time.sleep(self.request_interval_seconds - elapsed)
            self._last_request_started = time.monotonic()
            response = self.http.get(path, params=params)
            retryable = response.status_code == 429 or response.status_code >= 500
            if not retryable or attempt == self.max_retries:
                return response
            retry_after = response.headers.get("Retry-After")
            try:
                delay = float(retry_after) if retry_after is not None else 0.5 * (2**attempt)
            except ValueError:
                delay = 0.5 * (2**attempt)
            time.sleep(max(0.0, delay))
        assert response is not None
        return response

    def _load_cache(self) -> dict[str, Any]:
        if self.cache_path.exists():
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if payload.get("schema_version") == 1 and isinstance(payload.get("institutions"), dict):
                return payload
        return {
            "schema_version": 1,
            "source": OPENALEX_BASE_URL,
            "institutions": {},
        }

    def _save_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self.cache, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.cache_path)


def enrich_reconciled_rows(
    client: OpenAlexClient,
    rows: list[dict[str, str]],
    *,
    from_year: int,
    to_year: int,
    refresh: bool = False,
) -> list[dict[str, Any]]:
    if from_year > to_year:
        raise ValueError("from_year must be less than or equal to to_year")
    institutions: dict[str, dict[str, Any] | None] = {}
    enriched_rows: list[dict[str, Any]] = []

    for row in rows:
        enriched: dict[str, Any] = dict(row)
        ror_id = (row.get("ror_id") or "").strip()
        if not ror_id:
            enriched.update(_empty_enrichment("not_matched", from_year, to_year))
            enriched_rows.append(enriched)
            continue
        if ror_id not in institutions:
            institutions[ror_id] = client.get_institution(ror_id, refresh=refresh)
        institution = institutions[ror_id]
        if institution is None:
            enriched.update(_empty_enrichment("not_found", from_year, to_year))
            enriched_rows.append(enriched)
            continue

        counts = [
            count
            for count in institution["counts_by_year"]
            if from_year <= int(count["year"]) <= to_year
        ]
        enriched.update(
            {
                "openalex_status": "joined",
                "openalex_id": institution["openalex_id"],
                "openalex_name": institution["display_name"],
                "openalex_type": institution["type"],
                "openalex_country_code": institution["country_code"],
                "openalex_works_count_total": institution["works_count"],
                "openalex_cited_by_count_total": institution["cited_by_count"],
                "openalex_from_year": from_year,
                "openalex_to_year": to_year,
                "openalex_works_count_period": sum(int(item["works_count"]) for item in counts),
                "openalex_cited_by_count_period": sum(
                    int(item["cited_by_count"]) for item in counts
                ),
                "openalex_counts_by_year": json.dumps(counts, sort_keys=True),
            }
        )
        enriched_rows.append(enriched)
    return enriched_rows


def load_reconciled_rows(path: Path | str) -> list[dict[str, str]]:
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def write_enriched_rows(rows: list[dict[str, Any]], path: Path | str) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _institution_record(payload: dict[str, Any], requested_ror: str) -> dict[str, Any]:
    counts = []
    for item in payload.get("counts_by_year") or []:
        if item.get("year") is None:
            continue
        counts.append(
            {
                "year": int(item["year"]),
                "works_count": int(item.get("works_count") or 0),
                "cited_by_count": int(item.get("cited_by_count") or 0),
            }
        )
    counts.sort(key=lambda item: item["year"])
    return {
        "requested_ror_id": requested_ror,
        "ror_id": (payload.get("ids") or {}).get("ror") or requested_ror,
        "openalex_id": payload.get("id") or "",
        "display_name": payload.get("display_name") or "",
        "type": payload.get("type") or "",
        "country_code": payload.get("country_code") or "",
        "works_count": int(payload.get("works_count") or 0),
        "cited_by_count": int(payload.get("cited_by_count") or 0),
        "counts_by_year": counts,
    }


def _empty_enrichment(status: str, from_year: int, to_year: int) -> dict[str, Any]:
    return {
        "openalex_status": status,
        "openalex_id": "",
        "openalex_name": "",
        "openalex_type": "",
        "openalex_country_code": "",
        "openalex_works_count_total": "",
        "openalex_cited_by_count_total": "",
        "openalex_from_year": from_year,
        "openalex_to_year": to_year,
        "openalex_works_count_period": "",
        "openalex_cited_by_count_period": "",
        "openalex_counts_by_year": "[]",
    }
