import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from ror_reconcile.openrefine_api import create_app


def test_manifest_and_property_suggest(fixture_store):
    app = create_app(fixture_store.db_path)
    client = TestClient(app)

    manifest = client.get("/").json()
    assert manifest["versions"] == ["0.2"]
    assert manifest["identifierSpace"] == "https://ror.org/"
    assert manifest["suggest"]["property"]["service_path"] == "/suggest/property"

    suggested = client.get("/suggest/property", params={"prefix": "cou"}).json()
    assert suggested["result"][0]["id"] == "country"


def test_form_encoded_reconciliation_query(fixture_store):
    app = create_app(fixture_store.db_path)
    client = TestClient(app)

    queries = {
        "q0": {
            "query": "Universitat zu Koln",
            "limit": 3,
            "properties": [{"pid": "country", "v": "DE"}],
        }
    }
    response = client.post("/", data={"queries": json.dumps(queries)})
    assert response.status_code == 200
    query_result = response.json()["q0"]
    assert isinstance(query_result, dict)
    assert set(query_result) == {"result"}

    result = query_result["result"]
    assert result[0]["id"] == "https://ror.org/00rcxh774"
    assert result[0]["type"]
    assert {"id": "country_match", "value": True} in result[0]["features"]


def test_get_reconciliation_query_uses_spec_result_wrapper(fixture_store):
    app = create_app(fixture_store.db_path)
    client = TestClient(app)

    queries = {"q0": {"query": "UCL", "limit": 1}}
    response = client.get("/", params={"queries": json.dumps(queries)})
    assert response.status_code == 200
    assert response.json()["q0"]["result"][0]["id"] == "https://ror.org/02jx3x895"


def test_parallel_reconciliation_requests_share_store_safely(fixture_store):
    app = create_app(fixture_store.db_path)
    client = TestClient(app)
    query_payloads = [
        {"q0": {"query": "UCL", "limit": 3, "properties": [{"pid": "country", "v": "GB"}]}},
        {"q0": {"query": "Universitat zu Koln", "limit": 3}},
        {"q0": {"query": "Max Planck Gesellschaft", "limit": 3}},
        {"q0": {"query": "Institute for Advanced Studies", "limit": 3}},
    ]

    def post_query(index: int):
        payload = query_payloads[index % len(query_payloads)]
        return client.post("/", data={"queries": json.dumps(payload)})

    with ThreadPoolExecutor(max_workers=8) as executor:
        responses = list(executor.map(post_query, range(40)))

    assert all(response.status_code == 200 for response in responses)
    assert all("result" in response.json()["q0"] for response in responses)


def test_service_closes_its_sqlite_connection_on_shutdown(fixture_store):
    app = create_app(fixture_store.db_path)

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200

    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        app.state.store.count_orgs()
