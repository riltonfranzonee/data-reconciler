from ror_reconcile.config import MatchConfig
from ror_reconcile.matcher import RorMatcher
from ror_reconcile.models import IndexedName, MatchQuery, OrgRecord
from ror_reconcile.normalize import normalize_name
from ror_reconcile.store import RorStore


def test_exact_acronym_match(matcher):
    result = matcher.match_one(MatchQuery(query="UCL", country="GB"))
    assert result.candidates[0].org.ror_id == "https://ror.org/02jx3x895"
    assert result.candidates[0].match is True
    assert result.candidates[0].exact_match is True


# An exact name and matching country remain sufficient when a fuzzy competitor
# reduces the score margin.
def test_exact_country_match_survives_fuzzy_competitor(matcher):
    result = matcher.match_one(MatchQuery(query="University College London", country="GB"))

    assert result.candidates[0].org.ror_id == "https://ror.org/02jx3x895"
    assert result.candidates[0].match is True
    assert result.candidates[0].exact_match is True


def test_fuzzy_alias_match(matcher):
    result = matcher.match_one(MatchQuery(query="Max Planck Gesellschaft", country="DE"))
    assert result.candidates[0].org.ror_id == "https://ror.org/01hhn8329"
    assert result.candidates[0].name_score >= 0.82


def test_country_property_disambiguates_same_name(matcher):
    gb = matcher.match_one(MatchQuery(query="Institute for Advanced Studies", country="GB"))
    us = matcher.match_one(MatchQuery(query="Institute for Advanced Studies", country="US"))

    assert gb.candidates[0].org.ror_id == "https://ror.example/gb-advanced"
    assert us.candidates[0].org.ror_id == "https://ror.example/us-advanced"


def test_bad_query_abstains(matcher):
    result = matcher.match_one(MatchQuery(query="Fictional Moon Research Institute", country="US"))
    assert not result.candidates or result.candidates[0].match is False

    # Stopwords alone provide no name evidence for a fuzzy match.
    contentless = matcher.match_one(MatchQuery(query="de la", country="GB"))
    assert not contentless.candidates or contentless.candidates[0].match is False


def test_single_generic_token_does_not_fuzzy_auto_match(tmp_path):
    store = RorStore(tmp_path / "generic.sqlite")
    try:
        store.replace_records(
            [
                OrgRecord(
                    ror_id="https://ror.example/bm-science",
                    name="BM-Science",
                    country="Finland",
                    country_code="FI",
                    city="Helsinki",
                    types=("Company",),
                    names=(
                        IndexedName(
                            value="BM-Science",
                            norm=normalize_name("BM-Science"),
                            kind="ror_display",
                        ),
                    ),
                )
            ]
        )

        result = RorMatcher(store).match_one(MatchQuery(query="science", country="FI"))

        assert result.candidates[0].org.ror_id == "https://ror.example/bm-science"
        assert result.candidates[0].match is False
    finally:
        store.close()


def test_ambiguous_exact_acronym_in_same_country_abstains(tmp_path):
    store = RorStore(tmp_path / "ambiguous.sqlite")
    try:
        records = []
        for suffix, name in (("one", "Coastal Computing Centre"), ("two", "City Care Centre")):
            records.append(
                OrgRecord(
                    ror_id=f"https://ror.example/{suffix}",
                    name=name,
                    country="United States",
                    country_code="US",
                    city=None,
                    types=("Other",),
                    names=(
                        IndexedName(
                            value=name,
                            norm=normalize_name(name),
                            kind="ror_display",
                        ),
                        IndexedName(value="CCC", norm=normalize_name("CCC"), kind="acronym"),
                    ),
                )
            )
        store.replace_records(records)

        result = RorMatcher(store).match_one(MatchQuery(query="CCC", country="US"))

        assert len(result.candidates) == 2
        assert result.candidates[0].exact_match is True
        assert result.candidates[0].margin == 0.0
        assert result.candidates[0].match is False
    finally:
        store.close()


def test_trigram_fallback_recovers_in_token_typo(tmp_path):
    store = RorStore(tmp_path / "trigram.sqlite")
    try:
        store.replace_records(
            [
                OrgRecord(
                    ror_id="https://ror.example/distractor",
                    name="College",
                    country="United Kingdom",
                    country_code="GB",
                    names=(
                        IndexedName(
                            value="College",
                            norm=normalize_name("College"),
                            kind="ror_display",
                        ),
                    ),
                ),
                OrgRecord(
                    ror_id="https://ror.org/02jx3x895",
                    name="University College London",
                    country="United Kingdom",
                    country_code="GB",
                    city="London",
                    names=(
                        IndexedName(
                            value="University College London",
                            norm=normalize_name("University College London"),
                            kind="ror_display",
                        ),
                    ),
                ),
            ]
        )
        matcher = RorMatcher(store, MatchConfig(shortlist_limit=1))

        result = matcher.match_one(
            MatchQuery(query="Universty College Londn", country="GB", city="London")
        )

        assert result.candidates[0].org.ror_id == "https://ror.org/02jx3x895"
        assert result.candidates[0].match is True
    finally:
        store.close()


# The shared word "college" is insufficient to link the misspelled UCL name to Wye College.
def test_shared_boilerplate_token_does_not_clear_lexical_gate(tmp_path):
    store = RorStore(tmp_path / "wye.sqlite")
    try:
        store.replace_records(
            [
                OrgRecord(
                    ror_id="https://ror.example/wye-college",
                    name="Wye College",
                    country="United Kingdom",
                    country_code="GB",
                    city="Wye",
                    types=("Education",),
                    names=(
                        IndexedName(
                            value="Wye College",
                            norm=normalize_name("Wye College"),
                            kind="ror_display",
                        ),
                        IndexedName(
                            value="The College of St Gregory and St Martin at Wye",
                            norm=normalize_name("The College of St Gregory and St Martin at Wye"),
                            kind="alias",
                        ),
                    ),
                )
            ]
        )
        result = RorMatcher(store).match_one(
            MatchQuery(query="Universty College Londn", country="GB")
        )

        assert result.candidates[0].org.ror_id == "https://ror.example/wye-college"
        assert result.candidates[0].match is False
        assert result.candidates[0].name_score < RorMatcher(store).config.lexical_gate
    finally:
        store.close()
