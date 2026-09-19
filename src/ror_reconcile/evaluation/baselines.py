from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from ror_reconcile.evaluation.gold import GoldCase

ROR_API_BASE_URL = "https://api.ror.org/v2"


class RorApiBaseline:
    """Cached hosted ROR name-search baseline, independent of local tuning."""

    def __init__(
        self,
        *,
        cache_path: Path | str,
        http_client: httpx.Client | None = None,
        max_retries: int = 3,
        backoff_seconds: float = 0.5,
    ):
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if backoff_seconds < 0:
            raise ValueError("backoff_seconds must be non-negative")
        self.cache_path = Path(cache_path)
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self._owns_client = http_client is None
        self.http = http_client or httpx.Client(
            base_url=ROR_API_BASE_URL,
            timeout=httpx.Timeout(30.0),
            headers={"User-Agent": "ror-reconcile/0.1"},
        )
        self.cache = self._load_cache()

    def close(self) -> None:
        if self._owns_client:
            self.http.close()

    def __enter__(self) -> RorApiBaseline:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def search(self, query: str, *, refresh: bool = False) -> list[dict[str, Any]]:
        cleaned_query = query.strip()
        cached = self.cache["queries"].get(cleaned_query)
        if cached and not refresh:
            return list(cached["results"])

        response = self._get_with_retries(cleaned_query)
        if response is None:
            return []
        results = [_ror_result(item) for item in response.json().get("items") or []]
        results = [result for result in results if result["ror_id"]]
        self.cache["queries"][cleaned_query] = {
            "fetched_at": datetime.now(UTC).isoformat(),
            "results": results,
        }
        self._save_cache()
        return results

    def lookup_error(self, query: str) -> str | None:
        cached = self.cache["queries"].get(query.strip()) or {}
        error = cached.get("error")
        return str(error) if error else None

    def _get_with_retries(self, query: str) -> httpx.Response | None:
        for attempt in range(self.max_retries + 1):
            try:
                response = self.http.get("/organizations", params={"query": query})
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status != 429 and status < 500:
                    raise
                error = f"HTTP {status}: {exc.response.reason_phrase}"
            except httpx.RequestError as exc:
                error = f"{type(exc).__name__}: {exc}"

            if attempt < self.max_retries:
                time.sleep(self.backoff_seconds * (2**attempt))
                continue
            self.cache["queries"][query] = {
                "fetched_at": datetime.now(UTC).isoformat(),
                "results": [],
                "error": error,
            }
            self._save_cache()
            return None

        raise AssertionError("retry loop must return")

    def _load_cache(self) -> dict[str, Any]:
        if self.cache_path.exists():
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if payload.get("schema_version") == 1 and isinstance(payload.get("queries"), dict):
                return payload
        return {"schema_version": 1, "source": ROR_API_BASE_URL, "queries": {}}

    def _save_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self.cache, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.cache_path)


def evaluate_ror_api_baseline(
    baseline: RorApiBaseline,
    cases: list[GoldCase],
    *,
    refresh: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    evidence: list[dict[str, Any]] = []
    known = 0
    answered = 0
    correct = 0
    top1_hits = 0
    correct_abstentions = 0
    lookup_errors = 0

    for case in cases:
        results = baseline.search(case.query, refresh=refresh)
        lookup_error = baseline.lookup_error(case.query)
        lookup_errors += lookup_error is not None
        top = results[0] if results else None
        predicted = top["ror_id"] if top else None
        is_known = bool(case.expected_ror_id)
        known += is_known
        answered += top is not None
        is_correct = bool(case.expected_ror_id and predicted == case.expected_ror_id)
        correct += is_correct
        top1_hits += is_correct
        correct_abstentions += not is_known and top is None and lookup_error is None
        evidence.append(
            {
                "case_id": case.identifier,
                "query": case.query,
                "expected_ror_id": case.expected_ror_id,
                "predicted_ror_id": predicted,
                "predicted_name": top["name"] if top else None,
                "result_count": len(results),
                "correct": is_correct,
                "lookup_error": lookup_error,
            }
        )

    no_match_cases = len(cases) - known
    metrics = {
        "cases": len(cases),
        "known_match_cases": known,
        "top1_accuracy_known": _ratio(top1_hits, known),
        "selective_precision": _ratio(correct, answered),
        "coverage": _ratio(answered, len(cases)),
        "answerable_coverage": _ratio(
            sum(bool(item["predicted_ror_id"] and item["expected_ror_id"]) for item in evidence),
            known,
        ),
        "end_to_end_recall": _ratio(correct, known),
        "correct_abstentions": correct_abstentions,
        "no_match_specificity": _ratio(correct_abstentions, no_match_cases),
        "lookup_errors": lookup_errors,
    }
    return metrics, evidence


def _ror_result(item: dict[str, Any]) -> dict[str, Any]:
    organization = item.get("organization") if isinstance(item.get("organization"), dict) else item
    name = organization.get("name") or ""
    if not name:
        for candidate in organization.get("names") or []:
            if "ror_display" in (candidate.get("types") or []):
                name = candidate.get("value") or ""
                break
    return {
        "ror_id": organization.get("id") or "",
        "name": name,
        "score": item.get("score"),
        "chosen": item.get("chosen"),
    }


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0
