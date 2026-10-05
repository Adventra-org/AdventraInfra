import csv
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from .parser import DirectoryError, SearchPage, search_url


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.directory = directory
        self.db = sqlite3.connect(str(directory / "checkpoint.sqlite3"))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS scopes (
                field_id TEXT PRIMARY KEY,
                next_url TEXT,
                total INTEGER,
                pages INTEGER NOT NULL DEFAULT 0,
                listing_done INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS listings (
                field_id TEXT NOT NULL REFERENCES scopes(field_id),
                entity_id TEXT NOT NULL,
                summary TEXT NOT NULL,
                PRIMARY KEY (field_id, entity_id)
            );
            CREATE TABLE IF NOT EXISTS entities (
                entity_id TEXT PRIMARY KEY,
                record TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )

    def close(self) -> None:
        self.db.close()

    def add_scope(self, field_id: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO scopes(field_id, next_url) VALUES (?, ?)",
                (field_id, search_url(field_id)),
            )

    def scope(self, field_id: str) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM scopes WHERE field_id = ?", (field_id,)).fetchone()
        if row is None:
            raise DirectoryError("No checkpoint for scope " + field_id)
        return row

    def save_page(self, field_id: str, page: SearchPage) -> None:
        scope = self.scope(field_id)
        discovered = self.db.execute(
            "SELECT COUNT(*) FROM listings WHERE field_id = ?", (field_id,)
        ).fetchone()[0]
        if scope["total"] is not None and page.total != scope["total"]:
            raise DirectoryError(
                "Directory total changed during collection. Keep this checkpoint and start "
                "a new output directory for a fresh snapshot."
            )
        if page.total and page.start != discovered + 1:
            raise DirectoryError("Page range overlaps or skips the saved result set.")
        with self.db:
            for record in page.records:
                self.db.execute(
                    "INSERT INTO listings(field_id, entity_id, summary) VALUES (?, ?, ?)",
                    (field_id, record["source_entity_id"], json.dumps(record, ensure_ascii=False)),
                )
            if page.next_url is None and discovered + len(page.records) != page.total:
                raise DirectoryError("Final unique congregation count differs from directory total.")
            self.db.execute(
                """UPDATE scopes SET next_url = ?, total = ?, pages = pages + 1,
                   listing_done = ? WHERE field_id = ?""",
                (page.next_url, page.total, int(page.next_url is None), field_id),
            )

    def pending(self, field_id: str) -> Optional[Dict[str, Any]]:
        row = self.db.execute(
            """SELECT l.summary FROM listings l LEFT JOIN entities e ON l.entity_id = e.entity_id
               WHERE l.field_id = ? AND e.entity_id IS NULL ORDER BY l.rowid LIMIT 1""",
            (field_id,),
        ).fetchone()
        return json.loads(row["summary"]) if row else None

    def save_detail(self, record: Dict[str, Any]) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO entities(entity_id, record) VALUES (?, ?)",
                (record["source_entity_id"], json.dumps(record, ensure_ascii=False)),
            )

    def last_request(self) -> float:
        row = self.db.execute("SELECT value FROM metadata WHERE key = 'last_request'").fetchone()
        return float(row[0]) if row else 0

    def set_last_request(self, value: float) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES ('last_request', ?)",
                (str(value),),
            )

    def status(self, field_id: str) -> Dict[str, Any]:
        scope = self.scope(field_id)
        counts = self.db.execute(
            """SELECT COUNT(*) AS discovered, COUNT(e.entity_id) AS completed
               FROM listings l LEFT JOIN entities e ON l.entity_id = e.entity_id
               WHERE l.field_id = ?""",
            (field_id,),
        ).fetchone()
        records = self.db.execute(
            """SELECT e.record FROM entities e JOIN listings l ON e.entity_id = l.entity_id
               WHERE l.field_id = ?""",
            (field_id,),
        )
        missing_coordinates = sum(json.loads(row[0])["lat"] is None for row in records)
        return {
            "field_id": field_id,
            "advertised_total": scope["total"],
            "pages_saved": scope["pages"],
            "listing_complete": bool(scope["listing_done"]),
            "discovered": counts["discovered"],
            "details_saved": counts["completed"],
            "details_pending": counts["discovered"] - counts["completed"],
            "missing_coordinates": missing_coordinates,
            "complete": bool(scope["listing_done"]) and counts["completed"] == scope["total"],
        }

    def export(self, field_id: str) -> Dict[str, Any]:
        status = self.status(field_id)
        status["exported_at"] = timestamp()
        rows = self.db.execute(
            """SELECT l.summary, e.record FROM listings l
               LEFT JOIN entities e ON l.entity_id = e.entity_id
               WHERE l.field_id = ? ORDER BY l.entity_id""",
            (field_id,),
        )
        records = []
        for row in rows:
            record = json.loads(row["record"] or row["summary"])
            record["detail_status"] = "complete" if row["record"] else "pending"
            records.append(record)
        paths = {
            "json": self.directory / (field_id + ".json"),
            "csv": self.directory / (field_id + ".csv"),
            "status": self.directory / (field_id + ".status.json"),
        }
        self._json(paths["json"], {"metadata": status, "congregations": records})
        self._json(paths["status"], status)
        columns = [
            "church_id", "source_entity_id", "name", "congregation_type", "detail_status",
            "city", "state_province", "country", "address_lines", "mailing_address_lines",
            "lat", "lon", "coordinate_source", "website", "phone", "services", "language",
            "members", "conference", "conference_id", "union", "union_id", "division",
            "division_id", "source_url", "fetched_at",
        ]
        temporary = paths["csv"].with_suffix(".csv.tmp")
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for record in records:
                flattened = {
                    key: json.dumps(value, ensure_ascii=False) if isinstance(value, list) else value
                    for key, value in record.items()
                }
                # Prevent directory text from becoming a spreadsheet formula.
                for key, value in flattened.items():
                    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                        flattened[key] = "'" + value
                writer.writerow(flattened)
        os.replace(temporary, paths["csv"])
        return status

    @staticmethod
    def _json(path: Path, value: Any) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
        os.replace(temporary, path)
