from __future__ import annotations

from collections import OrderedDict

from rapidfuzz import fuzz

from ror_reconcile.config import MatchConfig
from ror_reconcile.models import Candidate, IndexedName, MatchQuery, MatchResult, OrgRecord
from ror_reconcile.normalize import normalize_city, normalize_country, normalize_name
from ror_reconcile.store import RorStore

_SCORING_STOPWORDS = {
    "and",
    "das",
    "de",
    "der",
    "des",
    "die",
    "for",
    "la",
    "le",
    "of",
    "or",
    "the",
    "zu",
    "zur",
}


class RorMatcher:
    """Rank ROR candidates and decide whether the leading match can be accepted."""

    def __init__(self, store: RorStore, config: MatchConfig | None = None):
        self.store = store
        self.config = config or MatchConfig()

    def match_batch(self, queries: dict[str, MatchQuery]) -> dict[str, MatchResult]:
        return {key: self.match_one(query) for key, query in queries.items()}

    def blocking_candidate_ids(self, query: MatchQuery) -> set[str]:
        norm_query = normalize_name(query.query)
        if not norm_query:
            return set()
        return {org.ror_id for org in self._candidate_records(norm_query)}

    def match_one(self, query: MatchQuery) -> MatchResult:
        norm_query = normalize_name(query.query)
        if not norm_query:
            return MatchResult()

        limit = query.limit or self.config.return_limit
        candidates = self._candidate_records(norm_query)
        scored = [self._score_candidate(norm_query, query, org) for org in candidates]
        scored.sort(key=lambda candidate: candidate.score, reverse=True)

        if not scored:
            return MatchResult()

        scored = self._apply_match_decision(scored, norm_query)
        return MatchResult(candidates=tuple(scored[:limit]))

    def _candidate_records(self, norm_query: str) -> list[OrgRecord]:
        by_id: OrderedDict[str, OrgRecord] = OrderedDict()
        exact = self.store.find_exact(
            norm_query, limit=self.config.exact_limit, active_only=self.config.active_only
        )
        fts = (
            self.store.shortlist(
                norm_query,
                limit=self.config.shortlist_limit,
                active_only=self.config.active_only,
            )
            if self.config.use_fts
            else []
        )
        trigram = (
            self.store.shortlist_trigram(
                norm_query,
                limit=self.config.shortlist_limit,
                active_only=self.config.active_only,
            )
            if self.config.use_trigram
            else []
        )
        for org in [*exact, *fts, *trigram]:
            by_id.setdefault(org.ror_id, org)
        return list(by_id.values())

    def _score_candidate(self, norm_query: str, query: MatchQuery, org: OrgRecord) -> Candidate:
        best_name, name_score, exact = self._best_name_score(norm_query, org.names)
        country_match = self._country_match(query.country, org) if self.config.use_country else None
        city_match = self._city_match(query.city, org) if self.config.use_city else None

        final_score = name_score
        if country_match is True:
            final_score += self.config.country_boost
        elif country_match is False:
            final_score -= self.config.country_penalty

        if city_match is True:
            final_score += self.config.city_boost
        elif city_match is False:
            final_score -= self.config.city_penalty

        return Candidate(
            org=org,
            score=max(0.0, min(1.0, final_score)),
            name_score=name_score,
            best_name=best_name,
            country_match=country_match,
            city_match=city_match,
            exact_match=exact,
        )

    def _apply_match_decision(
        self, candidates: list[Candidate], norm_query: str
    ) -> list[Candidate]:
        if not candidates:
            return candidates
        top = candidates[0]
        second_score = candidates[1].score if len(candidates) > 1 else 0.0
        margin = top.score - second_score
        margin_ok = not self.config.use_margin or margin >= self.config.min_margin
        exact_country_candidates = sum(
            candidate.exact_match and candidate.country_match is True for candidate in candidates
        )
        exact_country_ok = (
            top.exact_match and top.country_match is True and exact_country_candidates == 1
        )
        has_name_signal = (
            not self.config.require_multi_token_fuzzy_signal
            or top.exact_match
            or len(_scoring_tokens(norm_query, self.config.min_scoring_token_len)) >= 2
        )
        top_is_match = (
            has_name_signal
            and top.name_score >= self.config.lexical_gate
            and top.score >= self.config.auto_match_threshold
            and (margin_ok or exact_country_ok)
            and top.country_match is not False
        )

        decided: list[Candidate] = []
        for index, candidate in enumerate(candidates):
            candidate_margin = candidate.score - (
                candidates[index + 1].score if index + 1 < len(candidates) else 0.0
            )
            decided.append(
                Candidate(
                    org=candidate.org,
                    score=candidate.score,
                    name_score=candidate.name_score,
                    best_name=candidate.best_name,
                    match=top_is_match if index == 0 else False,
                    margin=candidate_margin,
                    country_match=candidate.country_match,
                    city_match=candidate.city_match,
                    exact_match=candidate.exact_match,
                )
            )
        return decided

    def _best_name_score(
        self,
        norm_query: str,
        names: tuple[IndexedName, ...],
    ) -> tuple[IndexedName, float, bool]:
        if not names:
            placeholder = IndexedName(value="", norm="", kind="missing")
            return placeholder, 0.0, False

        min_len = self.config.min_scoring_token_len
        threshold = self.config.token_coverage_threshold
        exponent = self.config.compactness_exponent

        best = names[0]
        best_score = -1.0
        query_tokens = _scoring_tokens(norm_query, min_len)
        for indexed_name in names:
            if norm_query == indexed_name.norm:
                return indexed_name, 1.0, True
            coverage = (
                _token_coverage_factor(
                    query_tokens,
                    _scoring_tokens(indexed_name.norm, min_len),
                    threshold,
                    exponent,
                )
                if self.config.use_coverage
                else 1.0
            )
            raw_score = max(
                fuzz.token_set_ratio(norm_query, indexed_name.norm) / 100.0,
                fuzz.token_sort_ratio(norm_query, indexed_name.norm) / 100.0,
                fuzz.WRatio(norm_query, indexed_name.norm) / 100.0,
            )
            score = raw_score * coverage
            if score > best_score:
                best = indexed_name
                best_score = score
        return best, max(0.0, best_score), False

    @staticmethod
    def _country_match(country: str | None, org: OrgRecord) -> bool | None:
        query_country = normalize_country(country)
        if not query_country:
            return None
        locations = org.locations or ()
        country_pairs = [
            (normalize_country(location.country_code), normalize_country(location.country))
            for location in locations
        ] or [(normalize_country(org.country_code), normalize_country(org.country))]
        country_pairs = [pair for pair in country_pairs if pair[0] or pair[1]]
        if not country_pairs:
            return None
        if len(query_country) == 2:
            return any(query_country == code for code, _ in country_pairs)
        return any(
            query_country == country or query_country == code for code, country in country_pairs
        )

    @staticmethod
    def _city_match(city: str | None, org: OrgRecord) -> bool | None:
        query_city = normalize_city(city)
        if not query_city:
            return None
        org_cities = [
            normalize_city(location.city) for location in org.locations if location.city
        ] or [normalize_city(org.city)]
        org_cities = [city for city in org_cities if city]
        if not org_cities:
            return None
        return query_city in org_cities


def _scoring_tokens(norm_name: str, min_len: int) -> list[str]:
    return [
        token
        for token in norm_name.split()
        if len(token) >= min_len and token not in _SCORING_STOPWORDS
    ]


def _token_coverage_factor(
    query_tokens: list[str],
    name_tokens: list[str],
    threshold: float,
    exponent: float,
) -> float:
    # A query with no discriminating tokens carries no usable name signal; only
    # the exact-match short-circuit may match it, never the fuzzy path.
    if not query_tokens:
        return 0.0
    if not name_tokens:
        return 0.0

    covered_query_tokens = 0
    for query_token in query_tokens:
        best = max(fuzz.ratio(query_token, name_token) / 100.0 for name_token in name_tokens)
        if best >= threshold:
            covered_query_tokens += 1

    covered_name_tokens = 0
    for name_token in name_tokens:
        best = max(fuzz.ratio(name_token, query_token) / 100.0 for query_token in query_tokens)
        if best >= threshold:
            covered_name_tokens += 1

    query_coverage = covered_query_tokens / len(query_tokens)
    candidate_compactness = covered_name_tokens / len(name_tokens)
    return query_coverage * (candidate_compactness**exponent)
