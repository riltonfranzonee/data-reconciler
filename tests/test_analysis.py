from ror_reconcile.analysis import analyse_combined_rows


def test_analysis_counts_projects_without_duplicating_openalex_totals():
    rows = [
        {
            "ror_id": "https://ror.org/ucl",
            "ror_name": "University College London",
            "country": "GB",
            "activityType": "HES",
            "projectID": "101",
            "ecContribution": "10",
            "openalex_status": "joined",
            "openalex_works_count_period": "12",
            "openalex_cited_by_count_period": "50",
        },
        {
            "ror_id": "https://ror.org/ucl",
            "ror_name": "University College London",
            "country": "GB",
            "activityType": "HES",
            "projectID": "102",
            "ecContribution": "20",
            "openalex_status": "joined",
            "openalex_works_count_period": "12",
            "openalex_cited_by_count_period": "50",
        },
        {
            "ror_id": "https://ror.org/other",
            "ror_name": "Other University",
            "country": "DE",
            "activityType": "HES",
            "projectID": "103",
            "ecContribution": "5",
            "openalex_status": "joined",
            "openalex_works_count_period": "3",
            "openalex_cited_by_count_period": "7",
        },
        {"ror_id": "", "projectID": "104", "openalex_status": "not_matched"},
    ]

    run = analyse_combined_rows(rows)

    ucl = next(row for row in run.organizations if row["ror_id"] == "https://ror.org/ucl")
    assert ucl["cordis_project_count"] == 2
    assert ucl["cordis_ec_contribution"] == 30.0
    assert ucl["openalex_works_count_period"] == 12
    assert run.summary["joined_organizations"] == 2
    assert run.summary["unmatched_source_rows"] == 1
    assert run.summary["project_count_works_pearson_r"] == 1.0
    assert run.groups[0]["activity_type"] == "HES"
