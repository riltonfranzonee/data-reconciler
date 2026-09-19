from __future__ import annotations

import re
import unicodedata

_PARENS_RE = re.compile(r"\([^)]*\)")
_NON_WORD_RE = re.compile(r"[^a-z0-9]+")
_SPACE_RE = re.compile(r"\s+")

_LEGAL_SUFFIXES = {
    "ag",
    "as",
    "bv",
    "ev",
    "gmbh",
    "inc",
    "kg",
    "ltd",
    "llc",
    "lp",
    "nv",
    "oy",
    "plc",
    "sa",
    "sas",
    "spa",
    "srl",
}

_FTS_STOPWORDS = {
    "and",
    "for",
    "not",
    "of",
    "or",
    "the",
}


def fold_ascii(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    folded = fold_ascii(value).lower()
    folded = _PARENS_RE.sub(" ", folded)
    folded = folded.replace("&", " and ")
    folded = _NON_WORD_RE.sub(" ", folded)
    return _SPACE_RE.sub(" ", folded).strip()


def normalize_name(value: str | None) -> str:
    # Legal-form markers are stripped only from the end of the name: "Bath Spa
    # University" must keep its "spa", while "Acme Robotics GmbH" loses its "gmbh".
    text = normalize_text(value)
    tokens = text.split()
    if len(tokens) >= 2 and tokens[-2:] == ["e", "v"]:
        tokens = tokens[:-2]
    while len(tokens) > 1 and tokens[-1] in _LEGAL_SUFFIXES:
        tokens = tokens[:-1]
    return " ".join(tokens)


def normalize_city(value: str | None) -> str:
    return normalize_text(value)


# CORDIS publishes UK and EL where ISO (and ROR) hold GB and GR.
_COUNTRY_CODE_ALIASES = {"UK": "GB", "EL": "GR"}


def normalize_country(value: str | None) -> str:
    if not value:
        return ""
    stripped = value.strip()
    if len(stripped) == 2 and stripped.isalpha():
        code = stripped.upper()
        return _COUNTRY_CODE_ALIASES.get(code, code)
    return normalize_text(stripped)


def fts_tokens(value: str) -> list[str]:
    return [
        token
        for token in normalize_name(value).split()
        if len(token) >= 2 and token not in _FTS_STOPWORDS
    ]
