from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ror_reconcile.config import DEFAULT_DB_PATH, MatchConfig
from ror_reconcile.matcher import RorMatcher
from ror_reconcile.models import MatchQuery
from ror_reconcile.store import RorStore

COUNTRY_PROPERTIES = {"country", "country_code", "organization_country", "organisation_country"}
CITY_PROPERTIES = {"city", "organization_city", "organisation_city"}


def create_app(
    db_path: Path | str = DEFAULT_DB_PATH,
    *,
    config: MatchConfig | None = None,
) -> FastAPI:
    store = RorStore(db_path)
    store.init_schema()
    matcher = RorMatcher(store, config=config)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            store.close()

    app = FastAPI(title="Local ROR Reconciler", version="0.1.0", lifespan=lifespan)
    app.state.store = store
    app.state.matcher = matcher

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/")
    def get_root(request: Request, queries: str | None = Query(default=None)):
        if queries is None:
            return manifest(request)
        return reconcile_queries(matcher, queries)

    @app.post("/")
    def post_root(queries: str = Form(...)):
        return reconcile_queries(matcher, queries)

    @app.get("/suggest/property")
    def suggest_property(prefix: str = "", type: str | None = None):  # noqa: A002
        del type
        options = [
            {"id": "country", "name": "Country code or country name"},
            {"id": "city", "name": "City"},
        ]
        lowered = prefix.lower()
        if lowered:
            options = [
                option
                for option in options
                if lowered in option["id"].lower() or lowered in option["name"].lower()
            ]
        return {"result": options}

    @app.get("/health")
    def health():
        return {"ok": True, "organizations": store.count_orgs()}

    return app


def manifest(request: Request) -> dict[str, Any]:
    service_url = str(request.base_url).rstrip("/")
    return {
        "versions": ["0.2"],
        "name": "Local ROR Reconciler",
        "identifierSpace": "https://ror.org/",
        "schemaSpace": "https://ror.org/",
        "defaultTypes": [{"id": "organization", "name": "Organization"}],
        "batchSize": 50,
        "view": {"url": "{{id}}"},
        "suggest": {
            "property": {
                "service_url": service_url,
                "service_path": "/suggest/property",
            }
        },
    }


def reconcile_queries(matcher: RorMatcher, raw_queries: str) -> JSONResponse:
    try:
        payload = json.loads(raw_queries)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"invalid queries JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="queries must be a JSON object")

    parsed = {key: _parse_query(value) for key, value in payload.items()}
    results = matcher.match_batch(parsed)
    return JSONResponse(
        {key: {"result": result.to_openrefine()} for key, result in results.items()}
    )


def _parse_query(payload: Any) -> MatchQuery:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="each query must be an object")

    properties = payload.get("properties") or []
    if not isinstance(properties, list):
        raise HTTPException(status_code=400, detail="query properties must be an array")

    country = None
    city = None
    for prop in properties:
        if not isinstance(prop, dict):
            continue
        pid = str(prop.get("pid") or "").strip().lower()
        value = _property_value(prop.get("v"))
        if pid in COUNTRY_PROPERTIES:
            country = value
        elif pid in CITY_PROPERTIES:
            city = value

    limit = payload.get("limit")
    if limit is not None:
        try:
            limit = max(1, min(20, int(limit)))
        except (TypeError, ValueError):
            limit = None

    return MatchQuery(
        query=str(payload.get("query") or ""),
        country=country,
        city=city,
        limit=limit,
    )


def _property_value(value: Any) -> str | None:
    if isinstance(value, list):
        return _property_value(value[0]) if value else None
    if isinstance(value, dict):
        return str(value.get("id") or value.get("name") or value.get("value") or "").strip() or None
    if value is None:
        return None
    return str(value).strip() or None
