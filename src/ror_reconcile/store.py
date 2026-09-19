from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable
from pathlib import Path

from ror_reconcile.models import IndexedName, OrgLocation, OrgRecord
from ror_reconcile.normalize import fts_tokens


class RorStore:
    """Query ROR records and retrieve candidates from SQLite name indexes."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    def init_schema(self, *, reset: bool = False) -> None:
        with self._lock:
            self._init_schema_unlocked(reset=reset)

    def _init_schema_unlocked(self, *, reset: bool = False) -> None:
        if reset:
            self.conn.executescript(
                """
                DROP TABLE IF EXISTS name_trigram_fts;
                DROP TABLE IF EXISTS name_fts;
                DROP TABLE IF EXISTS locations;
                DROP TABLE IF EXISTS names;
                DROP TABLE IF EXISTS organizations;
                """
            )

        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS organizations (
                ror_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                country TEXT,
                country_code TEXT,
                city TEXT,
                status TEXT,
                types_json TEXT NOT NULL DEFAULT '[]'
            );

            CREATE TABLE IF NOT EXISTS names (
                id INTEGER PRIMARY KEY,
                ror_id TEXT NOT NULL REFERENCES organizations(ror_id) ON DELETE CASCADE,
                value TEXT NOT NULL,
                norm TEXT NOT NULL,
                kind TEXT NOT NULL,
                UNIQUE (ror_id, norm, kind)
            );

            CREATE TABLE IF NOT EXISTS locations (
                id INTEGER PRIMARY KEY,
                ror_id TEXT NOT NULL REFERENCES organizations(ror_id) ON DELETE CASCADE,
                country TEXT,
                country_code TEXT,
                city TEXT,
                UNIQUE (ror_id, country, country_code, city)
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS name_fts USING fts5(
                norm,
                value UNINDEXED,
                ror_id UNINDEXED,
                kind UNINDEXED,
                tokenize = 'unicode61 remove_diacritics 2'
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS name_trigram_fts USING fts5(
                norm,
                value UNINDEXED,
                ror_id UNINDEXED,
                kind UNINDEXED,
                tokenize = 'trigram'
            );

            CREATE INDEX IF NOT EXISTS idx_names_norm ON names(norm);
            CREATE INDEX IF NOT EXISTS idx_names_ror_id ON names(ror_id);
            CREATE INDEX IF NOT EXISTS idx_org_country ON organizations(country_code, country);
            CREATE INDEX IF NOT EXISTS idx_locations_ror_id ON locations(ror_id);
            """
        )
        self.conn.commit()

    def replace_records(self, records: Iterable[OrgRecord]) -> int:
        with self._lock:
            self._init_schema_unlocked(reset=True)
            count = 0
            with self.conn:
                for org in records:
                    self.conn.execute(
                        """
                        INSERT INTO organizations
                          (ror_id, name, country, country_code, city, status, types_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            org.ror_id,
                            org.name,
                            org.country,
                            org.country_code,
                            org.city,
                            org.status,
                            json.dumps(list(org.types)),
                        ),
                    )
                    locations = org.locations or (
                        OrgLocation(
                            country=org.country,
                            country_code=org.country_code,
                            city=org.city,
                        ),
                    )
                    for location in locations:
                        if not (location.country or location.country_code or location.city):
                            continue
                        self.conn.execute(
                            """
                            INSERT OR IGNORE INTO locations (ror_id, country, country_code, city)
                            VALUES (?, ?, ?, ?)
                            """,
                            (
                                org.ror_id,
                                location.country,
                                location.country_code,
                                location.city,
                            ),
                        )
                    for indexed_name in org.names:
                        self.conn.execute(
                            """
                            INSERT OR IGNORE INTO names (ror_id, value, norm, kind)
                            VALUES (?, ?, ?, ?)
                            """,
                            (org.ror_id, indexed_name.value, indexed_name.norm, indexed_name.kind),
                        )
                        self.conn.execute(
                            """
                            INSERT INTO name_fts (norm, value, ror_id, kind)
                            VALUES (?, ?, ?, ?)
                            """,
                            (indexed_name.norm, indexed_name.value, org.ror_id, indexed_name.kind),
                        )
                        self.conn.execute(
                            """
                            INSERT INTO name_trigram_fts (norm, value, ror_id, kind)
                            VALUES (?, ?, ?, ?)
                            """,
                            (indexed_name.norm, indexed_name.value, org.ror_id, indexed_name.kind),
                        )
                    count += 1
            return count

    # Excludes records ROR has marked inactive or withdrawn; unknown status stays matchable.
    _ACTIVE_FILTER = "(o.status = 'active' OR o.status IS NULL)"

    def find_exact(
        self, norm_name: str, *, limit: int, active_only: bool = False
    ) -> list[OrgRecord]:
        status_clause = f"AND {self._ACTIVE_FILTER}" if active_only else ""
        with self._lock:
            rows = self.conn.execute(
                f"""
                SELECT DISTINCT n.ror_id
                FROM names n
                JOIN organizations o ON o.ror_id = n.ror_id
                WHERE n.norm = ? {status_clause}
                ORDER BY n.ror_id
                LIMIT ?
                """,
                (norm_name, limit),
            ).fetchall()
            return self._records_for_ids([row["ror_id"] for row in rows])

    def shortlist(self, query: str, *, limit: int, active_only: bool = False) -> list[OrgRecord]:
        expression = self._fts_expression(query)
        if not expression:
            return []
        status_clause = f"WHERE {self._ACTIVE_FILTER}" if active_only else ""
        with self._lock:
            rows = self.conn.execute(
                f"""
                SELECT f.ror_id, MIN(f.rank) AS best_rank
                FROM (
                    SELECT ror_id, bm25(name_fts) AS rank
                    FROM name_fts
                    WHERE name_fts MATCH ?
                    ORDER BY bm25(name_fts) ASC
                    LIMIT ?
                ) AS f
                JOIN organizations o ON o.ror_id = f.ror_id
                {status_clause}
                GROUP BY f.ror_id
                ORDER BY best_rank ASC, f.ror_id ASC
                LIMIT ?
                """,
                (expression, limit * 5, limit),
            ).fetchall()
            return self._records_for_ids([row["ror_id"] for row in rows])

    def shortlist_trigram(
        self, query: str, *, limit: int, active_only: bool = False
    ) -> list[OrgRecord]:
        expression = self._trigram_expression(query)
        if not expression:
            return []
        status_clause = f"WHERE {self._ACTIVE_FILTER}" if active_only else ""
        with self._lock:
            try:
                rows = self.conn.execute(
                    f"""
                    SELECT f.ror_id, MIN(f.rank) AS best_rank
                    FROM (
                        SELECT ror_id, bm25(name_trigram_fts) AS rank
                        FROM name_trigram_fts
                        WHERE name_trigram_fts MATCH ?
                        ORDER BY bm25(name_trigram_fts) ASC
                        LIMIT ?
                    ) AS f
                    JOIN organizations o ON o.ror_id = f.ror_id
                    {status_clause}
                    GROUP BY f.ror_id
                    ORDER BY best_rank ASC, f.ror_id ASC
                    LIMIT ?
                    """,
                    (expression, limit * 5, limit),
                ).fetchall()
            except sqlite3.OperationalError as error:
                # Databases built before the trigram index was introduced remain
                # readable; re-ingestion upgrades them and enables this fallback.
                if "name_trigram_fts" in str(error):
                    return []
                raise
            return self._records_for_ids([row["ror_id"] for row in rows])

    def get_org(self, ror_id: str) -> OrgRecord | None:
        with self._lock:
            records = self._records_for_ids([ror_id])
            return records[0] if records else None

    def count_orgs(self) -> int:
        with self._lock:
            row = self.conn.execute("SELECT COUNT(*) AS n FROM organizations").fetchone()
            return int(row["n"])

    def _records_for_ids(self, ror_ids: list[str]) -> list[OrgRecord]:
        if not ror_ids:
            return []
        placeholders = ",".join("?" for _ in ror_ids)
        org_rows = self.conn.execute(
            f"""
            SELECT ror_id, name, country, country_code, city, status, types_json
            FROM organizations
            WHERE ror_id IN ({placeholders})
            """,
            ror_ids,
        ).fetchall()
        name_rows = self.conn.execute(
            f"""
            SELECT ror_id, value, norm, kind
            FROM names
            WHERE ror_id IN ({placeholders})
            ORDER BY ror_id, kind, value
            """,
            ror_ids,
        ).fetchall()
        try:
            location_rows = self.conn.execute(
                f"""
                SELECT ror_id, country, country_code, city
                FROM locations
                WHERE ror_id IN ({placeholders})
                ORDER BY ror_id, id
                """,
                ror_ids,
            ).fetchall()
        except sqlite3.OperationalError as error:
            if "locations" not in str(error):
                raise
            location_rows = []

        names_by_id: dict[str, list[IndexedName]] = {ror_id: [] for ror_id in ror_ids}
        for row in name_rows:
            names_by_id[row["ror_id"]].append(
                IndexedName(value=row["value"], norm=row["norm"], kind=row["kind"])
            )

        locations_by_id: dict[str, list[OrgLocation]] = {ror_id: [] for ror_id in ror_ids}
        for row in location_rows:
            locations_by_id[row["ror_id"]].append(
                OrgLocation(
                    country=row["country"],
                    country_code=row["country_code"],
                    city=row["city"],
                )
            )

        by_id: dict[str, OrgRecord] = {}
        for row in org_rows:
            by_id[row["ror_id"]] = OrgRecord(
                ror_id=row["ror_id"],
                name=row["name"],
                country=row["country"],
                country_code=row["country_code"],
                city=row["city"],
                status=row["status"],
                types=tuple(json.loads(row["types_json"] or "[]")),
                names=tuple(names_by_id.get(row["ror_id"], [])),
                locations=tuple(locations_by_id.get(row["ror_id"], [])),
            )
        return [by_id[ror_id] for ror_id in ror_ids if ror_id in by_id]

    @staticmethod
    def _fts_expression(query: str) -> str:
        tokens = fts_tokens(query)
        if not tokens:
            return ""
        return " OR ".join(f"{token}*" for token in tokens[:8])

    @staticmethod
    def _trigram_expression(query: str) -> str:
        compact_trigrams = {
            query[index : index + 3]
            for index in range(max(0, len(query) - 2))
            if " " not in query[index : index + 3]
        }
        return " OR ".join(
            f'"{trigram.replace(chr(34), chr(34) * 2)}"'
            for trigram in sorted(compact_trigrams)[:64]
        )
