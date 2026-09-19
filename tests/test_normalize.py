from ror_reconcile.normalize import fts_tokens, normalize_country, normalize_name


def test_normalize_name_folds_noise_and_legal_suffixes():
    assert normalize_name("Max-Planck-Gesellschaft zur Forderung e.V.") == (
        "max planck gesellschaft zur forderung"
    )
    assert normalize_name("Acme Robotics GmbH") == "acme robotics"
    # Legal-form tokens are stripped only at the end of a name.
    assert normalize_name("Bath Spa University") == "bath spa university"
    assert normalize_name("SAS Institute") == "sas institute"


def test_country_codes_stay_uppercase():
    assert normalize_country("de") == "DE"
    assert normalize_country("United Kingdom") == "united kingdom"
    # CORDIS-style codes map to the ISO codes ROR holds.
    assert normalize_country("UK") == "GB"
    assert normalize_country("EL") == "GR"


def test_fts_tokens_drop_query_operators_and_stopwords():
    assert fts_tokens("University of the Arts OR Design") == ["university", "arts", "design"]
