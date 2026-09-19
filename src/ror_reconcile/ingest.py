from __future__ import annotations

import csv
import io
import json
import zipfile
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from ror_reconcile.models import IndexedName, OrgLocation, OrgRecord
from ror_reconcile.normalize import normalize_name


def load_ror_records(path: Path | str) -> list[OrgRecord]:
    payload = _load_json_payload(Path(path))
    raw_records = _extract_records(payload)
    return [record for record in (_parse_ror_record(raw) for raw in raw_records) if record]


# Drop contact and address fields that are unnecessary for matching and analysis.
_CORDIS_EXCLUDED_FIELDS = frozenset({"vatNumber", "street", "postCode", "contactForm"})


def load_cordis_organizations(
    path: Path | str,
    *,
    limit: int | None = None,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with _open_cordis_organization_csv(Path(path)) as handle:
        sample = handle.read(8192)
        handle.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
            delimiter = dialect.delimiter
        except csv.Error:
            delimiter = ";" if sample.count(";") >= sample.count(",") else ","
        reader = csv.DictReader(handle, delimiter=delimiter)
        for row in reader:
            rows.append(
                {
                    key: (value or "").strip()
                    for key, value in row.items()
                    if key not in _CORDIS_EXCLUDED_FIELDS
                }
            )
            if limit and len(rows) >= limit:
                break
    return rows


def _load_json_payload(path: Path) -> Any:
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            json_names = [
                name
                for name in archive.namelist()
                if name.lower().endswith(".json") and not name.endswith("/")
            ]
            if not json_names:
                raise ValueError(f"no JSON file found in {path}")
            json_name = sorted(json_names, key=lambda name: ("/" in name, len(name)))[0]
            with archive.open(json_name) as handle:
                return json.load(handle)
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _extract_records(payload: Any) -> Iterable[dict[str, Any]]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("items", "records", "organizations", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    raise ValueError("could not find a list of ROR records in JSON payload")


def _parse_ror_record(record: dict[str, Any]) -> OrgRecord | None:
    ror_id = str(record.get("id") or record.get("ror_id") or "").strip()
    if not ror_id:
        return None

    names = _record_names(record)
    if not names:
        return None

    display_name = _display_name(record, names)
    locations = _locations(record)
    primary_location = locations[0] if locations else OrgLocation()

    indexed = _indexed_names(names)
    return OrgRecord(
        ror_id=ror_id,
        name=display_name,
        country=primary_location.country,
        country_code=primary_location.country_code,
        city=primary_location.city,
        status=_clean(record.get("status")),
        types=tuple(_clean(value) for value in record.get("types", []) if _clean(value)),
        names=tuple(indexed),
        locations=locations,
    )


def _record_names(record: dict[str, Any]) -> list[tuple[str, str]]:
    names: list[tuple[str, str]] = []

    for entry in record.get("names") or []:
        if not isinstance(entry, dict):
            continue
        value = _clean(entry.get("value") or entry.get("name"))
        if not value:
            continue
        types = entry.get("types") or []
        kind = _first_known_name_type(types)
        names.append((value, kind))

    legacy_name = _clean(record.get("name"))
    if legacy_name:
        names.append((legacy_name, "ror_display"))

    for value in record.get("aliases") or []:
        cleaned = _clean(value)
        if cleaned:
            names.append((cleaned, "alias"))

    for value in record.get("acronyms") or []:
        cleaned = _clean(value)
        if cleaned:
            names.append((cleaned, "acronym"))

    for entry in record.get("labels") or []:
        if isinstance(entry, dict):
            cleaned = _clean(entry.get("label") or entry.get("value"))
        else:
            cleaned = _clean(entry)
        if cleaned:
            names.append((cleaned, "label"))

    return names


def _first_known_name_type(types: Iterable[str]) -> str:
    priority = ("ror_display", "label", "alias", "acronym")
    seen = [str(value) for value in types]
    for name_type in priority:
        if name_type in seen:
            return name_type
    return seen[0] if seen else "name"


def _display_name(record: dict[str, Any], names: list[tuple[str, str]]) -> str:
    legacy_name = _clean(record.get("name"))
    if legacy_name:
        return legacy_name
    for value, kind in names:
        if kind == "ror_display":
            return value
    for value, kind in names:
        if kind == "label":
            return value
    return names[0][0]


def _indexed_names(names: list[tuple[str, str]]) -> Iterator[IndexedName]:
    seen: set[tuple[str, str]] = set()
    for value, kind in names:
        norm = normalize_name(value)
        if not norm:
            continue
        key = (norm, kind)
        if key in seen:
            continue
        seen.add(key)
        yield IndexedName(value=value, norm=norm, kind=kind)


def _locations(record: dict[str, Any]) -> tuple[OrgLocation, ...]:
    locations: list[OrgLocation] = []
    for location in record.get("locations") or []:
        details = location.get("geonames_details") or {}
        country = _clean(details.get("country_name") or location.get("country_name"))
        country_code = _clean(details.get("country_code") or location.get("country_code"))
        city = _clean(details.get("name") or location.get("city"))
        if country or country_code or city:
            locations.append(OrgLocation(country=country, country_code=country_code, city=city))

    for address in record.get("addresses") or []:
        country_blob = address.get("country") or {}
        country = _clean(country_blob.get("country_name") or address.get("country_name"))
        country_code = _clean(country_blob.get("country_code") or address.get("country_code"))
        city = _clean(address.get("city"))
        if country or country_code or city:
            locations.append(OrgLocation(country=country, country_code=country_code, city=city))

    if locations:
        return _dedupe_locations(locations)

    country_blob = record.get("country") or {}
    if isinstance(country_blob, dict):
        country = _clean(country_blob.get("country_name") or country_blob.get("name"))
        country_code = _clean(country_blob.get("country_code") or country_blob.get("code"))
    else:
        country = _clean(country_blob)
        country_code = None
    if country or country_code:
        return (OrgLocation(country=country, country_code=country_code),)
    return ()


def _dedupe_locations(locations: list[OrgLocation]) -> tuple[OrgLocation, ...]:
    seen: set[tuple[str | None, str | None, str | None]] = set()
    unique: list[OrgLocation] = []
    for location in locations:
        key = (location.country, location.country_code, location.city)
        if key not in seen:
            seen.add(key)
            unique.append(location)
    return tuple(unique)


def _open_cordis_organization_csv(path: Path):
    if path.suffix.lower() == ".zip":
        archive = zipfile.ZipFile(path)
        name = next(
            (
                member
                for member in archive.namelist()
                if member.lower().endswith("organization.csv")
            ),
            None,
        )
        if not name:
            archive.close()
            raise ValueError(f"no organization.csv found in {path}")
        return _ZipTextHandle(archive, name)
    return path.open(encoding="utf-8-sig", newline="")


class _ZipTextHandle:
    def __init__(self, archive: zipfile.ZipFile, name: str):
        self.archive = archive
        self.binary = archive.open(name)
        self.text = io.TextIOWrapper(self.binary, encoding="utf-8-sig", newline="")

    def __enter__(self):
        return self.text

    def __exit__(self, exc_type, exc, tb):
        self.text.close()
        self.binary.close()
        self.archive.close()


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None
