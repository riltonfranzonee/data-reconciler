import csv
import json

from ror_reconcile.cli import reconcile_main
from ror_reconcile.cordis import (
    reconcile_cordis_rows,
    select_cordis_cohort,
    write_cordis_outputs,
)
from ror_reconcile.ingest import load_cordis_organizations


def test_cordis_loader_accepts_comma_or_semicolon_csv(tmp_path):
    comma = tmp_path / "organizations.csv"
    comma.write_text(
        "name,shortName,country,city,organisationID,projectID\n"
        "University College London,UCL,GB,London,999,101\n",
        encoding="utf-8",
    )
    semicolon = tmp_path / "organization.csv"
    semicolon.write_text(
        "name;shortName;country;city;organisationID;projectID\n"
        "University College London;UCL;GB;London;999;101\n",
        encoding="utf-8",
    )

    comma_rows = load_cordis_organizations(comma)
    semicolon_rows = load_cordis_organizations(semicolon)

    assert comma_rows == semicolon_rows
    assert comma_rows[0]["organisationID"] == "999"


def test_cordis_loader_drops_vat_and_contact_fields(tmp_path):
    source = tmp_path / "organizations.csv"
    source.write_text(
        "name,country,organisationID,vatNumber,street,postCode,contactForm\n"
        "University College London,GB,999,GB123456789,Gower Street,WC1E 6BT,https://example.org/contact\n",
        encoding="utf-8",
    )

    rows = load_cordis_organizations(source)

    assert rows[0]["name"] == "University College London"
    for field in ("vatNumber", "street", "postCode", "contactForm"):
        assert field not in rows[0]


def test_cordis_reconciliation_preserves_projects_and_writes_unique_evidence(matcher, tmp_path):
    rows = [
        {
            "name": "University College London",
            "shortName": "UCL",
            "country": "GB",
            "city": "London",
            "activityType": "HES",
            "organisationID": "999",
            "projectID": "101",
            "projectAcronym": "ONE",
        },
        {
            "name": "University College London",
            "shortName": "UCL",
            "country": "GB",
            "city": "London",
            "activityType": "HES",
            "organisationID": "999",
            "projectID": "102",
            "projectAcronym": "TWO",
        },
        {
            "name": "Fictional Moon Research Institute",
            "country": "US",
            "city": "Houston",
            "organisationID": "000",
            "projectID": "103",
        },
    ]

    run = reconcile_cordis_rows(matcher, rows)
    csv_path = tmp_path / "reconciled.csv"
    evidence_path = tmp_path / "evidence.jsonl"
    write_cordis_outputs(run, csv_path, evidence_path)

    with csv_path.open(encoding="utf-8", newline="") as handle:
        output = list(csv.DictReader(handle))
    evidence = [json.loads(line) for line in evidence_path.read_text().splitlines()]

    assert [row["projectID"] for row in output] == ["101", "102", "103"]
    assert output[0]["ror_id"] == "https://ror.org/02jx3x895"
    assert output[0]["match_status"] == "matched"
    assert output[2]["match_status"] in {"abstained", "no_candidates"}
    assert len(evidence) == 2
    assert evidence[0]["source"]["organisationID"] == "999"
    assert evidence[0]["candidates"][0]["features"][0]["id"] == "name_fuzzy"


def test_reconcile_cli_runs_file_to_file(fixture_store, monkeypatch, tmp_path):
    source = tmp_path / "cordis.csv"
    source.write_text(
        "name;country;city;organisationID;projectID\nUniversity College London;GB;London;999;101\n",
        encoding="utf-8",
    )
    output = tmp_path / "reconciled.csv"
    evidence = tmp_path / "evidence.jsonl"
    monkeypatch.setattr(
        "sys.argv",
        [
            "ror-reconcile-cordis",
            "--db",
            str(fixture_store.db_path),
            "--input",
            str(source),
            "--output",
            str(output),
            "--evidence-output",
            str(evidence),
        ],
    )

    reconcile_main()

    assert "https://ror.org/02jx3x895" in output.read_text(encoding="utf-8")
    assert evidence.read_text(encoding="utf-8").count("\n") == 1


def test_cordis_cohort_keeps_all_projects_for_selected_organizations():
    rows = [
        {"organisationID": "1", "name": "One", "country": "GB", "projectID": "101"},
        {"organisationID": "1", "name": "One", "country": "GB", "projectID": "102"},
        {"organisationID": "2", "name": "Two", "country": "DE", "projectID": "103"},
        {"organisationID": "3", "name": "Three", "country": "FR", "projectID": "104"},
    ]

    cohort = select_cordis_cohort(rows, organization_limit=3, seed=42)

    assert len(cohort) == 4
    assert [row["projectID"] for row in cohort if row["organisationID"] == "1"] == [
        "101",
        "102",
    ]
