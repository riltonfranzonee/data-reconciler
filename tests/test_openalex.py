import httpx

from ror_reconcile.openalex import OpenAlexClient, enrich_reconciled_rows


def test_openalex_enrichment_joins_by_ror_and_reuses_local_cache(tmp_path):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "https://openalex.org/I123",
                "display_name": "University College London",
                "type": "education",
                "country_code": "GB",
                "works_count": 100,
                "cited_by_count": 500,
                "counts_by_year": [
                    {"year": 2024, "works_count": 7, "cited_by_count": 30},
                    {"year": 2023, "works_count": 5, "cited_by_count": 20},
                    {"year": 2022, "works_count": 3, "cited_by_count": 10},
                ],
                "ids": {"ror": "https://ror.org/02jx3x895"},
            },
        )

    cache = tmp_path / "openalex-cache.json"
    rows = [
        {"projectID": "101", "ror_id": "https://ror.org/02jx3x895"},
        {"projectID": "102", "ror_id": "https://ror.org/02jx3x895"},
        {"projectID": "103", "ror_id": ""},
    ]
    with httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.test") as http:
        client = OpenAlexClient(cache_path=cache, http_client=http)
        enriched = enrich_reconciled_rows(client, rows, from_year=2023, to_year=2024)

    assert len(requests) == 1
    assert enriched[0]["openalex_id"] == "https://openalex.org/I123"
    assert enriched[0]["openalex_works_count_period"] == 12
    assert enriched[0]["openalex_cited_by_count_period"] == 50
    assert enriched[1]["openalex_id"] == "https://openalex.org/I123"
    assert enriched[2]["openalex_status"] == "not_matched"
    assert cache.exists()

    def should_not_call_network(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected request: {request.url}")

    with httpx.Client(
        transport=httpx.MockTransport(should_not_call_network), base_url="https://api.test"
    ) as http:
        cached_client = OpenAlexClient(cache_path=cache, http_client=http)
        institution = cached_client.get_institution("https://ror.org/02jx3x895")

    assert institution["openalex_id"] == "https://openalex.org/I123"


def test_openalex_retries_rate_limit_response(tmp_path):
    responses = iter(
        [
            httpx.Response(429, headers={"Retry-After": "0"}),
            httpx.Response(
                200,
                json={
                    "id": "https://openalex.org/I123",
                    "display_name": "University College London",
                    "ids": {"ror": "https://ror.org/02jx3x895"},
                },
            ),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return next(responses)

    with httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.test") as http:
        client = OpenAlexClient(
            cache_path=tmp_path / "cache.json",
            http_client=http,
            request_interval_seconds=0,
            max_retries=1,
        )
        institution = client.get_institution("https://ror.org/02jx3x895")

    assert institution["openalex_id"] == "https://openalex.org/I123"
