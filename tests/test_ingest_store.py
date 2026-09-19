import json
from pathlib import Path

from ror_reconcile.ingest import load_ror_records
from ror_reconcile.matcher import RorMatcher
from ror_reconcile.models import IndexedName, MatchQuery, OrgLocation, OrgRecord
from ror_reconcile.normalize import normalize_name
from ror_reconcile.store import RorStore


def test_loads_ror_fixture_records():
    records = load_ror_records(Path(__file__).parent / "fixtures" / "ror_fixture.json")
    assert len(records) == 7
    assert records[0].ror_id == "https://ror.org/02jx3x895"
    assert {name.kind for name in records[0].names} >= {"ror_display", "acronym", "alias"}


def test_store_exact_and_fts_lookup(fixture_store):
    exact = fixture_store.find_exact("ucl", limit=5)
    assert exact[0].ror_id == "https://ror.org/02jx3x895"

    shortlist = fixture_store.shortlist("Universitat Koln", limit=5)
    assert "https://ror.org/00rcxh774" in {org.ror_id for org in shortlist}


def test_all_ror_locations_survive_storage_and_support_city_evidence(tmp_path):
    store = RorStore(tmp_path / "locations.sqlite")
    try:
        record = OrgRecord(
            ror_id="https://ror.example/multi-site",
            name="Multi Site Institute",
            country="United Kingdom",
            country_code="GB",
            city="London",
            locations=(
                OrgLocation(country="United Kingdom", country_code="GB", city="London"),
                OrgLocation(country="United Kingdom", country_code="GB", city="Manchester"),
            ),
            names=(
                IndexedName(
                    value="Multi Site Institute",
                    norm=normalize_name("Multi Site Institute"),
                    kind="ror_display",
                ),
            ),
        )
        store.replace_records([record])

        restored = store.get_org(record.ror_id)
        result = RorMatcher(store).match_one(
            MatchQuery(query="Multi Site Institute", country="GB", city="Manchester")
        )

        assert restored is not None
        assert [location.city for location in restored.locations] == ["London", "Manchester"]
        assert result.candidates[0].city_match is True
        assert result.candidates[0].match is True
    finally:
        store.close()


def test_ror_ingest_preserves_every_location(tmp_path):
    source = tmp_path / "ror.json"
    source.write_text(
        json.dumps(
            [
                {
                    "id": "https://ror.example/multi-site",
                    "names": [{"value": "Multi Site Institute", "types": ["ror_display"]}],
                    "locations": [
                        {
                            "geonames_details": {
                                "country_code": "GB",
                                "country_name": "United Kingdom",
                                "name": "London",
                            }
                        },
                        {
                            "geonames_details": {
                                "country_code": "GB",
                                "country_name": "United Kingdom",
                                "name": "Manchester",
                            }
                        },
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )

    record = load_ror_records(source)[0]

    assert [location.city for location in record.locations] == ["London", "Manchester"]
